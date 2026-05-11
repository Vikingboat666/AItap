"""Typer CLI entry.

`aitap init` is fully implemented here. The other subcommands (`scan`, `audit`,
`ui`, `diff`, `rollback`) are scaffolded with their final signatures, help text,
and flags so other worktrees can fill in the bodies without changing the
public surface. Each stub prints a rich-styled "not yet implemented" notice
and exits with code 0 so help/inspection workflows don't break.

Dispatch pattern for stubs: we use `importlib.util.find_spec` to detect whether
the downstream module has been wired up yet. We deliberately avoid catching
`ImportError` around the real import — that would silently mask real bugs in a
present-but-broken module (syntax error, missing transitive dep) as "not yet
implemented." `find_spec` only reports module visibility; if the module exists,
the import is allowed to fail loudly.

Validation contract for `scan`: the CLI guarantees `not (rules_only and deep)`
before dispatching to `scan_project`. Downstream engines may treat this as an
invariant and skip re-validating.
"""

from __future__ import annotations

import contextlib
import sys
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from typing import IO, Annotated, Literal

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from aitap import __version__
from aitap.config import CostLimits, ProviderConfig, Settings

app = typer.Typer(
    name="aitap",
    help="Zero-config CLI to discover, test, and iterate prompts in your AI codebase.",
    no_args_is_help=True,
    add_completion=False,
)


def _force_utf8_stdio() -> None:
    """Reconfigure stdout/stderr to UTF-8 before any console renders.

    Windows defaults stdout to the OS code page (often cp936 / GBK in CN
    locales) and rich crashes the moment it tries to render a bullet,
    arrow, ellipsis, or any other glyph outside that page. Forcing UTF-8
    with errors='replace' keeps output alive even on terminals that can't
    actually display every glyph — better a `?` than an aborted command.

    No-op when:
      - the stream lacks ``reconfigure`` (already replaced by a test runner
        capturing into StringIO, or a bytes stream);
      - the stream is already UTF-8.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if encoding == "utf8":
            continue
        # Stream may be in a mode that forbids reconfigure (e.g., bytes wrapper);
        # silently leave it alone rather than abort startup.
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8", errors="replace")


_force_utf8_stdio()


def _console_width(stream: IO[str]) -> int | None:
    """Pin width when the stream isn't a TTY (CI, pipes) so panels don't wrap
    long absolute paths into garbage; let rich auto-detect on real terminals
    so users with narrow windows still get a readable layout."""
    return None if stream.isatty() else 120


console = Console(width=_console_width(sys.stdout))
err_console = Console(stderr=True, width=_console_width(sys.stderr))


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"aitap {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            callback=_version_callback,
            is_eager=True,
            help="Show version and exit.",
        ),
    ] = False,
) -> None:
    """aitap root command."""


# --------------------------------------------------------------------------- #
# init — fully implemented                                                    #
# --------------------------------------------------------------------------- #

# Tri-state status for each artifact init touches:
#   created  — new file/dir written by this run
#   appended — pre-existing file edited (e.g., aitap block added to .gitignore)
#   exists   — already aitap-managed, left alone
ItemStatus = Literal["created", "appended", "exists"]

_GITIGNORE_BANNER = "# >>> aitap >>>"
_GITIGNORE_FOOTER = "# <<< aitap <<<"
# `.aitap/db.sqlite-*` covers WAL companions (-wal, -shm) and rollback-journal
# (-journal) without us having to predict which mode store/db.py picks.
_GITIGNORE_ENTRIES: tuple[str, ...] = (
    ".aitap/db.sqlite",
    ".aitap/db.sqlite-*",
    ".aitap/runs/",
)


def _default_config_yaml() -> str:
    """Render the default config.yaml from config.py's pydantic defaults.

    Single source of truth — when ProviderConfig/CostLimits defaults change in
    config.py, this template tracks them automatically.
    """
    p = ProviderConfig()
    c = CostLimits()
    return (
        "# aitap project config — edit to taste, commit alongside your code.\n"
        "# Environment variables prefixed with AITAP_ override these values\n"
        "# (e.g., AITAP_PROVIDER__MODEL=claude-opus-4-7).\n"
        "\n"
        "provider:\n"
        f'  name: {p.name}            # "anthropic" | "openai"\n'
        f"  model: {p.model}\n"
        "  judge_model: null         # falls back to `provider.model` when null\n"
        "\n"
        "cost:\n"
        f"  per_run_usd: {c.per_run_usd:.2f}\n"
        f"  per_session_usd: {c.per_session_usd:.2f}\n"
    )


def _render_gitignore_block() -> str:
    body = "\n".join(_GITIGNORE_ENTRIES)
    return f"{_GITIGNORE_BANNER}\n{body}\n{_GITIGNORE_FOOTER}\n"


def _ensure_gitignore(project_root: Path) -> tuple[Path, ItemStatus]:
    """Idempotently apply the aitap block to .gitignore.

    Returns:
        (path, status) where status is:
          - "created"  if .gitignore did not exist and we wrote a new one
          - "appended" if .gitignore existed and we added our block to it
          - "exists"   if our banner is already present (no write)
    """
    gi = project_root / ".gitignore"
    block = _render_gitignore_block()
    if gi.exists():
        existing = gi.read_text(encoding="utf-8")
        if _GITIGNORE_BANNER in existing:
            return gi, "exists"
        sep = "" if existing.endswith("\n") or existing == "" else "\n"
        gi.write_text(existing + sep + "\n" + block, encoding="utf-8")
        return gi, "appended"
    gi.write_text(block, encoding="utf-8")
    return gi, "created"


_STATUS_STYLE: dict[ItemStatus, str] = {
    "created": "green",
    "appended": "cyan",
    "exists": "yellow",
}


@app.command("init")
def init_command(
    path: Annotated[
        Path,
        typer.Argument(
            help="Project root to initialize. Must be an existing directory.",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
        ),
    ] = Path("."),
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            "-f",
            help="Overwrite an existing .aitap/config.yaml.",
        ),
    ] = False,
) -> None:
    """Initialize a project: create .aitap/ skeleton and update .gitignore."""
    project_root = path.resolve()
    # typer's `dir_okay`/`file_okay` only validates type when the path *exists*;
    # explicitly reject non-existent paths so users don't accidentally pollute
    # a wrong directory by typo.
    if not project_root.exists():
        err_console.print(
            f"[red]error:[/red] {project_root} does not exist. "
            "Create the project directory first, then run aitap init inside it."
        )
        raise typer.Exit(code=2)
    if not project_root.is_dir():
        err_console.print(f"[red]error:[/red] {project_root} is not a directory.")
        raise typer.Exit(code=2)

    settings = Settings(project_root=project_root)
    aitap_dir = project_root / settings.aitap_dir
    aitap_dir.mkdir(parents=True, exist_ok=True)

    # Drive subdir creation off Settings' properties so future config.py
    # changes (e.g., renaming "runs" → "snapshots") propagate here for free.
    sub_targets: tuple[tuple[Path, str], ...] = (
        (settings.prompts_dir, ".aitap/prompts/"),
        (settings.pipelines_dir, ".aitap/pipelines/"),
        (settings.datasets_dir, ".aitap/datasets/"),
        (settings.runs_dir, ".aitap/runs/"),
    )

    rows: list[tuple[ItemStatus, str]] = []

    for dir_path, label in sub_targets:
        if dir_path.exists():
            rows.append(("exists", label))
        else:
            dir_path.mkdir(parents=True, exist_ok=True)
            rows.append(("created", label))

    config_path = aitap_dir / "config.yaml"
    if config_path.exists() and not force:
        rows.append(("exists", ".aitap/config.yaml"))
    else:
        config_path.write_text(_default_config_yaml(), encoding="utf-8")
        rows.append(("created", ".aitap/config.yaml"))

    gi_path, gi_status = _ensure_gitignore(project_root)
    rows.append((gi_status, gi_path.name))

    table = Table(show_header=True, header_style="bold cyan", box=None, pad_edge=False)
    table.add_column("Status", style="bold")
    table.add_column("Path")
    for status, label in rows:
        color = _STATUS_STYLE[status]
        table.add_row(f"[{color}]{status}[/{color}]", label)

    console.print(
        Panel.fit(
            table,
            title=f"aitap init — {project_root}",
            border_style="cyan",
        )
    )
    console.print(
        "[dim]Next:[/dim] run [bold]aitap scan[/bold] to discover prompts in this project."
    )


# --------------------------------------------------------------------------- #
# scan / audit / ui / diff / rollback — scaffolded stubs                      #
# --------------------------------------------------------------------------- #


def _module_available(module: str) -> bool:
    """Probe whether a downstream module exists, without importing it.

    Using `find_spec` instead of `try/except ImportError` around the import
    means a present-but-broken module (e.g., syntax error, missing transitive
    dep) raises loudly rather than being silently misreported as "not yet
    implemented." Tests can monkeypatch this function directly to simulate
    a "module merged" state.
    """
    return find_spec(module) is not None


def _not_yet_implemented(command: str, status_label: str, hint: str | None = None) -> None:
    """Emit a consistent "not yet implemented" notice for stub commands."""
    body = (
        f"[yellow]aitap {command}[/yellow] is not yet implemented.\n"
        f"Status: [bold]{status_label}[/bold]"
    )
    if hint:
        body += f"\n\n{hint}"
    err_console.print(
        Panel.fit(
            body,
            title="not yet implemented",
            border_style="yellow",
        )
    )


# `aitap scan` is owned by the scanner package — it exports `scan_command`
# specifically so we can register it here without duplicating the flag surface
# or importing scanner internals. See src/aitap/scanner/__init__.py.
from aitap.scanner import scan_command as _scanner_scan_command  # noqa: E402

app.command("scan")(_scanner_scan_command)


@app.command("audit")
def audit_command(
    repo: Annotated[
        str,
        typer.Argument(
            help=(
                "Remote repo to audit. Accepts 'gh:owner/repo' shorthand or any "
                "git-cloneable URL (https://, git@, etc)."
            ),
        ),
    ],
    rules_only: Annotated[
        bool,
        typer.Option(
            "--rules-only",
            help="Force L1 (default for audit — never run L2 against unknown code).",
        ),
    ] = True,
    keep_clone: Annotated[
        bool,
        typer.Option(
            "--keep-clone",
            help="Keep the temporary clone after the report; default is to delete.",
        ),
    ] = False,
) -> None:
    """Read-only audit of a remote repository (clone → scan → report → cleanup)."""
    if not _module_available("aitap.audit.clone"):
        _not_yet_implemented(
            "audit",
            "coming in M2 (remote audit)",
            hint=(
                f"Would audit: [bold]{repo}[/bold]\n"
                f"Keep clone: {keep_clone}    Rules-only: {rules_only}"
            ),
        )
        return

    audit = import_module("aitap.audit.clone")
    audit.audit_repo(repo, rules_only=rules_only, keep_clone=keep_clone)


@app.command("ui")
def ui_command(
    port: Annotated[
        int,
        typer.Option(
            "--port",
            "-p",
            min=1,
            max=65535,
            help="Port to bind the web playground on.",
        ),
    ] = 7860,
    host: Annotated[
        str,
        typer.Option(
            "--host",
            help="Host interface to bind. Defaults to localhost (loopback only).",
        ),
    ] = "127.0.0.1",
    no_browser: Annotated[
        bool,
        typer.Option(
            "--no-browser",
            help="Don't auto-open a browser tab.",
        ),
    ] = False,
) -> None:
    """Launch the local web playground (FastAPI + bundled React UI)."""
    if not _module_available("aitap.server.app"):
        _not_yet_implemented(
            "ui",
            "coming in M3 (web playground)",
            hint=f"Would serve on http://{host}:{port}",
        )
        return

    server = import_module("aitap.server.app")
    server.serve(host=host, port=port, open_browser=not no_browser)


@app.command("diff")
def diff_command(
    prompt: Annotated[
        str,
        typer.Argument(help="Prompt id or name (e.g., 'summarize_email')."),
    ],
    v1: Annotated[
        int,
        typer.Argument(help="Older version number to diff from."),
    ],
    v2: Annotated[
        int,
        typer.Argument(help="Newer version number to diff to."),
    ],
) -> None:
    """Show a side-by-side diff between two stored prompt versions."""
    if not _module_available("aitap.store.history"):
        _not_yet_implemented(
            "diff",
            "coming in M2 (storage + history)",
            hint=f"Would diff: [bold]{prompt}[/bold]  v{v1} → v{v2}",
        )
        return

    history = import_module("aitap.store.history")
    history.diff_versions(prompt, v1, v2)


@app.command("rollback")
def rollback_command(
    prompt: Annotated[
        str,
        typer.Argument(help="Prompt id or name to rollback."),
    ],
    version: Annotated[
        int,
        typer.Argument(help="Target version to roll back to (creates a new head version)."),
    ],
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Skip the interactive confirmation prompt.",
        ),
    ] = False,
) -> None:
    """Roll a prompt back to a previous version (creates a new head version)."""
    if not _module_available("aitap.store.history"):
        _not_yet_implemented(
            "rollback",
            "coming in M2 (storage + history)",
            hint=f"Would rollback: [bold]{prompt}[/bold] → v{version}    Skip-confirm: {yes}",
        )
        return

    history = import_module("aitap.store.history")
    history.rollback_version(prompt, version, skip_confirm=yes)
