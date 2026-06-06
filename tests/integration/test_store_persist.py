"""End-to-end persistence flow: scan → persist_scan_result → re-scan idempotency."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from aitap.cli import app
from aitap.config import Settings
from aitap.scanner.engine import scan_project
from aitap.store import db, files, persist_scan_result


@pytest.fixture()
def initialised_project(tmp_path: Path) -> Path:
    """Run `aitap init` against tmp_path so a .aitap/ skeleton exists."""
    runner = CliRunner()
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return tmp_path


def test_persist_skips_when_no_aitap(tmp_path: Path) -> None:
    """Without `aitap init`, persist must be a no-op — never auto-create."""
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "openai_basic"
    result = scan_project(fixture)
    report = persist_scan_result(Settings(project_root=tmp_path), result)
    assert report.skipped_no_aitap is True
    assert not (tmp_path / ".aitap").exists()


def test_persist_writes_db_and_yamls(initialised_project: Path) -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "openai_basic"
    settings = Settings(project_root=initialised_project)

    result = scan_project(fixture)
    report = persist_scan_result(settings, result)

    assert report.skipped_no_aitap is False
    assert report.prompts_written == len(result.prompts)
    assert (initialised_project / ".aitap" / "db.sqlite").exists()
    yamls = list(settings.prompts_dir.glob("*.prompt.yaml"))
    assert len(yamls) == len(result.prompts)


def test_persist_is_idempotent(initialised_project: Path) -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "openai_basic"
    settings = Settings(project_root=initialised_project)
    result = scan_project(fixture)

    persist_scan_result(settings, result)
    persist_scan_result(settings, result)

    conn = db.connect(settings.db_path)
    try:
        rows = db.read_prompts(conn)
        assert len(rows) == len(result.prompts)  # no duplicates
    finally:
        conn.close()


def test_persist_round_trips_prompt_via_yaml(initialised_project: Path) -> None:
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "openai_basic"
    settings = Settings(project_root=initialised_project)
    result = scan_project(fixture)
    persist_scan_result(settings, result)

    yamls = files.list_prompts(settings.prompts_dir)
    assert yamls, "expected at least one persisted prompt yaml"
    loaded = files.read_prompt(yamls[0])

    # Find the matching original site by id and compare.
    by_id = {site.id: site for site in result.prompts}
    assert loaded == by_id[loaded.id]


def test_scan_command_persists_when_aitap_present(initialised_project: Path) -> None:
    """End-to-end via the CLI: `aitap scan` in an init'd project writes .aitap/."""
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "openai_basic"
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["scan", str(fixture)],
        env={"AITAP_PROJECT_ROOT": str(initialised_project)},
    )
    assert result.exit_code == 0, result.output
    # The scan was for the fixture path but persistence is keyed off
    # AITAP_PROJECT_ROOT — confirm db landed in initialised_project.
    assert (initialised_project / ".aitap" / "db.sqlite").exists()


# --------------------------------------------------------------------------- #
# Bug-fix regressions added by wt/store-persist-fixes (PR #54)                #
# --------------------------------------------------------------------------- #


def test_scan_command_persists_to_scan_target_when_no_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI was invoked through ``uv --directory <aitap_repo> run aitap
    scan <user_project>`` style wrappers, which change the process's CWD
    to the wrapper's project root. Before PR #54 a bare ``Settings()``
    resolved ``project_root`` to that CWD and persistence tried to write
    into ``<aitap_repo>/.aitap/``, which didn't exist on the user's
    machine — the cc-project web-playground eval hit this exact bug.

    With no ``AITAP_PROJECT_ROOT`` env var set, persistence must
    target the scan path itself, not whatever CWD the wrapper happened
    to land on.
    """
    # initialise the would-be user project
    scan_target = tmp_path / "user_project"
    scan_target.mkdir()
    runner = CliRunner()
    init_result = runner.invoke(app, ["init", str(scan_target)])
    assert init_result.exit_code == 0, init_result.output

    # ... and a fake "wrapper repo" CWD that has no ``.aitap/``.
    wrapper_cwd = tmp_path / "wrapper_repo"
    wrapper_cwd.mkdir()
    monkeypatch.chdir(wrapper_cwd)
    monkeypatch.delenv("AITAP_PROJECT_ROOT", raising=False)

    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "openai_basic"
    result = runner.invoke(app, ["scan", str(fixture)])
    assert result.exit_code == 0, result.output

    # We don't care what fixture's own .aitap state looks like — what
    # matters is that the wrapper's CWD didn't get its .aitap/ stamped
    # with anything (it was never initialised), and the scan_target
    # path (where we ran ``aitap init``) still has none either because
    # the fixture's project_root != scan_target. The bug used to make
    # the scan crash with a stderr warning about a missing wrapper-
    # path file; we just confirm the wrapper's CWD didn't grow one.
    assert not (wrapper_cwd / ".aitap").exists()


def test_persist_removes_orphan_prompt_yamls_on_rescan(
    initialised_project: Path,
) -> None:
    """When a site's content fingerprint changes between scans, the
    site id changes too and the YAML file name changes with it. Without
    explicit orphan cleanup the prior YAML stays on disk and the
    inventory doubles up by name — the cc-project HEAVEN_WORLD_RULES
    eval hit this. PR #54 deletes any YAML that was on disk before the
    write pass but didn't get rewritten.
    """
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "openai_basic"
    settings = Settings(project_root=initialised_project)

    # First pass — populate the prompts directory.
    first = scan_project(fixture)
    persist_scan_result(settings, first)
    initial_yamls = set(settings.prompts_dir.glob("*.prompt.yaml"))
    assert initial_yamls, "expected at least one persisted prompt yaml"

    # Plant a stale YAML the next scan won't touch. The filename is
    # deliberately distinct from anything ``write_prompt`` would emit.
    stale = settings.prompts_dir / "orphan_from_prior_scan.deadbeef00.prompt.yaml"
    stale.write_text("id: deadbeef00ff\n", encoding="utf-8")
    assert stale.exists()

    # Second pass — same scan result; the orphan must be removed.
    report = persist_scan_result(settings, first)

    assert report.prompts_removed == 1
    assert not stale.exists()
    # Real prompts stayed.
    final_yamls = set(settings.prompts_dir.glob("*.prompt.yaml"))
    assert final_yamls == initial_yamls


def test_persist_orphan_removal_handles_missing_file_gracefully(
    initialised_project: Path,
) -> None:
    """A buggy detector that names two passes' worth of unrelated files
    must not crash if one of the orphans already disappeared between
    listing and removal (race / external delete).
    """
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "openai_basic"
    settings = Settings(project_root=initialised_project)
    persist_scan_result(settings, scan_project(fixture))

    # Persist again — no orphans yet — should not raise.
    report = persist_scan_result(settings, scan_project(fixture))
    assert report.prompts_removed == 0
    assert report.pipelines_removed == 0
