"""L1 rule-based scanner.

Public surface (consumed by other worktrees):

- :func:`scan_project` — programmatic entry point used by the API and CLI.
- :func:`make_scan_command` — build the ``aitap scan`` :class:`typer.Typer`
  subcommand. Exported here so ``wt/cli-scaffold`` can register it without
  importing scanner internals (avoiding circular imports between cli.py and
  store/audit packages).
- :func:`build_markdown` — render a :class:`ScanResult` as Markdown text.
- :func:`render_terminal_report` — pretty-print to a rich console.
"""

from __future__ import annotations

from pathlib import Path

import typer

from aitap.scanner.engine import DEFAULT_IGNORE_DIRS, scan_project, to_json
from aitap.scanner.report import build_markdown, render_terminal_report

__all__ = [
    "DEFAULT_IGNORE_DIRS",
    "build_markdown",
    "make_scan_command",
    "render_terminal_report",
    "scan_command",
    "scan_project",
    "to_json",
]


def scan_command(
    path: Path = typer.Argument(  # noqa: B008 — Typer pattern
        Path("."),
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Project root to scan. Defaults to the current directory.",
    ),
    rules_only: bool = typer.Option(
        False,
        "--rules-only",
        help="Force L1 rule-based scan only (CI-friendly default).",
    ),
    deep: bool = typer.Option(
        False,
        "--deep",
        help="Enable L2 deep scan (uses your project's API key — costs money).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit ScanResult as JSON to stdout instead of a Markdown report.",
    ),
) -> None:
    """Scan PATH for LLM prompt sites and emit a Markdown report."""
    if deep and rules_only:
        raise typer.BadParameter("--deep and --rules-only are mutually exclusive.")
    if deep:
        # M5 wires up deep scanning. For now the flag is reserved.
        typer.secho(
            "warning: --deep is not yet implemented; running L1 scan instead.",
            fg=typer.colors.YELLOW,
            err=True,
        )

    result = scan_project(path)

    if json_output:
        typer.echo(to_json(result))
        return

    render_terminal_report(result)


def make_scan_command() -> typer.Typer:
    """Build a single-command :class:`typer.Typer` exposing :func:`scan_command`.

    cli-scaffold can register this on the root app::

        from aitap.scanner import make_scan_command
        app.add_typer(make_scan_command(), name="scan")

    Or, if it prefers a flat command, register :func:`scan_command` directly::

        from aitap.scanner import scan_command
        app.command("scan")(scan_command)
    """
    sub = typer.Typer(
        help="Scan a project for LLM prompt sites.",
        no_args_is_help=False,
        add_completion=False,
    )
    sub.command()(scan_command)
    return sub
