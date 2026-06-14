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
    from aitap.config import Settings
    from aitap.deep.client import LLMClient, ProviderError
    from aitap.deep.orchestrator import L2CostEstimate
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
    profile_id: str | None = typer.Option(
        None,
        "--profile",
        help=(
            "Profile id to use for the deep scan. Routes L2 through the "
            "new profile-keyed dispatch (DeepSeek / Moonshot / Groq / "
            "OpenAI / Anthropic gateways). Falls back to the legacy "
            "provider config when omitted."
        ),
    ),
) -> None:
    """Scan PATH for LLM prompt sites and emit a Markdown report."""
    if deep and rules_only:
        raise typer.BadParameter("--deep and --rules-only are mutually exclusive.")
    if profile_id is not None and not deep:
        raise typer.BadParameter("--profile only applies when --deep is set.")

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
        result = _run_l2(
            result,
            auto_approve=yes,
            json_mode=json_output,
            profile_id=profile_id,
        )

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


def _run_l2(
    result: ScanResult,
    *,
    auto_approve: bool,
    json_mode: bool,
    profile_id: str | None = None,
) -> ScanResult:
    """Run the L2 enrichment pass, returning a new (or unchanged) ScanResult.

    Defers the orchestrator + provider imports so a vanilla `aitap scan` (no
    --deep) doesn't pay the import cost. Failures are surfaced as warnings
    on stderr and the original result flows through.

    Dispatch (after contract v4 / A2-P3): profile-only. If *profile_id*
    is set, or ``settings.defaults.model_profile_id`` is set, or the
    project has exactly one configured profile, resolve a
    :class:`~aitap.config.ProfileConfig` and build the client via
    :func:`aitap.deep.factory.get_client_for_profile_config`. This
    routes through the OpenAI-compatible / Anthropic-protocol client
    family that PR #40 shipped — DeepSeek / Moonshot / Groq / Together
    / Qwen / SiliconFlow / Ollama / LM Studio all work natively.
    Otherwise the deep pass is skipped and the L1 result flows through.

    The legacy ``settings.provider`` / ``settings.provider.model`` /
    ``secrets.get_key`` fallback was removed in A2-P3 alongside the
    legacy ``RunCreate.provider`` / ``RunCreate.model`` and the
    ``register_provider`` / ``OpenAIClient`` / ``get_client`` registry.

    An explicit ``--profile`` that doesn't resolve fails loudly (no
    silent fallback): "you asked for X, X isn't configured" is more
    helpful than a stack trace.
    """
    from aitap.config import Settings

    try:
        from aitap.deep.client import ProviderError
        from aitap.deep.orchestrator import L2CostEstimate, enrich_with_l2
    except ImportError as exc:
        if not json_mode:
            typer.secho(
                f"warning: couldn't load the deep-scan engine ({exc}). Returning the L1 result.",
                fg=typer.colors.YELLOW,
                err=True,
            )
        return result

    settings = Settings()

    # --- Profile path -----------------------------------------------------
    resolved_profile_id = _resolve_profile_id(settings, profile_id)
    if resolved_profile_id is not None:
        client = _build_profile_client(
            settings,
            resolved_profile_id,
            explicit=profile_id is not None,
            json_mode=json_mode,
        )
        if client is not None:
            return _run_enrichment(
                result,
                client,
                auto_approve=auto_approve,
                json_mode=json_mode,
                L2CostEstimate=L2CostEstimate,
                enrich_with_l2=enrich_with_l2,
                ProviderError=ProviderError,
            )
        # An explicit --profile that failed (no key / not found) already
        # warned; don't silently fall through to legacy.
        if profile_id is not None:
            return result
        # An implicit profile that failed (single-profile / default) ALSO
        # already warned — but for symmetry with the explicit case, we
        # don't fall through. The user can drop their default to opt
        # back into the legacy path.
        return result

    # --- Multi-profile ambiguity guard ------------------------------------
    # Two or more profiles configured but no explicit --profile and no
    # ``defaults.model_profile_id`` → ambiguous intent. Silently dropping
    # to the legacy path here would surprise a user who configured
    # profiles thinking they were *the* L2 path; tell them which knob to
    # turn so the next ``--deep`` lands on the profile they meant.
    configured_profiles = settings.profiles
    if len(configured_profiles) >= 2:
        names = ", ".join(repr(p.id) for p in configured_profiles)
        typer.secho(
            f"warning: --deep found {len(configured_profiles)} configured "
            f"profiles ({names}) but no default. Pick one with `--profile "
            f"<id>`, or set the default in Settings. Skipping the deep "
            "pass and returning L1 results.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        return result

    # No profile resolved — A2-P3 (contract v4) removed the legacy
    # provider-keyed fallback that lived here. Tell the user the next
    # action and return the L1 result so the rest of ``aitap scan``
    # still produces something useful. ``ProviderError`` is still
    # referenced via ``_run_enrichment`` above for profile-path
    # errors.
    if not json_mode:
        typer.secho(
            "--deep needs a profile to dispatch through. Open Settings "
            "and add (or pick) a default model profile, then re-run. "
            "Skipping the deep pass and returning L1 results.",
            fg=typer.colors.YELLOW,
            err=True,
        )
    return result


# ---------------------------------------------------------------------------
# Profile resolution helpers
# ---------------------------------------------------------------------------


def _resolve_profile_id(settings: Settings, explicit: str | None) -> str | None:
    """Pick the profile id to use for L2.

    Order:

    1. Explicit ``--profile`` flag.
    2. ``settings.defaults.model_profile_id`` (the per-project default
       a user picks in Settings).
    3. The single configured profile (when there's exactly one — no
       ambiguity, so no need to make the user retype it).

    Returns ``None`` when none of the three apply; the caller then falls
    back to the legacy provider/model path.
    """
    if explicit:
        return explicit
    model_profile_id = settings.defaults.model_profile_id
    if model_profile_id:
        return model_profile_id
    profiles = settings.profiles
    if len(profiles) == 1:
        return profiles[0].id
    return None


def _build_profile_client(
    settings: Settings,
    profile_id: str,
    *,
    explicit: bool,
    json_mode: bool,
) -> LLMClient | None:
    """Look up *profile_id* in ``settings.profiles`` and build a client.

    Returns the client on success, or ``None`` after emitting a
    plain-language warning on failure. The *explicit* flag colours the
    error message: a user-typed ``--profile X`` deserves a different
    sentence than a default that points at a missing profile.

    Warnings go to stderr regardless of *json_mode*. The JSON consumer
    reads stdout (where ``ScanResult.model_dump_json`` lands), so
    surfacing the failure on stderr leaves stdout intact while still
    telling the human user *why* their ``--deep`` got downgraded to
    L1. A silent JSON-mode degradation would leave the user wondering
    why ``l2_used`` is ``false`` after they passed ``--deep --profile``.
    """
    profile_config = next((p for p in settings.profiles if p.id == profile_id), None)
    if profile_config is None:
        source = "your --profile flag" if explicit else "the default profile"
        typer.secho(
            f"warning: {source} points at {profile_id!r}, but no profile "
            f"with that id is configured. Open Settings to add one, then "
            f"re-run. Skipping the deep pass and returning L1 results.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        _ = json_mode  # explicit: we intentionally surface the warning either way
        return None

    from aitap import secrets as secrets_module

    api_key = secrets_module.get_key_for_profile(profile_id)
    if not api_key:
        typer.secho(
            f"warning: profile {profile_id!r} has no API key set. Open "
            f"Settings and click Test next to it, or set it under "
            f"profile:{profile_id} in `~/.aitap/secrets.yaml`. "
            "Skipping the deep pass and returning L1 results.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        return None

    try:
        from aitap.deep.factory import get_client_for_profile_config

        return get_client_for_profile_config(profile_config, api_key)
    except Exception as exc:
        typer.secho(
            f"warning: couldn't build the deep-scan client for profile "
            f"{profile_id!r} ({exc}). Returning the L1 result.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        return None


def _run_enrichment(
    result: ScanResult,
    client: LLMClient,
    *,
    auto_approve: bool,
    json_mode: bool,
    L2CostEstimate: type[L2CostEstimate],
    enrich_with_l2: Any,
    ProviderError: type[ProviderError],
) -> ScanResult:
    """Run the cost-confirmed enrichment loop. Shared between the
    profile path and the legacy path so the cost-gate UX, the JSON
    refusal behaviour, and the ProviderError → warn-and-fallback
    handling are byte-for-byte identical regardless of how the client
    was constructed.
    """
    import asyncio

    def _confirm(estimate: object) -> bool:
        if not json_mode:
            typer.secho(
                f"L2 deep scan: {estimate.total_calls} LLM calls, "  # type: ignore[attr-defined]
                f"~${estimate.estimated_usd:.4f} on {estimate.model}",  # type: ignore[attr-defined]
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
        return asyncio.run(enrich_with_l2(client, result, confirm=_confirm))  # type: ignore[operator]
    except ProviderError as exc:
        if not json_mode:
            typer.secho(
                f"warning: deep scan stopped ({exc}). Returning the L1 result.",
                fg=typer.colors.YELLOW,
                err=True,
            )
        return result


def _persist_if_initialised(result: ScanResult, *, suppress_output: bool) -> None:
    """Write *result* into the user's project ``.aitap/`` if it exists.

    Errors are surfaced to stderr but never raise — a persistence failure
    must not mask the scan output the user came for.

    Resolution order for ``project_root`` (highest first):

    1. ``AITAP_PROJECT_ROOT`` env var — the documented override; users
       and tests rely on it to point persistence at a path that differs
       from the scan target.
    2. :attr:`ScanResult.project_root` — the scan target itself. This
       is the right default for the CLI case ``aitap scan <path>`` and
       fixes a real bug where a bare ``Settings()`` resolved to
       :func:`Path.cwd`, which a wrapper invocation
       (``uv --directory <aitap_repo> run aitap scan <user_project>``)
       had already changed to the wrapper's project root — persistence
       then tried to write into ``<aitap_repo>/.aitap/`` and failed.
    3. ``Path.cwd()`` — only reached when neither override is set, e.g.
       a programmatic ``scan_project(path)`` from a Python REPL that
       lives in the same project as the scan target.
    """
    import os
    from pathlib import Path

    from aitap.config import Settings
    from aitap.store import persist_scan_result

    if "AITAP_PROJECT_ROOT" in os.environ:
        # Defer to pydantic-settings — it'll read the env var and resolve
        # to the user's override.
        settings = Settings()
    else:
        settings = Settings(project_root=Path(result.project_root))

    try:
        report = persist_scan_result(settings, result)
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
