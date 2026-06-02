"""HTTP routes for ``/api/profiles`` — the multi-provider redesign surface.

Endpoint inventory:

- ``GET /api/profiles`` → list of :class:`Profile` (config + per-row
  key-status triple).
- ``POST /api/profiles`` → create one profile from the upsert body;
  slugifies the label into the id, persists the metadata + (optional)
  key, returns the rendered :class:`Profile`.
- ``PUT /api/profiles/{profile_id}`` → mutate an existing profile's
  label/base_url/protocol/model_id/notes/key. The id is **immutable**
  — relabelling does not change it, so the keyring entry stays
  attached. 404 when the id doesn't exist.
- ``DELETE /api/profiles/{profile_id}`` → remove the profile + its
  keyring/fallback entry. If the deleted id was the active
  ``defaults.model_profile_id`` or ``defaults.judge_profile_id``, the
  corresponding default is auto-nulled (Decision 1 in
  ``docs/profiles-design.md``). The response carries a plain-language
  ``detail`` line so the UI can render the "we cleared your default"
  toast.
- ``POST /api/profiles/{profile_id}/test`` → connectivity probe. The
  handler resolves the key from the vault, builds a per-profile
  :class:`~aitap.deep.client.LLMClient` via
  :func:`~aitap.deep.factory.get_client_for_profile`, and sends one
  ``ping``-shaped chat call (Decision 3 in
  ``docs/profiles-design.md``). Errors map to the four documented
  reason slots (``auth`` / ``rate_limit`` / ``network`` / ``other``);
  the response detail is always plain-language and never includes the
  raw SDK exception string (B2 security regression from PR #35).

All endpoints honour the same security contract as the legacy key
routes (PR #35): the raw key never appears in any response body, log
line, or persisted file outside the OS keyring / opt-in
``~/.aitap/secrets.yaml``. Validation errors and conflict states
surface as plain-language sentences per CLAUDE.md.

The slugify algorithm is the one nailed down in Decision 2 of the
design doc: NFKD strip diacritics, lower-case ASCII, keep
``[a-z0-9_-]``, collapse repeated ``-``, append ``-2``/``-3``/... on
collision with an existing id. The helper :func:`slugify_label` is
exposed so the test module can pin the corner cases directly.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from aitap import secrets as secrets_module
from aitap.config import DefaultsConfig, ProfileConfig, Settings
from aitap.config_io import load_profiles_from_yaml, save_profiles_to_yaml
from aitap.deep.client import (
    ChatMessage,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
)
from aitap.deep.factory import get_client_for_profile
from aitap.server.routes import (
    Defaults,
    Profile,
    ProfileTestResponse,
    ProfileUpsertRequest,
)
from aitap.server.routes._deps import get_settings

router = APIRouter(tags=["profiles"])

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mutable in-process state
# ---------------------------------------------------------------------------
#
# Symmetric with ``settings.py::_MUTABLE_STATE``: lets the route layer
# react to a PUT/POST/DELETE without round-tripping through YAML every
# request. The persistence helpers in :mod:`aitap.config_io` are the
# durable mirror — every mutator calls both so a server restart picks
# up the same view the previous process had.
_PROFILES: list[ProfileConfig] = []
_defaults: DefaultsConfig = DefaultsConfig()
_initialised = False


def _ensure_loaded(settings: Settings) -> None:
    """Lazy-load the on-disk profiles into the in-process state.

    We don't load eagerly at import time because :class:`Settings` may
    not be fully configured yet (test fixtures override
    ``get_settings`` after the module is imported). First request wins;
    subsequent requests just see the cached view.
    """
    global _initialised
    if _initialised:
        return
    loaded_profiles, loaded_defaults = load_profiles_from_yaml(settings)
    _PROFILES[:] = loaded_profiles
    global _defaults
    _defaults = loaded_defaults
    _initialised = True


def reset_state_for_tests() -> None:
    """Drop the in-process cache. Called by route tests between cases."""
    global _initialised, _defaults
    _PROFILES.clear()
    _defaults = DefaultsConfig()
    _initialised = False


# ---------------------------------------------------------------------------
# Slugify (Decision 2 in profiles-design.md)
# ---------------------------------------------------------------------------


_NON_SLUG_CHARS = re.compile(r"[^a-z0-9_-]+")
_REPEATED_DASHES = re.compile(r"-{2,}")


def _normalise_label_to_slug(label: str) -> str:
    """Strip diacritics, lower-case, keep ``[a-z0-9_-]``, collapse dashes.

    Pure transform — the collision-resolution loop lives in
    :func:`slugify_label`. We split the steps so unit tests can pin the
    "what does this label normalise to" case independently of the
    "what does it become given the current pool" case.
    """
    # NFKD: decompose combined characters so the diacritic falls off as
    # a combining mark we can drop.
    decomposed = unicodedata.normalize("NFKD", label)
    ascii_only = decomposed.encode("ascii", errors="ignore").decode("ascii")
    lowered = ascii_only.lower()
    # Replace any remaining non-slug character with a dash; this is
    # what turns whitespace and punctuation into separators. Underscore
    # is kept because the design doc lists it as legal.
    candidate = _NON_SLUG_CHARS.sub("-", lowered)
    # Collapse runs of dashes introduced by the previous step, then
    # trim leading/trailing dashes so we never produce ``-foo-`` as an
    # id (would look ugly in keyring tooling).
    candidate = _REPEATED_DASHES.sub("-", candidate).strip("-")
    return candidate


def slugify_label(label: str, *, existing_ids: set[str]) -> str:
    """Return a fresh slug derived from *label* that doesn't collide.

    The base slug comes from :func:`_normalise_label_to_slug`. If that
    string is empty (the label was pure non-ASCII or punctuation), we
    fall back to ``profile`` so the user still gets a working id; the
    UI surfaces the auto-generated value so they can rename if they
    care. Collisions append ``-2``, ``-3``, … (per Decision 2).
    """
    base = _normalise_label_to_slug(label) or "profile"
    if base not in existing_ids:
        return base
    counter = 2
    while f"{base}-{counter}" in existing_ids:
        counter += 1
    return f"{base}-{counter}"


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _render_profile(config: ProfileConfig) -> Profile:
    """Materialise a :class:`Profile` with its current key status.

    The status triple comes from :mod:`aitap.secrets` — the raw key
    never leaves the vault. ``Profile.key_source`` is intentionally
    narrower than :class:`ProviderKeyStatus.source`: profile-id keys
    only ever come from the keyring or the opt-in fallback file (env
    vars are tied to provider *names*, not profile ids), so ``"env"``
    is not a legal value here.
    """
    status = secrets_module.key_status_for_profile(config.id)
    return Profile(
        id=config.id,
        label=config.label,
        base_url=config.base_url,
        protocol=config.protocol,
        model_id=config.model_id,
        notes=config.notes,
        key_configured=status.configured,
        key_source=status.source,
        key_masked=status.masked,
    )


def _find_profile_or_404(profile_id: str) -> ProfileConfig:
    for entry in _PROFILES:
        if entry.id == profile_id:
            return entry
    # Plain-language 404 — names the next action (CLAUDE.md).
    raise HTTPException(
        status_code=404,
        detail=(
            f"No profile with id {profile_id!r}. Refresh the Settings page; "
            "it may have been deleted in another tab."
        ),
    )


def _persist(settings: Settings) -> None:
    """Write the current in-memory view back to YAML, log on failure."""
    saved = save_profiles_to_yaml(settings, _PROFILES, _defaults)
    if not saved:
        # save_profiles_to_yaml already logged a WARNING; the route
        # still returns 200 because the in-process cache is the source
        # of truth for the rest of this server lifetime.
        _LOGGER.debug("profile persistence skipped (in-memory only)")


# ---------------------------------------------------------------------------
# Defaults — read / write across module boundaries
#
# These helpers are the only public surface the settings router uses to
# read / mutate ``_defaults``. Keeping the mutation inside this module
# means the in-process cache + the YAML mirror stay in lockstep
# regardless of which router triggered the change.
# ---------------------------------------------------------------------------


def current_defaults(settings: Settings) -> Defaults:
    """Return the active defaults as the API-shape :class:`Defaults` model."""
    _ensure_loaded(settings)
    return Defaults(
        model_profile_id=_defaults.model_profile_id,
        judge_profile_id=_defaults.judge_profile_id,
    )


def set_defaults(settings: Settings, defaults: Defaults) -> Defaults:
    """Validate references against the configured profiles + persist.

    Raises :class:`HTTPException` 422 with a plain-language detail when
    either ``model_profile_id`` or ``judge_profile_id`` points at a
    profile id that doesn't exist. Honours ``None`` on either field as
    the documented "no default chosen" sentinel.
    """
    _ensure_loaded(settings)
    known_ids = {p.id for p in _PROFILES}

    if defaults.model_profile_id is not None and defaults.model_profile_id not in known_ids:
        raise HTTPException(
            status_code=422,
            detail=(
                f"No profile with id {defaults.model_profile_id!r}. "
                "Open Settings and pick a default model from the list."
            ),
        )
    if defaults.judge_profile_id is not None and defaults.judge_profile_id not in known_ids:
        raise HTTPException(
            status_code=422,
            detail=(
                f"No profile with id {defaults.judge_profile_id!r}. "
                "Open Settings and pick a judge model from the list, "
                "or leave it blank to reuse the default model."
            ),
        )

    global _defaults
    _defaults = DefaultsConfig(
        model_profile_id=defaults.model_profile_id,
        judge_profile_id=defaults.judge_profile_id,
    )
    _persist(settings)
    return current_defaults(settings)


def _set_profile_key(profile_id: str, api_key: str, *, use_fallback: bool) -> None:
    """Write *api_key* via the secrets vault, mapping errors to HTTP shapes.

    Centralised because both POST and PUT go through the same set-key
    path; keeps the 409 plain-language sentence consistent with the
    legacy :class:`SetKeyRequest` flow that the UI already speaks.
    """
    try:
        secrets_module.set_key_for_profile(profile_id, api_key, use_fallback=use_fallback)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except secrets_module.KeyringUnavailableError:
        # Same wording as ``settings.py::set_provider_key`` so the UI's
        # existing confirm-dialog handler matches against a single
        # detail string. The user re-POSTs with use_fallback=True.
        raise HTTPException(
            status_code=409,
            detail=(
                "Aitap can't reach your system keychain on this machine. "
                "Save the key to a file in your home folder instead? "
                "It will be readable only by you."
            ),
        ) from None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/profiles", response_model=list[Profile])
def list_profiles(
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[Profile]:
    """List every configured profile with its current key status."""
    _ensure_loaded(settings)
    return [_render_profile(p) for p in _PROFILES]


@router.post("/profiles", response_model=Profile, status_code=201)
def create_profile(
    payload: ProfileUpsertRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Profile:
    """Create a new profile, optionally storing its key in one shot.

    The slug is derived from the label per :func:`slugify_label`;
    collisions append ``-2``/``-3``/etc. If ``payload.api_key`` is
    present we set the key first, then persist the metadata — this
    ordering means a 409 from the keyring path doesn't leave a
    keyless-but-persisted profile lying around.
    """
    _ensure_loaded(settings)
    existing_ids = {p.id for p in _PROFILES}
    new_id = slugify_label(payload.label, existing_ids=existing_ids)

    if payload.api_key is not None:
        _set_profile_key(
            new_id,
            payload.api_key,
            use_fallback=payload.use_fallback,
        )

    config = ProfileConfig(
        id=new_id,
        label=payload.label,
        base_url=payload.base_url,
        protocol=payload.protocol,
        model_id=payload.model_id,
        notes=payload.notes,
    )
    _PROFILES.append(config)
    _persist(settings)
    return _render_profile(config)


@router.put("/profiles/{profile_id}", response_model=Profile)
def update_profile(
    profile_id: str,
    payload: ProfileUpsertRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Profile:
    """Mutate everything but the id on an existing profile.

    The id is immutable by contract — a relabel doesn't drift the
    keyring entry (which would orphan the user's key) and downstream
    references (``defaults.model_profile_id``, run history rows) stay
    valid. If the user wants a clean break, they delete + re-add.
    """
    _ensure_loaded(settings)
    existing = _find_profile_or_404(profile_id)

    if payload.api_key is not None:
        _set_profile_key(
            profile_id,
            payload.api_key,
            use_fallback=payload.use_fallback,
        )

    # Pydantic v2 immutables: build a new instance with the updated
    # fields rather than mutate in place. The id is preserved
    # explicitly (the upsert body does NOT carry one — we forbid the
    # field on the schema so a stale frontend can't try to rename).
    index = _PROFILES.index(existing)
    updated = ProfileConfig(
        id=profile_id,
        label=payload.label,
        base_url=payload.base_url,
        protocol=payload.protocol,
        model_id=payload.model_id,
        notes=payload.notes,
    )
    _PROFILES[index] = updated
    _persist(settings)
    return _render_profile(updated)


@router.delete("/profiles/{profile_id}", response_model=Profile)
def delete_profile(
    profile_id: str,
    settings: Annotated[Settings, Depends(get_settings)],
) -> Profile:
    """Remove a profile and (if needed) clear it from the defaults.

    Real delete: the keyring entry is removed (``delete_password``),
    the YAML row is dropped, and any :class:`DefaultsConfig` reference
    is auto-nulled per Decision 1. The response is the *final* shape
    of the profile (configured=False, source="none") so the UI can
    flip the row out of the list and update the defaults card in one
    state update.
    """
    _ensure_loaded(settings)
    existing = _find_profile_or_404(profile_id)

    # Real key delete first — if it fails (e.g. keyring crashed
    # mid-request) we still want the profile gone from the user's
    # view; the orphaned keyring entry is recoverable but a stuck
    # zombie row is not. ``delete_key_for_profile`` already swallows
    # PasswordDeleteError so the call won't raise.
    secrets_module.delete_key_for_profile(profile_id)

    _PROFILES.remove(existing)

    global _defaults
    cleared_model = _defaults.model_profile_id == profile_id
    cleared_judge = _defaults.judge_profile_id == profile_id
    if cleared_model or cleared_judge:
        _defaults = DefaultsConfig(
            model_profile_id=(None if cleared_model else _defaults.model_profile_id),
            judge_profile_id=(None if cleared_judge else _defaults.judge_profile_id),
        )
        # profile_id is a user-chosen slug (e.g. ``"deepseek-2"``), not
        # a secret — it's the same string that appears in URLs the user
        # navigates to. Log it directly so diagnostics stay actionable
        # ("which profile lost its default?"). The keyring entry the
        # delete just removed is keyed by this same id, so blanking it
        # here would defeat the log line's purpose.
        _LOGGER.info(
            "Cleared defaults referencing deleted profile %r (model=%s, judge=%s)",
            profile_id,
            cleared_model,
            cleared_judge,
        )

    _persist(settings)

    # Render from the now-detached config so the response still names
    # the profile that just went away. ``key_status_for_profile`` will
    # report "none" because we deleted the keyring entry above.
    return _render_profile(existing)


@router.post("/profiles/{profile_id}/test", response_model=ProfileTestResponse)
async def test_profile(
    profile_id: str,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ProfileTestResponse:
    """Connectivity probe for a profile's key.

    Resolves the key from the vault, builds a per-profile
    :class:`~aitap.deep.client.LLMClient` via
    :func:`~aitap.deep.factory.get_client_for_profile`, and issues a
    single minimal chat call: ``messages=[{role:"user", content:"ping"}]``
    with ``max_tokens=4`` (Decision 3 in ``docs/profiles-design.md`` —
    same shape for both protocols).

    Exception → reason mapping:

    - :class:`ProviderAuthError`     → ``"auth"`` — the key is wrong / revoked.
    - :class:`ProviderRateLimitError` → ``"rate_limit"`` — key works, just busy.
    - :class:`ProviderError`         → ``"network"`` — couldn't reach the host.
    - Anything else                  → ``"other"`` — log + opaque detail.

    None of the response detail strings ever include the raw exception
    message: SDK exceptions have historically embedded request payloads
    (Authorization header, body) in their ``str()`` (B2 regression from
    PR #35). The detail copy is static + plain-language; the maintainer
    sees the real exception in the log with ``exc_info=True``.

    The 404-on-missing-profile-id path is shared with the rest of the
    CRUD surface via :func:`_find_profile_or_404`.
    """
    _ensure_loaded(settings)
    profile_config = _find_profile_or_404(profile_id)

    # Resolve the key first — short-circuit before any factory work so
    # an unconfigured profile never reaches the SDK. ``get_key_for_profile``
    # is on the AST allow-list for this file (see
    # ``test_secrets_import_discipline.py``).
    api_key = secrets_module.get_key_for_profile(profile_id)
    if not api_key:
        return ProfileTestResponse(
            ok=False,
            reason="auth",
            detail="No key is set for this profile. Add one in Settings.",
        )

    # Render once so both the success and failure messages can name the
    # profile in a stable, user-friendly way. ``profile.label`` is the
    # user's free-text display name; we prefer it over ``id`` / a raw
    # provider name so a user with two "DeepSeek" rows can tell which
    # one rejected the key.
    profile = _render_profile(profile_config)
    label = profile.label

    try:
        client = get_client_for_profile(profile, api_key)
        # Minimal probe — same shape on both protocols per Decision 3.
        # max_tokens=4 keeps Anthropic happy (required field) and bills
        # essentially nothing on OpenAI-compatible endpoints.
        await client.chat(
            [ChatMessage(role="user", content="ping")],
            max_tokens=4,
        )
    except ProviderAuthError:
        # Plain-language: name the host + tell the user the next action
        # (open Settings). Three reasons it might have rejected (wrong /
        # revoked / wrong org) so the user has a checklist.
        return ProfileTestResponse(
            ok=False,
            reason="auth",
            detail=(
                f"{label} rejected the key. Check it in Settings — it may be "
                "wrong, revoked, or for a different organisation."
            ),
        )
    except ProviderRateLimitError:
        # Plain-language: the key works, the service is busy. "Wait and
        # try again" is the actionable next step.
        return ProfileTestResponse(
            ok=False,
            reason="rate_limit",
            detail=(
                f"{label} accepted the key but is rate-limiting you. Wait a moment and try again."
            ),
        )
    except ProviderError:
        # ProviderError without a more specific subclass means the SDK
        # couldn't talk to the host (DNS, TLS, timeout, connection refused
        # for a local Ollama, …). "Check your network" is the right next
        # action for the working developer audience.
        return ProfileTestResponse(
            ok=False,
            reason="network",
            detail=(f"Aitap couldn't reach {label}. Check your network and try again."),
        )
    except Exception:
        # Anything outside our taxonomy — including SDK-construction
        # failures and unexpected runtime errors — gets logged with
        # exc_info=True for the maintainer, but the user-facing detail
        # stays static. The B2 regression test in
        # ``test_routes_settings_keys.py`` confirms SDK exception strings
        # (which historically embedded auth headers) never reach the
        # response body.
        _LOGGER.warning(
            "profile test probe for %r raised an unexpected exception",
            profile_id,
            exc_info=True,
        )
        return ProfileTestResponse(
            ok=False,
            reason="other",
            detail=(
                f"{label} returned an unexpected response. Try again; if it keeps "
                "happening, capture the timestamp and contact your admin."
            ),
        )

    return ProfileTestResponse(
        ok=True,
        reason=None,
        detail=f"{label} is reachable. The key works.",
    )


__all__ = ["reset_state_for_tests", "router", "slugify_label"]
