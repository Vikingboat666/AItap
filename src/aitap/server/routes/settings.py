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
import logging
import sqlite3
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException

from aitap import secrets as secrets_module
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
    ProviderKeyStatus,
    SetKeyRequest,
    SettingsResponse,
    SettingsUpdate,
    TestKeyResponse,
)

# Local import to avoid the circular profiles ↔ settings dance at module
# load. The profiles router owns the canonical ``_defaults`` cache and
# exposes ``current_defaults`` / ``set_defaults`` helpers we delegate to.
from aitap.server.routes import profiles as profiles_routes
from aitap.server.routes._deps import get_db, get_settings

router = APIRouter(tags=["settings"])

_LOGGER = logging.getLogger(__name__)

# Provider-specific plain-language messages for the connectivity test.
# Centralised so en/zh stay aligned with the i18n test discipline — the
# Chinese surface comes from the React layer's translation table; here
# we hand back the English form (the i18n layer renders the localised
# label and the API-detail line as a fallback / details disclosure).
_TEST_OK_DETAIL = "The {provider} key works. You can run prompts that use it."
_TEST_AUTH_DETAIL = (
    "{provider} rejected the key. Check it in Settings — the value may be "
    "wrong, revoked, or for a different organisation."
)
_TEST_RATE_DETAIL = (
    "{provider} accepted the key but is rate-limiting you. Wait a moment and try again."
)
_TEST_NETWORK_DETAIL = (
    "Aitap couldn't reach {provider}. Check your network connection and try again."
)
_TEST_OTHER_DETAIL = (
    "{provider} returned an unexpected response. Try again; if it keeps "
    "happening, capture the timestamp and contact your admin."
)
_TEST_NO_KEY_DETAIL = "No {provider} key is set. Add one in Settings before testing."
_TEST_SDK_MISSING_DETAIL = (
    # NOTE: deliberately static — never interpolate the wrapped
    # ``ProviderError.__str__`` into the API body. SDK exception strings
    # have historically embedded request payloads (including auth headers)
    # on 4xx; we do not give them a wire path out of the server.
    "Aitap can't talk to {provider} on this machine. The provider SDK may not "
    "be installed — try `pip install 'aitap[{slug}]'`."
)

# Display names for use in user-facing copy. ``"openai".title()`` produces
# ``"Openai"``, which reads like a typo. The map keeps the canonical
# capitalisations consistent across the CLI, API, and UI.
_PRETTY_PROVIDER_NAMES: dict[str, str] = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
}


def _pretty_provider(provider: str) -> str:
    """Return ``provider`` in its canonical display form (e.g. ``OpenAI``)."""
    return _PRETTY_PROVIDER_NAMES.get(provider, provider)


# In-memory override store. Keyed by attribute name -> new value. The
# YAML persister below is the durable mirror; this dict keeps the current
# process in sync without a full ``Settings`` reload.
_MUTABLE_STATE: dict[str, object] = {}


def _persist_provider_defaults_to_yaml(
    settings: Settings,
    *,
    provider: str | None,
    model: str | None,
    judge_model: str | None,
) -> None:
    """Write the provider defaults back to ``.aitap/config.yaml``.

    Without this, a PUT to ``/api/settings`` would only live in the
    in-memory ``_MUTABLE_STATE`` and revert on the next ``aitap ui``
    restart — which would look like the UI silently forgot the user's
    choice. We round-trip through PyYAML; comments are not preserved
    (a small price for not adding ruamel.yaml as a hard dep).

    Only the three fields the Settings page exposes are touched; cost
    limits and anything else in the file are left intact. If the file
    doesn't exist (dev install that never ran ``aitap init`` in this
    directory), we silently skip — the runtime override still applies
    for the rest of this process.
    """
    import yaml  # local import: only this helper needs PyYAML.

    config_path = settings.project_root / settings.aitap_dir / "config.yaml"
    if not config_path.is_file():
        _LOGGER.info(
            "No .aitap/config.yaml at %s — defaults change kept in memory only",
            config_path,
        )
        return

    try:
        raw = config_path.read_text(encoding="utf-8")
        loaded = yaml.safe_load(raw)
    except (OSError, yaml.YAMLError):
        _LOGGER.warning(
            "Couldn't read .aitap/config.yaml; defaults change kept in memory only",
            exc_info=True,
        )
        return

    data: dict[str, object] = loaded if isinstance(loaded, dict) else {}
    raw_provider = data.get("provider")
    provider_block: dict[str, object] = raw_provider if isinstance(raw_provider, dict) else {}
    data["provider"] = provider_block

    if provider is not None:
        provider_block["name"] = provider
    if model is not None:
        provider_block["model"] = model
    if judge_model is not None:
        # An explicit empty string from the UI means "fall back to
        # ``model``" — same semantics as the default YAML stub where
        # ``judge_model: null``.
        provider_block["judge_model"] = judge_model if judge_model.strip() else None

    try:
        config_path.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    except OSError:
        _LOGGER.warning(
            "Couldn't write .aitap/config.yaml; defaults change kept in memory only",
            exc_info=True,
        )


@router.get("/settings", response_model=SettingsResponse)
def get_settings_endpoint(
    settings: Annotated[Settings, Depends(get_settings)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> SettingsResponse:
    """Render the effective :class:`Settings` + detected providers as JSON.

    The ``keys`` field is additive (CONTRACTS.md): each entry reports
    the per-provider ``{configured, source, masked}`` triple from
    :mod:`aitap.secrets`. The raw key value is never exposed.
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
        keys=_collect_key_statuses(),
        defaults=profiles_routes.current_defaults(settings),
    )


@router.put("/settings", response_model=SettingsResponse)
def put_settings(
    payload: SettingsUpdate,
    settings: Annotated[Settings, Depends(get_settings)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
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
        # Empty string from the UI means "fall back to the default
        # model" — normalise to None so :class:`ProviderConfig` reads it
        # as the documented sentinel.
        _MUTABLE_STATE["judge_model"] = payload.judge_model if payload.judge_model.strip() else None
    if payload.cost_per_run_usd is not None:
        _MUTABLE_STATE["cost_per_run_usd"] = float(payload.cost_per_run_usd)
    if payload.cost_per_session_usd is not None:
        _MUTABLE_STATE["cost_per_session_usd"] = float(payload.cost_per_session_usd)

    # Persist the provider triple to .aitap/config.yaml so the change
    # survives the next ``aitap ui`` restart. Cost limits are not
    # persisted from the UI yet (they're API-only — separate follow-up).
    if any(f is not None for f in (payload.provider, payload.model, payload.judge_model)):
        _persist_provider_defaults_to_yaml(
            settings,
            provider=payload.provider.value if payload.provider is not None else None,
            model=payload.model,
            judge_model=payload.judge_model,
        )

    return get_settings_endpoint(settings, conn)


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


@router.post("/settings/key", response_model=ProviderKeyStatus)
def set_provider_key(payload: SetKeyRequest) -> ProviderKeyStatus:
    """Persist *payload.key* for *payload.provider*.

    The response body is intentionally a :class:`ProviderKeyStatus` —
    metadata only. We never echo the submitted key (not in the response,
    not in the log filter, not in the SQLite store). The client should
    immediately drop the typed-key React state on success and rely on
    the returned masked preview.
    """
    try:
        status = secrets_module.set_key(
            payload.provider,
            payload.key,
            use_fallback=payload.use_fallback,
        )
    except ValueError as exc:
        # Plain-language remediation, no stack trace, no key.
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from None
    except secrets_module.KeyringUnavailableError:
        # The OS keyring isn't usable on this machine (no Secret Service
        # daemon, locked Keychain, etc.). The user hasn't opted into the
        # file fallback yet; surface a 409 so the UI can show a confirm
        # dialog and re-POST with use_fallback=True. The detail is a
        # complete, plain-language sentence — no stack trace, no key,
        # and crucially no internal exception message that could change
        # across keyring versions.
        raise HTTPException(
            status_code=409,
            detail=(
                "Aitap can't reach your system keychain on this machine. "
                "Save the key to a file in your home folder instead? "
                "It will be readable only by you."
            ),
        ) from None
    return _to_api_key_status(status)


@router.delete("/settings/key/{provider}", response_model=ProviderKeyStatus)
def delete_provider_key(provider: str) -> ProviderKeyStatus:
    """Delete *provider*'s key from every store aitap manages.

    Real delete (``keyring.delete_password`` / fallback-file entry
    removal), not an overwrite. The response reflects whatever the
    resolver sees afterwards — which may be ``source='env'`` if the
    user also has the env var set; the UI uses that signal to remind
    them to clear their shell config.
    """
    if provider not in secrets_module.supported_providers():
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown provider {provider!r}. Supported: "
                + ", ".join(secrets_module.supported_providers())
                + "."
            ),
        )
    status = secrets_module.delete_key(provider)  # type: ignore[arg-type]
    return _to_api_key_status(status)


@router.post("/settings/test/{provider}", response_model=TestKeyResponse)
async def test_provider_key(provider: str) -> TestKeyResponse:
    """Probe *provider* with one minimal LLM call to confirm the key works.

    Anthropic: ``/v1/messages`` with ``[{"role":"user","content":"ping"}]``
    and ``max_tokens=4``. OpenAI: the equivalent ``chat.completions`` call.
    The response is a :class:`TestKeyResponse` — never the raw key,
    never a stack trace, never a status code in the message. The
    ``detail`` field is the plain-language sentence the UI surfaces in
    the test card.
    """
    if provider not in secrets_module.supported_providers():
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown provider {provider!r}. Supported: "
                + ", ".join(secrets_module.supported_providers())
                + "."
            ),
        )

    # Bail early if there's nothing to test — saves a round-trip.
    status = secrets_module.key_status(provider)  # type: ignore[arg-type]
    if not status.configured:
        return TestKeyResponse(
            ok=False,
            reason="auth",
            detail=_TEST_NO_KEY_DETAIL.format(provider=_pretty_provider(provider)),
        )

    return await _run_connectivity_probe(provider)


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


def _collect_key_statuses() -> list[ProviderKeyStatus]:
    """Snapshot the per-provider key state for ``GET /api/settings``.

    Reads through :mod:`aitap.secrets` only via :func:`key_status` —
    the raw key never enters this code path.
    """
    return [
        _to_api_key_status(secrets_module.key_status(provider))
        for provider in secrets_module.supported_providers()
    ]


def _to_api_key_status(status: secrets_module.KeyStatus) -> ProviderKeyStatus:
    """Convert the vault's dataclass into the pydantic API model.

    Both shapes are intentionally identical; the conversion is a single
    function so future contract drift is localised.
    """
    return ProviderKeyStatus(
        provider=status.provider,
        configured=status.configured,
        source=status.source,
        masked=status.masked,
    )


async def _run_connectivity_probe(provider: str) -> TestKeyResponse:
    """Issue one minimal chat call to confirm the key works.

    We import the LLM client lazily so a missing optional SDK doesn't
    crash the route at import time — the user sees a plain-language
    "other" detail instead. ``ProviderAuthError`` / ``RateLimitError``
    map to the obvious ``reason`` slots; anything else lands in
    ``"other"``.
    """
    from aitap.deep.client import (
        ChatMessage,
        ProviderAuthError,
        ProviderError,
        ProviderRateLimitError,
        get_client,
    )

    # Pick a small, modern default model per provider so the probe
    # actually completes. The user's configured model may not exist on
    # the provider's most-restrictive tier; we pick the cheapest one
    # known to be widely available.
    probe_models: dict[str, str] = {
        "anthropic": "claude-3-5-haiku-20241022",
        "openai": "gpt-4o-mini",
    }
    model = probe_models.get(provider, "unknown")
    pretty = _pretty_provider(provider)

    try:
        client = get_client(provider, model)
    except ProviderError:
        # Most likely "install the SDK". We deliberately do NOT interpolate
        # the wrapped exception string into the response — historically
        # provider SDKs have embedded request payloads (including auth
        # headers) into their exception messages on 4xx responses. The
        # detail stays static; the actual cause goes to the logs (the
        # secret log filter strips any leaked key from the traceback).
        _LOGGER.warning("probe for %s failed at client construction", provider, exc_info=True)
        return TestKeyResponse(
            ok=False,
            reason="other",
            detail=_TEST_SDK_MISSING_DETAIL.format(provider=pretty, slug=provider),
        )

    try:
        await client.chat(
            [ChatMessage(role="user", content="ping")],
            max_tokens=4,
        )
    except ProviderAuthError:
        return TestKeyResponse(
            ok=False,
            reason="auth",
            detail=_TEST_AUTH_DETAIL.format(provider=pretty),
        )
    except ProviderRateLimitError:
        return TestKeyResponse(
            ok=False,
            reason="rate_limit",
            detail=_TEST_RATE_DETAIL.format(provider=pretty),
        )
    except ProviderError:
        # Catches the generic ``ProviderError`` we wrap network-level
        # SDK failures with. We treat unknown-cause errors as network
        # for the UX (it's the most actionable category).
        return TestKeyResponse(
            ok=False,
            reason="network",
            detail=_TEST_NETWORK_DETAIL.format(provider=pretty),
        )
    except Exception:
        # Final safety net so a bad SDK upgrade doesn't blow up the
        # route. We log with ``exc_info=True`` so the cause is at least
        # debuggable; the secret log filter strips any leaked key from
        # the formatted traceback before it reaches an output handler.
        _LOGGER.warning("probe for %s failed with unexpected error", provider, exc_info=True)
        return TestKeyResponse(
            ok=False,
            reason="other",
            detail=_TEST_OTHER_DETAIL.format(provider=pretty),
        )

    return TestKeyResponse(
        ok=True,
        reason=None,
        detail=_TEST_OK_DETAIL.format(provider=pretty),
    )


__all__ = ["router"]
