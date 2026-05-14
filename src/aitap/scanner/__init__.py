"""L1 rule-based scanner.

Public surface (consumed by other worktrees):

- :func:`scan_project` — programmatic entry point used by the API and CLI.
- :func:`make_scan_command` — build the ``aitap scan`` :class:`typer.Typer`
  subcommand. Exported here so ``wt/cli-scaffold`` can register it without
  importing scanner internals (avoiding circular imports between cli.py and
  store/audit packages).
- :func:`build_markdown` — render a :class:`ScanResult` as Markdown text.
- :func:`render_terminal_report` — pretty-print to a rich console.

Imports of the engine / report modules are deferred to first attribute
access (via :func:`__getattr__`) so that ``python -m aitap.scanner.engine``
does not double-import the engine module — runpy would otherwise emit a
``RuntimeWarning`` because the package init eagerly loaded the same module
that runpy is about to execute as ``__main__``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

if TYPE_CHECKING:
    from aitap.scanner.engine import DEFAULT_IGNORE_DIRS, scan_project, to_json
    from aitap.scanner.models import ScanResult
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


_ENGINE_NAMES = {"DEFAULT_IGNORE_DIRS", "scan_project", "to_json"}
_REPORT_NAMES = {"build_markdown", "render_terminal_report"}


def __getattr__(name: str) -> Any:
    """Lazy re-exports for the engine/report modules.

    See module docstring for why this is lazy."""
    if name in _ENGINE_NAMES:
        from aitap.scanner import engine

        return getattr(engine, name)
    if name in _REPORT_NAMES:
        from aitap.scanner import report

        return getattr(report, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Auto-approve cost prompts (e.g., L2 cost confirmation).",
    ),
) -> None:
    """Scan PATH for LLM prompt sites and emit a Markdown report."""
    if deep and rules_only:
        raise typer.BadParameter("--deep and --rules-only are mutually exclusive.")

    # Deferred import — see module docstring for why __init__ stays lazy.
    from aitap.scanner.engine import scan_project as _scan_project
    from aitap.scanner.engine import to_json as _to_json
    from aitap.scanner.report import render_terminal_report as _render

    result: ScanResult = _scan_project(path)

    # L2 enrichment runs BEFORE persistence so the enriched data (confirmed
    # confidence, resolved templates, inferred purposes) is what lands in
    # .aitap/ — otherwise re-running scan would lose every enrichment
    # between sessions.
    if deep:
        result = _run_l2(result, auto_approve=yes, json_mode=json_output)

    # Persistence hook (wt/store): silently no-ops when the user's project
    # hasn't run `aitap init`. Persistence is keyed off Settings.project_root
    # (defaults to cwd, overridable via $AITAP_PROJECT_ROOT) — *not* the scan
    # target — so `aitap scan src/` from a project root persists into ./.aitap,
    # and scanning a fixture inside the test suite never touches anything.
    _persist_if_initialised(result, suppress_output=json_output)

    if json_output:
        typer.echo(_to_json(result))
        return

    _render(result)


def _run_l2(result: ScanResult, *, auto_approve: bool, json_mode: bool) -> ScanResult:
    """Run the L2 enrichment pass, returning a new (or unchanged) ScanResult.

    Defers the orchestrator + provider imports so a vanilla `aitap scan` (no
    --deep) doesn't pay the import cost. Failures are surfaced as warnings
    on stderr and the original result flows through.
    """
    import asyncio

    from aitap.config import Settings

    try:
        from aitap.deep.client import ProviderError, get_client
        from aitap.deep.orchestrator import L2CostEstimate, enrich_with_l2
    except ImportError as exc:
        if not json_mode:
            typer.secho(f"warning: L2 unavailable ({exc})", fg=typer.colors.YELLOW, err=True)
        return result

    settings = Settings()
    try:
        client = get_client(
            settings.provider.name,
            settings.provider.model,
        )
    except Exception as exc:
        if not json_mode:
            typer.secho(
                f"warning: cannot get L2 client ({exc}); falling back to L1 result",
                fg=typer.colors.YELLOW,
                err=True,
            )
        return result

    def _confirm(estimate: L2CostEstimate) -> bool:
        if not json_mode:
            typer.secho(
                f"L2 deep scan: {estimate.total_calls} LLM calls, "
                f"~${estimate.estimated_usd:.4f} on {estimate.model}",
                fg=typer.colors.CYAN,
                err=True,
            )
        if auto_approve:
            return True
        if json_mode:
            # Without TTY interaction in JSON mode we refuse to spend by default.
            return False
        return typer.confirm("Proceed?", default=False)

    # Provider key validation is lazy (per the LLMClient contract — clients
    # don't touch the network at construction). That means auth/rate-limit/
    # transport errors only fire on the first chat() call inside the
    # enrichers — i.e., here, inside asyncio.run. Without this guard,
    # `aitap scan --deep` without an API key surfaces a full traceback
    # instead of the documented "warn + L1 fallback" behaviour.
    try:
        return asyncio.run(enrich_with_l2(client, result, confirm=_confirm))
    except ProviderError as exc:
        if not json_mode:
            typer.secho(
                f"warning: L2 enrichment aborted ({exc}); using L1 result",
                fg=typer.colors.YELLOW,
                err=True,
            )
        return result


def _persist_if_initialised(result: ScanResult, *, suppress_output: bool) -> None:
    """Write *result* into the user's project ``.aitap/`` if it exists.

    Errors are surfaced to stderr but never raise — a persistence failure
    must not mask the scan output the user came for.
    """
    from aitap.config import Settings
    from aitap.store import persist_scan_result

    try:
        report = persist_scan_result(Settings(), result)
    except Exception as exc:
        if not suppress_output:
            typer.secho(
                f"warning: failed to persist scan to .aitap/: {exc}",
                fg=typer.colors.YELLOW,
                err=True,
            )
        return

    if suppress_output or report.skipped_no_aitap:
        return

    typer.secho(
        f"persisted to .aitap/  ({report.prompts_written} prompts, "
        f"{report.pipelines_written} pipelines)",
        fg=typer.colors.GREEN,
        err=True,
    )


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
