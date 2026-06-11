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


# --------------------------------------------------------------------------- #
# DB orphan cleanup (PR #60) — completes the half-fix PR #54 shipped          #
# --------------------------------------------------------------------------- #
#
# PR #54 cleaned orphan YAML files. PR #60 adds the matching DB-row cleanup
# the server-facing read path actually depends on. Without these tests, a
# regression that drops the DELETE step would only surface through a
# live-browser eval — exactly the bug cc-project hit between PR #58 and
# PR #60.


def test_persist_removes_orphan_db_rows_on_rescan(
    initialised_project: Path,
) -> None:
    """When a prompt site disappears between scans (deleted code, or a
    fingerprint change that makes the same site present under a new
    id), the prior DB row must be deleted. Before PR #60 the row
    stayed forever and ``read_prompts`` kept returning ghosts — the
    cc-project Inventory still listed five stale prompts after the
    deep scan rewrote their fingerprints.
    """
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "openai_basic"
    settings = Settings(project_root=initialised_project)

    # First persist with the full scan.
    first = scan_project(fixture)
    persist_scan_result(settings, first)
    assert first.prompts, "fixture should produce at least one prompt"
    doomed_id = first.prompts[0].id

    # Second persist drops one prompt — simulating either a deleted
    # call site OR a fingerprint shift where the old id is no longer
    # written.
    survivor = first.model_copy(update={"prompts": first.prompts[1:]})
    report = persist_scan_result(settings, survivor)

    # The doomed id is gone from the DB.
    conn = db.connect(settings.db_path)
    try:
        live_ids = {row[0] for row in conn.execute("SELECT id FROM prompts")}
    finally:
        conn.close()
    assert doomed_id not in live_ids
    assert live_ids == {site.id for site in survivor.prompts}

    # And the report calls it out.
    assert report.prompts_removed_from_db == 1


def test_persist_db_orphan_cleanup_cascades_to_prompt_versions(
    initialised_project: Path,
) -> None:
    """``prompt_versions.prompt_id`` has ``ON DELETE CASCADE`` in the
    schema, and :func:`db.connect` sets ``PRAGMA foreign_keys=ON``.
    Pin both together: when an orphan prompt row is deleted, any
    version rows that referenced it must vanish too — otherwise we
    end up with dangling-FK rows the next sqlite migration could
    refuse to touch.
    """
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "openai_basic"
    settings = Settings(project_root=initialised_project)

    first = scan_project(fixture)
    persist_scan_result(settings, first)
    doomed_id = first.prompts[0].id

    # Plant a synthetic prompt_versions row referencing the doomed
    # prompt. Raw INSERT (not history.create_version) so the test pins
    # cascade-on-delete behaviour, not the version-write path. If the
    # schema grows a new NOT NULL column on prompt_versions, this test
    # will fail — that's the intended sentinel.
    conn = db.connect(settings.db_path)
    try:
        with db.transaction(conn):
            conn.execute(
                """
                INSERT INTO prompt_versions (
                    prompt_id, version, template_json, parameters_json,
                    created_by, note
                ) VALUES (?, 1, '[]', '{}', 'iteration', 'planted-for-test')
                """,
                (doomed_id,),
            )
        version_rows_before = conn.execute(
            "SELECT COUNT(*) FROM prompt_versions WHERE prompt_id = ?",
            (doomed_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert version_rows_before == 1, "planted version row should exist"

    # Re-persist without the doomed prompt — cascade should empty the
    # prompt_versions table for that prompt id.
    survivor = first.model_copy(update={"prompts": first.prompts[1:]})
    persist_scan_result(settings, survivor)

    conn = db.connect(settings.db_path)
    try:
        version_rows_after = conn.execute(
            "SELECT COUNT(*) FROM prompt_versions WHERE prompt_id = ?",
            (doomed_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert version_rows_after == 0


def test_persist_db_orphan_cleanup_reports_zero_when_no_orphans(
    initialised_project: Path,
) -> None:
    """Idempotency: re-persisting the same scan yields zero DB orphans.
    The bug PR #60 fixes is asymmetric — adding DELETE only matters
    when something IS orphaned. Pin the no-op path too so a careless
    refactor doesn't accidentally start deleting valid rows.
    """
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "openai_basic"
    settings = Settings(project_root=initialised_project)
    result = scan_project(fixture)

    persist_scan_result(settings, result)
    report = persist_scan_result(settings, result)

    assert report.prompts_removed_from_db == 0
    assert report.pipelines_removed_from_db == 0


def test_persist_yaml_orphan_count_unchanged_by_db_cleanup_addition(
    initialised_project: Path,
) -> None:
    """Belt-and-braces backward-compat check for PR #54's behaviour.
    PR #60 adds ``prompts_removed_from_db`` as a new field; the
    existing ``prompts_removed`` (the YAML cleanup count) must keep
    its prior semantics. A regression that mixed the counters would
    break downstream consumers (CLI summary line, integration tests
    in ``test_store_persist`` above).
    """
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "openai_basic"
    settings = Settings(project_root=initialised_project)

    first = scan_project(fixture)
    persist_scan_result(settings, first)

    # Plant a YAML with an id no scan would produce. No DB row for it.
    stale = settings.prompts_dir / "yaml_only_orphan.cafef00d00.prompt.yaml"
    stale.write_text("id: cafef00d00ff\n", encoding="utf-8")

    report = persist_scan_result(settings, first)

    # YAML count picks up the stale file (PR #54 semantics).
    assert report.prompts_removed == 1
    # DB count is zero — the planted file had no matching row.
    assert report.prompts_removed_from_db == 0
    assert not stale.exists()


def test_persist_db_orphan_cleanup_cascades_to_iterations(
    initialised_project: Path,
) -> None:
    """Parallel to the ``prompt_versions`` cascade test above —
    ``iterations.prompt_id`` also carries ``ON DELETE CASCADE`` and
    must vanish with its parent prompt. Without this sibling
    assertion, a future schema rename that breaks the iterations FK
    has no sentinel.
    """
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "openai_basic"
    settings = Settings(project_root=initialised_project)

    first = scan_project(fixture)
    persist_scan_result(settings, first)
    doomed_id = first.prompts[0].id

    # Plant a synthetic iterations row. NOT NULL columns: id, prompt_id,
    # round, session_id, is_baseline, weighted_score, per_dim_scores,
    # started_at. (See DDL_ITERATIONS in db.py.)
    conn = db.connect(settings.db_path)
    try:
        with db.transaction(conn):
            conn.execute(
                """
                INSERT INTO iterations (
                    id, prompt_id, round, session_id, is_baseline,
                    weighted_score, per_dim_scores, started_at
                ) VALUES (
                    'iter_planted_for_test',
                    ?,
                    1,
                    'session_planted',
                    1,
                    0.5,
                    '{}',
                    datetime('now')
                )
                """,
                (doomed_id,),
            )
        iter_rows_before = conn.execute(
            "SELECT COUNT(*) FROM iterations WHERE prompt_id = ?",
            (doomed_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert iter_rows_before == 1, "planted iteration row should exist"

    # Re-persist without the doomed prompt — cascade should empty the
    # iterations table for that prompt id.
    survivor = first.model_copy(update={"prompts": first.prompts[1:]})
    persist_scan_result(settings, survivor)

    conn = db.connect(settings.db_path)
    try:
        iter_rows_after = conn.execute(
            "SELECT COUNT(*) FROM iterations WHERE prompt_id = ?",
            (doomed_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert iter_rows_after == 0


def test_persist_removes_orphan_pipeline_db_rows_on_rescan(
    initialised_project: Path,
) -> None:
    """Mirror of :func:`test_persist_removes_orphan_db_rows_on_rescan`
    for the pipelines table. Without this test, commenting out the
    ``delete_pipelines_by_ids`` call inside ``persist_scan_result``
    would leave the entire suite green — and we would only learn about
    the regression when a real project produces a pipeline whose
    fingerprint shifts between scans.

    Uses a hand-built :class:`Pipeline` because the openai_basic
    fixture doesn't produce pipelines; the test only needs the DB
    row + the round-trip through ``persist_scan_result``.
    """
    from aitap.scanner.models import Pipeline

    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "openai_basic"
    settings = Settings(project_root=initialised_project)

    # First persist with a synthetic pipeline added to the result.
    first = scan_project(fixture)
    doomed_pipeline = Pipeline(
        id="pipe_doomed_for_test",
        name="doomed_pipeline",
        nodes=[],
        edges=[],
    )
    first_with_pipeline = first.model_copy(update={"pipelines": [doomed_pipeline]})
    persist_scan_result(settings, first_with_pipeline)

    # Sanity: row landed.
    conn = db.connect(settings.db_path)
    try:
        rows = {r[0] for r in conn.execute("SELECT id FROM pipelines")}
    finally:
        conn.close()
    assert doomed_pipeline.id in rows

    # Second persist drops the pipeline.
    second = first.model_copy(update={"pipelines": []})
    report = persist_scan_result(settings, second)

    conn = db.connect(settings.db_path)
    try:
        rows_after = {r[0] for r in conn.execute("SELECT id FROM pipelines")}
    finally:
        conn.close()
    assert doomed_pipeline.id not in rows_after
    assert report.pipelines_removed_from_db == 1
