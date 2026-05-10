"""Tests for the public :mod:`aitap.scanner` surface — the scan-command
factory consumed by ``wt/cli-scaffold``."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from aitap.scanner import build_markdown, make_scan_command, scan_command, scan_project

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
OPENAI_BASIC = _FIXTURES / "openai_basic"


def test_scan_command_typer_registration_runs() -> None:
    """Ensure scan_command works when wired into a Typer app the way
    cli-scaffold will register it (multi-subcommand app)."""
    import typer

    app = typer.Typer()
    app.command("scan")(scan_command)

    @app.command("noop")
    def _noop() -> None:  # second command keeps Typer in multi-command mode
        """placeholder"""

    runner = CliRunner()
    result = runner.invoke(app, ["scan", str(OPENAI_BASIC), "--json"])
    assert result.exit_code == 0, result.output
    assert "prompts" in result.stdout


def test_make_scan_command_returns_typer_with_expected_help() -> None:
    sub = make_scan_command()
    runner = CliRunner()
    result = runner.invoke(sub, ["--help"])
    assert result.exit_code == 0
    assert "Scan PATH for LLM prompt sites" in result.stdout


def test_deep_and_rules_only_are_mutually_exclusive() -> None:
    import typer

    app = typer.Typer()
    app.command("scan")(scan_command)

    @app.command("noop")
    def _noop() -> None:
        """placeholder"""

    runner = CliRunner()
    result = runner.invoke(app, ["scan", str(OPENAI_BASIC), "--rules-only", "--deep"])
    assert result.exit_code != 0


def test_build_markdown_includes_prompts_section() -> None:
    result = scan_project(OPENAI_BASIC)
    md = build_markdown(result)
    assert md.startswith("# aitap scan")
    assert "## Prompts" in md
    assert "openai" in md
