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
