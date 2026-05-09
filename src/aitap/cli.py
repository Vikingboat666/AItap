"""Typer CLI entry. Subcommands are wired up by Wave 1 wt/cli-scaffold."""

from __future__ import annotations

import typer

from aitap import __version__

app = typer.Typer(
    name="aitap",
    help="Zero-config CLI to discover, test, and iterate prompts in your AI codebase.",
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"aitap {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(  # noqa: B008
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """aitap root command."""


# Subcommands registered by their owning modules:
#   aitap.scanner.cli  -> scan
#   aitap.store.cli    -> init
#   aitap.audit.cli    -> audit
#   aitap.server.cli   -> ui
#   aitap.store.history_cli -> diff, rollback
