"""HTTP routes for ``/api/settings`` and ``/api/settings/cost-estimate``.

The settings surface is the bridge between the long-lived YAML config
(``.aitap/config.yaml``) and the UI. After the multi-provider redesign
(contract v3) this module exposes:

- ``GET /api/settings`` — what the process is currently running with,
  plus any detected providers from the SQLite ``providers_detected`` table
  (populated by the scanner's L1 env inspector) and the per-process
  default profile selections.
- ``PUT /api/settings/defaults`` — pick which configured profile is
  the default model / judge. Delegates to
  :mod:`aitap.server.routes.profiles` so the in-process cache and the
  YAML mirror stay in lockstep.
- ``GET /api/settings/cost-estimate`` — pricebook-driven dry-run cost
  for a given prompt + model, using :mod:`aitap.deep.pricing` and a
  rough 4-chars-per-token estimator.

The legacy provider-keyed routes (``POST /api/settings/key``,
``DELETE /api/settings/key/{provider}``,
``POST /api/settings/test/{provider}``, ``PUT /api/settings``) were
removed in contract v3 — per-profile key management lives on
``/api/profiles`` now. See ``docs/profiles-design.md`` for the design
and ``CONTRACTS.md`` for the change protocol.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException

from aitap.config import Settings
from aitap.deep import pricing
from aitap.scanner.models import (
    CodeLocation,
    Provider,
    ProviderEvidence,
)
from aitap.server.routes import (
    CostEstimateResponse,
    Defaults,
    SettingsResponse,
)

# Local import to avoid the circular profiles ↔ settings dance at module
# load. The profiles router owns the canonical ``_defaults`` cache and
# exposes ``current_defaults`` / ``set_defaults`` helpers we delegate to.
from aitap.server.routes import profiles as profiles_routes
from aitap.server.routes._deps import get_db, get_settings

router = APIRouter(tags=["settings"])


@router.get("/settings", response_model=SettingsResponse)
def get_settings_endpoint(
    settings: Annotated[Settings, Depends(get_settings)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> SettingsResponse:
    """Render the effective :class:`Settings` + detected providers as JSON.

    Per-profile key status no longer rides on this response — clients
    that need it call ``GET /api/profiles`` instead. The legacy
    provider/model/judge_model fields stay so existing internal callers
    that key off them keep working until they switch to the profiles
    API.
    """
    provider_name, model, judge_model = _effective_provider(settings)
    cost = _effective_cost(settings)
    providers = _read_detected_providers(conn)
    return SettingsResponse(
        provider=_coerce_provider(provider_name),
        model=model,
        judge_model=judge_model,
        cost_per_run_usd=cost[0],
        cost_per_session_usd=cost[1],
        providers_available=providers,
        defaults=profiles_routes.current_defaults(settings),
    )


@router.put("/settings/defaults", response_model=Defaults)
def put_settings_defaults(
    payload: Defaults,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Defaults:
    """Pick which configured profiles are the default model + judge.

    The route delegates validation + persistence to
    :func:`aitap.server.routes.profiles.set_defaults` so the in-process
    cache and the YAML mirror stay in lockstep. 422 + plain-language
    detail when a referenced profile id doesn't exist; ``None`` on
    either field clears the corresponding default.
    """
    return profiles_routes.set_defaults(settings, payload)


@router.get("/settings/cost-estimate", response_model=CostEstimateResponse)
def get_cost_estimate(
    prompt_id: str,
    model: str,
    settings: Annotated[Settings, Depends(get_settings)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> CostEstimateResponse:
    """Estimate cost of running a prompt against *model* once.

    Token counts are rough — we use the classic "4 characters per token"
    heuristic against the latest prompt-version template text. The
    estimate is meant for "should I bother running this?" UX, not for
    accounting.

    Raises 404 when the prompt has no stored template (e.g., scanner
    saw the site but no version row was ever created) and 400 when the
    model isn't in our pricebook.
    """
    template_text = _load_template_text(conn, prompt_id)
    if template_text is None:
        raise HTTPException(
            status_code=404,
            detail=f"prompt {prompt_id!r} has no stored template",
        )

    # 4 chars/token is a documented heuristic across OpenAI/Anthropic
    # tokenizers for English. Good enough for an "approximate cost" UX.
    input_tokens = max(1, len(template_text) // 4)
    # Assume the user wants roughly as much output as input; the
    # frontend can request a tighter ceiling via the run parameters
    # later. We cap at 1024 so an enormous system prompt doesn't blow
    # the estimate up to billions of tokens.
    output_tokens = min(1024, max(64, input_tokens))

    # Price only with the project's *configured* provider. A silent
    # cross-provider fallback used to mask the common "I switched provider
    # in the UI but forgot to update the model" case: a user configured for
    # Anthropic could query ``gpt-4o`` and get a quote computed off the
    # OpenAI table. Cost limits built on that number would lie about what
    # the next real run will spend. So if the model isn't priced under the
    # configured provider, we surface the misconfiguration as a 400 with
    # both pieces of context the operator needs to fix it.
    provider_name, _, _ = _effective_provider(settings)
    try:
        usd = pricing.estimate_usd(
            provider_name,
            model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    except pricing.UnknownModelError:
        other_provider = _provider_pricing_model(model, exclude=provider_name)
        if other_provider is not None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"model {model!r} is not configured for provider "
                    f"{provider_name!r}; configure the {other_provider!r} "
                    f"provider or query a known {provider_name} model"
                ),
            ) from None
        raise HTTPException(
            status_code=400,
            detail=(
                f"no pricing for model {model!r} under provider "
                f"{provider_name!r}; add it to deep/pricing.py"
            ),
        ) from None

    return CostEstimateResponse(
        estimated_tokens=input_tokens + output_tokens,
        estimated_usd=usd,
        model=model,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _effective_provider(settings: Settings) -> tuple[str, str, str | None]:
    """Read the on-disk Settings' default provider triple.

    Returns ``(provider_name, model, judge_model)``. The in-memory
    override layer that the legacy ``PUT /api/settings`` populated is
    gone in contract v3; per-process tuning now goes through the
    profile-keyed API.
    """
    return settings.provider.name, settings.provider.model, settings.provider.judge_model


def _effective_cost(settings: Settings) -> tuple[float, float]:
    return float(settings.cost.per_run_usd), float(settings.cost.per_session_usd)


def _read_detected_providers(conn: sqlite3.Connection) -> list[ProviderEvidence]:
    """Read ``providers_detected`` rows and reify them as ProviderEvidence.

    The schema is created on every request by ``get_db``'s ``init_db``
    call, so the table is guaranteed to exist; an empty list is the
    expected response for a fresh project that hasn't run a scan yet.
    """
    out: list[ProviderEvidence] = []
    try:
        cur = conn.execute(
            """
            SELECT provider, source, file, line_start, key_var_name
            FROM providers_detected
            ORDER BY detected_at
            """
        )
    except sqlite3.OperationalError:
        # Defensive: should never trip after init_db, but failing soft
        # here keeps the settings endpoint healthy if the DDL ever
        # regresses mid-migration.
        return []
    rows = cur.fetchall()
    for row in rows:
        try:
            ev = ProviderEvidence(
                provider=_coerce_provider(str(row["provider"])),
                source=_coerce_source(str(row["source"])),
                location=CodeLocation(
                    file=str(row["file"]),
                    line_start=int(row["line_start"]),
                    line_end=int(row["line_start"]),  # not separately stored
                ),
                key_var_name=str(row["key_var_name"]),
            )
        except ValueError:
            # A row with an unrecognised provider/source enum value
            # shouldn't crash the whole endpoint.
            continue
        out.append(ev)
    return out


def _provider_pricing_model(model: str, *, exclude: str) -> str | None:
    """Return the *other* provider that prices *model*, or ``None``.

    Used to make the 400 we raise on provider/model mismatch actionable —
    if Anthropic is configured but the user queried ``gpt-4o``, the error
    message can name OpenAI as the provider they probably meant to switch
    to. We intentionally do **not** use this to compute a cost; the silent
    cross-provider fallback hid configuration bugs.
    """
    for candidate in ("anthropic", "openai"):
        if candidate == exclude:
            continue
        if model in pricing.known_models(candidate):
            return candidate
    return None


def _coerce_provider(name: str) -> Provider:
    """Map a string to the :class:`Provider` enum, defaulting to UNKNOWN."""
    try:
        return Provider(name)
    except ValueError:
        return Provider.UNKNOWN


def _coerce_source(value: str) -> Literal[".env", "config", "code"]:
    if value == ".env":
        return ".env"
    if value == "code":
        return "code"
    return "config"


def _load_template_text(conn: sqlite3.Connection, prompt_id: str) -> str | None:
    """Concatenate the template text of the latest version of *prompt_id*.

    Falls back to the ``prompts.payload_json`` if no ``prompt_versions``
    row exists yet (the scanner inserts into ``prompts`` but not
    ``prompt_versions``).
    """
    cur = conn.execute(
        """
        SELECT template_json
        FROM prompt_versions
        WHERE prompt_id = ?
        ORDER BY version DESC
        LIMIT 1
        """,
        (prompt_id,),
    )
    row = cur.fetchone()
    if row is not None:
        return _flatten_template_json(cast(str, row["template_json"]))
    cur = conn.execute("SELECT payload_json FROM prompts WHERE id = ?", (prompt_id,))
    prompt_row = cur.fetchone()
    if prompt_row is None:
        return None
    payload = cast(str, prompt_row["payload_json"])
    return _flatten_payload_json(payload)


def _flatten_template_json(raw: str) -> str:
    """Collapse a list-of-Message JSON into a single newline-joined string."""
    try:
        data: object = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if not isinstance(data, list):
        return raw
    parts: list[str] = []
    for msg in cast(list[object], data):
        if not isinstance(msg, dict):
            continue
        msg_dict = cast(dict[str, object], msg)
        text = msg_dict.get("template_text") or msg_dict.get("content") or ""
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def _flatten_payload_json(raw: str) -> str:
    """Pull template_text out of a PromptSite.model_dump_json payload."""
    try:
        data: object = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    if not isinstance(data, dict):
        return ""
    messages = cast(dict[str, object], data).get("messages")
    if not isinstance(messages, list):
        return ""
    parts: list[str] = []
    for msg in cast(list[object], messages):
        if not isinstance(msg, dict):
            continue
        text = cast(dict[str, object], msg).get("template_text", "")
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


__all__ = ["router"]
