"""HTTP routes for ``/api/settings`` and ``/api/settings/cost-estimate``.

The settings surface is the bridge between the long-lived YAML config
(``.aitap/config.yaml``) and the UI. Wave 3 exposes:

- ``GET /api/settings`` — what the process is currently running with,
  plus any detected providers from the SQLite ``providers_detected`` table
  (populated by the scanner's L1 env inspector).
- ``PUT /api/settings`` — partial update; in-memory only for now (M2 will
  persist back to YAML once the scanner round-trip story stabilises).
- ``GET /api/settings/cost-estimate`` — pricebook-driven dry run cost for
  a given prompt + model, using :mod:`aitap.deep.pricing` and a rough
  4-chars-per-token estimator.

The mutation endpoint is intentionally light in Wave 3: the process holds
the override in a module-level ``_MUTABLE_STATE`` dict so subsequent GETs
return the new value. The persistence story lands when the prompts API
worktree (``wt/api-prompts``) is wired and we have a canonical place for
config writes.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Literal, cast

from fastapi import APIRouter, HTTPException

from aitap.config import Settings
from aitap.deep import pricing
from aitap.scanner.models import (
    CodeLocation,
    Provider,
    ProviderEvidence,
)
from aitap.server.deps import SettingsDep, get_conn
from aitap.server.routes import (
    CostEstimateResponse,
    SettingsResponse,
    SettingsUpdate,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])


# In-memory override store. Keyed by attribute name → new value. Wiping the
# process resets — by design for Wave 3 where YAML persistence is owned by
# the prompts API worktree.
_MUTABLE_STATE: dict[str, object] = {}


@router.get("", response_model=SettingsResponse)
def get_settings_endpoint(settings: SettingsDep) -> SettingsResponse:
    """Render the effective :class:`Settings` + detected providers as JSON."""
    provider_name, model, judge_model = _effective_provider(settings)
    cost = _effective_cost(settings)
    providers = _read_detected_providers(settings)
    return SettingsResponse(
        provider=_coerce_provider(provider_name),
        model=model,
        judge_model=judge_model,
        cost_per_run_usd=cost[0],
        cost_per_session_usd=cost[1],
        providers_available=providers,
    )


@router.put("", response_model=SettingsResponse)
def put_settings(
    payload: SettingsUpdate,
    settings: SettingsDep,
) -> SettingsResponse:
    """Apply a partial settings update.

    Only the fields explicitly set on ``payload`` overwrite state — None
    values are treated as "leave unchanged" so the UI can PATCH a single
    field without resending the rest. The merged state is reflected back
    in the response so the frontend doesn't need a second GET.
    """
    if payload.provider is not None:
        _MUTABLE_STATE["provider"] = payload.provider.value
    if payload.model is not None:
        _MUTABLE_STATE["model"] = payload.model
    if payload.judge_model is not None:
        _MUTABLE_STATE["judge_model"] = payload.judge_model
    if payload.cost_per_run_usd is not None:
        _MUTABLE_STATE["cost_per_run_usd"] = float(payload.cost_per_run_usd)
    if payload.cost_per_session_usd is not None:
        _MUTABLE_STATE["cost_per_session_usd"] = float(payload.cost_per_session_usd)
    return get_settings_endpoint(settings)


@router.get("/cost-estimate", response_model=CostEstimateResponse)
def get_cost_estimate(
    prompt_id: str,
    model: str,
    settings: SettingsDep,
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
    template_text = _load_template_text(settings, prompt_id)
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
    """Merge the on-disk Settings with the in-memory override layer.

    Returns ``(provider_name, model, judge_model)``.
    """
    provider = cast(str, _MUTABLE_STATE.get("provider", settings.provider.name))
    model = cast(str, _MUTABLE_STATE.get("model", settings.provider.model))
    judge_raw = _MUTABLE_STATE.get("judge_model", settings.provider.judge_model)
    judge_model = cast("str | None", judge_raw)
    return provider, model, judge_model


def _effective_cost(settings: Settings) -> tuple[float, float]:
    per_run = float(cast(float, _MUTABLE_STATE.get("cost_per_run_usd", settings.cost.per_run_usd)))
    per_session = float(
        cast(float, _MUTABLE_STATE.get("cost_per_session_usd", settings.cost.per_session_usd))
    )
    return per_run, per_session


def _read_detected_providers(settings: Settings) -> list[ProviderEvidence]:
    """Read ``providers_detected`` rows and reify them as ProviderEvidence.

    The DB may not exist yet (fresh project, no scan yet); we treat that
    as "no providers detected" rather than 500ing the settings endpoint.
    """
    if not settings.db_path.exists():
        return []
    out: list[ProviderEvidence] = []
    with get_conn(settings) as conn:
        try:
            cur = conn.execute(
                """
                SELECT provider, source, file, line_start, key_var_name
                FROM providers_detected
                ORDER BY detected_at
                """
            )
        except sqlite3.OperationalError:
            # init_db() should have created the table — but defensively
            # return [] if it didn't (e.g., migration mid-flight).
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


def _load_template_text(settings: Settings, prompt_id: str) -> str | None:
    """Concatenate the template text of the latest version of *prompt_id*.

    Falls back to the ``prompts.payload_json`` if no ``prompt_versions``
    row exists yet (the scanner inserts into ``prompts`` but not
    ``prompt_versions``).
    """
    if not settings.db_path.exists():
        return None
    with get_conn(settings) as conn:
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
        return raw
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
