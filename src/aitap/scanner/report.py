"""Render a :class:`ScanResult` to the terminal as Markdown via :mod:`rich`.

Kept dependency-free of any I/O — :func:`render_terminal_report` just hands a
:class:`rich.console.Console` the rendered Markdown. Tests construct their own
console (with ``record=True``) to capture output deterministically.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console
from rich.markdown import Markdown

from aitap.scanner.models import (
    Confidence,
    Provider,
    ScanResult,
    TemplateKind,
)


def build_markdown(result: ScanResult) -> str:
    """Return the Markdown body for *result*. Pure function, no rich involved."""
    buf = StringIO()
    write = buf.write

    write(f"# aitap scan — {result.project_root}\n\n")
    write(f"- **Files scanned**: {result.files_scanned}\n")
    write(f"- **Prompts found**: {len(result.prompts)}\n")
    write(f"- **Pipelines**: {len(result.pipelines)}\n")
    write(f"- **Providers detected**: {len(result.providers_detected)}\n")
    write(f"- **L2 used**: {'yes' if result.l2_used else 'no'}\n")
    if result.git_commit:
        write(f"- **git commit**: `{result.git_commit}`\n")
    write("\n")

    if result.providers_detected:
        write("## Providers detected\n\n")
        for ev in result.providers_detected:
            write(
                f"- `{_format_provider(ev.provider)}` — "
                f"`{ev.key_var_name}` in `{ev.location.file}:{ev.location.line_start}` "
                f"({ev.source})\n"
            )
        write("\n")

    if result.prompts:
        write("## Prompts\n\n")
        for site in result.prompts:
            write(
                f"### {site.name}  \n"
                f"`{site.location.file}:{site.location.line_start}`  \n"
                f"- provider: **{_format_provider(site.provider)}**\n"
                f"- confidence: **{_format_confidence(site.confidence)}**\n"
            )
            if site.parameters.model:
                write(f"- model: `{site.parameters.model}`\n")
            if site.parameters.temperature is not None:
                write(f"- temperature: `{site.parameters.temperature}`\n")
            if site.parameters.max_tokens is not None:
                write(f"- max_tokens: `{site.parameters.max_tokens}`\n")
            if site.parameters.response_format:
                write(f"- response_format: `{site.parameters.response_format}`\n")
            if site.tags:
                write(f"- tags: {', '.join(f'`{t}`' for t in site.tags)}\n")
            write("\n")
            for msg in site.messages:
                preview = _preview(msg.template_text)
                kind_marker = _format_kind(msg.template_kind)
                write(f"  - **{msg.role.value}** {kind_marker}: {preview}\n")
            write("\n")
    else:
        write("_No prompts found. Try `aitap scan --deep` to enable L2._\n\n")

    if result.warnings:
        write("## Warnings\n\n")
        for warn in result.warnings:
            location = (
                f"{warn.location.file}:{warn.location.line_start}"
                if warn.location is not None
                else "<global>"
            )
            write(f"- `{warn.code}` at `{location}` — {warn.message}\n")
        write("\n")

    return buf.getvalue()


def render_terminal_report(result: ScanResult, *, console: Console | None = None) -> None:
    """Print the report to *console* (default: a fresh stdout console)."""
    if console is None:
        console = Console()
    console.print(Markdown(build_markdown(result)))


def _format_provider(provider: Provider) -> str:
    return provider.value


def _format_confidence(confidence: Confidence) -> str:
    return confidence.value


def _format_kind(kind: TemplateKind) -> str:
    return f"_({kind.value})_"


def _preview(text: str, *, limit: int = 200) -> str:
    """Inline preview of a template body — single-line, length-capped, with the
    body wrapped in backticks. Empty strings render as ``_(empty)_``."""
    if not text:
        return "_(empty)_"
    flat = text.replace("\n", " ⏎ ")
    if len(flat) > limit:
        flat = flat[: limit - 1] + "…"
    return f"`{flat}`"
