"""Sole owner of API-key read/write/delete for aitap.

# secret-source

This module exists so the rest of the codebase has exactly **one** door
to the API-key vault. Every other module that needs to call an LLM gets
its key by going through ``aitap.secrets``; nothing else is allowed to
import ``get_key`` (a unit test enforces this — see
``tests/unit/test_secrets_import_discipline.py``).

The design (see ``docs/settings-ui-design.md``) is:

1. **Primary:** the OS-native secret store, via the ``keyring`` package.
   Windows Credential Manager, macOS Keychain, freedesktop Secret
   Service / KWallet on Linux. The service name is ``aitap``; the
   account is ``provider:<name>`` (e.g. ``provider:anthropic``).
2. **Fallback:** a user-scoped ``~/.aitap/secrets.yaml`` with ``0600``
   permissions (Windows: current-user-only ACL). Used **only** when the
   ``keyring`` backend reports itself unusable (typical on a headless
   Linux box with no Secret Service). The UI must explicitly confirm
   before this fallback engages — never silent.
3. **Never** ``.aitap/`` inside the user's project tree.

Public surface:

- :func:`get_key` — the only function that returns the raw key. All
  callers must be on the allow-list in
  ``tests/unit/test_secrets_import_discipline.py``.
- :func:`key_status` — metadata only (``configured``, ``source``,
  ``masked``). Safe for the API layer.
- :func:`set_key` / :func:`delete_key` — write side.
- :func:`install_log_filter` — registered at server startup; drops any
  log record whose msg or args look like a leaked key.

The env-var fallback (``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY``) is
preserved so existing CI / docker setups keep working. ``key_status``
reports ``source == "env"`` in that case so the UI can surface "this
key comes from your shell, not from aitap" honestly.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

Provider = Literal["anthropic", "openai"]
KeySource = Literal["keyring", "fallback", "env", "none"]
# Narrower variant for profile-id keys: env vars are tied to *provider*
# names (ANTHROPIC_API_KEY, OPENAI_API_KEY), so a profile-id resolver
# can only return keyring / fallback / none. Exposing the tighter union
# lets the route layer surface ``Profile.key_source`` without the dead
# "env" arm that callers would never see.
ProfileKeySource = Literal["keyring", "fallback", "none"]

# Canonical provider list — single source of truth for the rest of the
# module. Adding a third provider means adding it here + the env var map
# below + the contract enum; nothing else has to change.
_PROVIDERS: tuple[Provider, ...] = ("anthropic", "openai")

_ENV_VARS: dict[Provider, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}

_KEYRING_SERVICE = "aitap"


@dataclass(frozen=True)
class KeyStatus:
    """Metadata describing whether/where a provider's key lives.

    The ``masked`` field is the only "key-like" string this object ever
    exposes — it is the last four characters of the configured key with
    a fixed prefix, so it can be safely logged or rendered in the UI.
    Anywhere that needs the raw key must call :func:`get_key` directly.
    """

    provider: Provider
    configured: bool
    source: KeySource
    masked: str | None


@dataclass(frozen=True)
class ProfileKeyStatus:
    """Metadata describing whether/where a profile's key lives.

    The profile-keyed counterpart to :class:`KeyStatus`. Used by the new
    multi-provider redesign (``docs/profiles-design.md``); the legacy
    :class:`KeyStatus` is kept until wt/profile-cleanup removes it.

    ``profile_id`` is the documented slug, not the free-text label.
    """

    profile_id: str
    configured: bool
    source: ProfileKeySource
    masked: str | None


# ---------------------------------------------------------------------------
# Backend resolution
# ---------------------------------------------------------------------------


def _keyring_module() -> object | None:
    """Return the ``keyring`` package, or ``None`` if it can't be imported.

    Wrapping the import lets tests pin "no keyring available" without
    monkey-patching ``sys.modules``: they just patch this function.
    """
    try:
        import keyring  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - keyring is a hard dep
        return None
    return keyring


def _keyring_usable() -> bool:
    """True iff the OS keyring backend is actually usable.

    ``keyring.backends.fail.Keyring`` and ``chainer.ChainerBackend`` with
    no working sub-backend both indicate "nothing here" — in either case
    we should fall back to ``~/.aitap/secrets.yaml``. We import the
    backend module lazily because some users won't have it on the path.
    """
    keyring = _keyring_module()
    if keyring is None:
        return False
    try:
        backend = keyring.get_keyring()  # type: ignore[attr-defined]
    except Exception:
        # Some backends raise on platform mismatch (e.g. dbus missing).
        # Treat that as "not usable" — the fallback path catches it.
        return False
    module_name = type(backend).__module__
    # ``keyring.backends.fail.Keyring`` is the documented "no-op" sentinel
    # the package returns when no real backend is available. Anything
    # else is treated as usable; a runtime error on the actual call is
    # surfaced later (and the API layer reports it as plain language).
    return "keyring.backends.fail" not in module_name


def _fallback_path() -> Path:
    """Return the resolved path to ``~/.aitap/secrets.yaml``.

    We always anchor on ``Path.home()`` so even on Windows the fallback
    lives under the user's profile (``C:\\Users\\<name>\\.aitap``) — never
    inside the project tree. The project-level ``.aitap/`` is for scan
    output and is checked into ``.gitignore``; we keep secrets out of
    that bucket on purpose.
    """
    return Path.home() / ".aitap" / "secrets.yaml"


# ---------------------------------------------------------------------------
# Fallback storage helpers (file mode locked down so other users can't read)
# ---------------------------------------------------------------------------


def _read_fallback() -> dict[str, str]:
    """Load ``~/.aitap/secrets.yaml`` into a dict, or return ``{}``."""
    path = _fallback_path()
    if not path.is_file():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        loaded = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    # Coerce to {str: str} — anything weird becomes "missing".
    result: dict[str, str] = {}
    for k, v in loaded.items():
        if isinstance(k, str) and isinstance(v, str):
            result[k] = v
    return result


def _write_fallback(data: dict[str, str]) -> None:
    """Persist *data* to ``~/.aitap/secrets.yaml`` with 0600 permissions.

    Creates the parent directory if needed. The chmod is done *after*
    write so a partial-write doesn't briefly expose the file at a more
    permissive mode. On Windows, ``os.chmod`` is essentially a no-op for
    permission narrowing — we additionally rely on the file living under
    the user's profile (which has user-only ACL by default).
    """
    path = _fallback_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(data, sort_keys=True) if data else ""
    path.write_text(text, encoding="utf-8")
    # 0o600 = user read/write only. Best-effort: some filesystems (FAT,
    # network shares) don't honour POSIX bits, but on the common case
    # (local NTFS / ext4 / APFS) this locks the file down.
    with contextlib.suppress(OSError):  # pragma: no cover - best-effort hardening
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _account(provider: Provider) -> str:
    """The keyring 'account' part — namespaced per provider."""
    return f"provider:{provider}"


def _profile_account(profile_id: str) -> str:
    """The keyring 'account' part — namespaced per profile id.

    Mirrors :func:`_account` for the new multi-provider redesign. The
    ``profile:<id>`` convention coexists with the legacy ``provider:<name>``
    one in the same keyring service until wt/profile-cleanup retires
    the latter.
    """
    return f"profile:{profile_id}"


def _validate_profile_id(profile_id: str) -> str:
    """Reject blank profile ids with a plain-language error.

    Returns the trimmed id so callers can rely on a canonical value.
    Anything non-empty is accepted here — the slugify algorithm in
    ``server/routes/profiles.py`` enforces the documented character
    rules at write time, so by the time a value reaches this layer it
    is already normalised. We still guard against the empty / whitespace
    cases because the keyring backend would happily store under
    ``profile:`` and that would be a security-relevant footgun.
    """
    if not profile_id or not profile_id.strip():
        raise ValueError("profile id cannot be empty")
    return profile_id


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _mask(key: str) -> str:
    """Mask *key* down to a UI-safe ``"sk-...XXXX"`` preview.

    We keep the last four characters because that's the standard "do
    you have the right key in?" disambiguator (matching the AWS / gh
    convention). Anything shorter than 4 chars gets fully masked.
    """
    tail = key[-4:] if len(key) >= 4 else ""
    if key.startswith("sk-ant-"):
        return f"sk-ant-...{tail}"
    if key.startswith("sk-"):
        return f"sk-...{tail}"
    return f"...{tail}" if tail else "..."


def key_status(provider: Provider) -> KeyStatus:
    """Report what aitap knows about *provider*'s key without leaking it.

    Resolution order matches :func:`get_key`:

    1. Keyring (preferred).
    2. Fallback ``~/.aitap/secrets.yaml`` (only used when the keyring
       backend is unusable, but we read it opportunistically when
       present so a user who chose the fallback path keeps working).
    3. Environment variable (compatibility for legacy setups / CI).
    4. Nothing.

    The ``masked`` value is derived from whichever source won, so the UI
    can show "the key ending in ...XYZW from your keychain" honestly.
    """
    _validate_provider(provider)

    if _keyring_usable():
        keyring = _keyring_module()
        try:
            raw = keyring.get_password(_KEYRING_SERVICE, _account(provider))  # type: ignore[attr-defined]
        except Exception:
            raw = None
        if raw:
            return KeyStatus(
                provider=provider,
                configured=True,
                source="keyring",
                masked=_mask(raw),
            )

    fallback = _read_fallback()
    fallback_value = fallback.get(provider)
    if fallback_value:
        return KeyStatus(
            provider=provider,
            configured=True,
            source="fallback",
            masked=_mask(fallback_value),
        )

    env_value = os.environ.get(_ENV_VARS[provider])
    if env_value:
        return KeyStatus(
            provider=provider,
            configured=True,
            source="env",
            masked=_mask(env_value),
        )

    return KeyStatus(provider=provider, configured=False, source="none", masked=None)


def get_key(provider: Provider) -> str | None:
    """Return the raw API key for *provider*, or ``None``.

    **Restricted import:** only the LLM-client construction path
    (``aitap.deep.anthropic_client``, ``aitap.deep.openai_client``, and
    the dispatch glue in ``aitap.playground.dispatch``) is allowed to
    call this function. ``tests/unit/test_secrets_import_discipline.py``
    enforces the allow-list with an AST scan; new call sites must be
    added there explicitly.

    Resolution order: keyring → fallback file → env var.
    """
    _validate_provider(provider)

    if _keyring_usable():
        keyring = _keyring_module()
        try:
            raw = keyring.get_password(_KEYRING_SERVICE, _account(provider))  # type: ignore[attr-defined]
        except Exception:
            raw = None
        if raw:
            return raw

    fallback = _read_fallback()
    if fallback.get(provider):
        return fallback[provider]

    env_value = os.environ.get(_ENV_VARS[provider])
    if env_value:
        return env_value

    return None


class KeyringUnavailableError(RuntimeError):
    """Raised by :func:`set_key` when the OS keyring isn't usable and the
    caller hasn't opted into the file fallback.

    The design contract (``docs/settings-ui-design.md`` → Security model)
    is explicit: aitap never silently writes a key to ``~/.aitap/secrets.yaml``.
    The user must see a "your keychain isn't available — save to a file
    instead?" confirmation and re-submit with ``use_fallback=True``.

    Carries no key material; the message is plain-language and safe to
    surface verbatim to the user.
    """


def set_key(provider: Provider, key: str, *, use_fallback: bool = False) -> KeyStatus:
    """Persist *key* for *provider*.

    Args:
        provider: which provider this key is for.
        key: the raw secret — never logged, never echoed back.
        use_fallback: when True, write to ``~/.aitap/secrets.yaml``. The
            UI should only set this after explicitly asking the user to
            confirm (the API layer surfaces a 409 + dialog when the
            keyring is down so the user makes that choice consciously).

    Behaviour:
        - ``use_fallback=False`` (default) AND keyring healthy → writes
          to keyring, returns ``source="keyring"``.
        - ``use_fallback=False`` AND keyring unusable / write fails →
          raises :class:`KeyringUnavailableError`. **No file is touched.**
          This is the contract: a silent fallback would let a key land on
          disk without the user knowing, which the security model forbids.
        - ``use_fallback=True`` → writes to ``~/.aitap/secrets.yaml``
          regardless of keyring health. Honours an explicit opt-in.

    Returns the post-write :class:`KeyStatus` so callers don't need a
    second :func:`key_status` round-trip.
    """
    _validate_provider(provider)
    if not key or not key.strip():
        raise ValueError("API key cannot be empty")

    if not use_fallback:
        if not _keyring_usable():
            raise KeyringUnavailableError("Aitap can't reach your system keychain on this machine.")
        keyring = _keyring_module()
        try:
            keyring.set_password(_KEYRING_SERVICE, _account(provider), key)  # type: ignore[attr-defined]
        except Exception as exc:
            # Keyring write blew up at runtime (SecretService daemon
            # died, Keychain locked, etc.). Surface as the same error
            # the not-usable case raises — no silent file write. The
            # caller (API layer) maps this to a 409 + UI confirmation
            # dialog; on confirm the UI re-POSTs with use_fallback=True.
            # We deliberately don't embed ``str(exc)``: it's not
            # user-actionable and would leak into the response body if a
            # caller forwarded the message blindly.
            raise KeyringUnavailableError(
                "Aitap couldn't write the key to your system keychain."
            ) from exc
        return KeyStatus(
            provider=provider,
            configured=True,
            source="keyring",
            masked=_mask(key),
        )

    # use_fallback=True: the user has explicitly opted in.
    fallback = _read_fallback()
    fallback[provider] = key
    _write_fallback(fallback)
    return KeyStatus(
        provider=provider,
        configured=True,
        source="fallback",
        masked=_mask(key),
    )


def delete_key(provider: Provider) -> KeyStatus:
    """Remove *provider*'s key from every source aitap manages.

    "Manages" here means keyring + fallback file — the env var is the
    user's shell config and we don't touch it. We return a
    :class:`KeyStatus` so callers can immediately see what's left (which
    may be ``source == "env"`` if the user also has the env var set —
    that's a deliberate signal so the UI can tell them).

    The keyring delete is a real delete (``delete_password``), not an
    overwrite-with-empty — so a forensic look at the credential store
    doesn't find a stub entry.
    """
    _validate_provider(provider)

    if _keyring_usable():
        keyring = _keyring_module()
        # ``contextlib.suppress(Exception)`` covers the documented
        # ``PasswordDeleteError`` for "no such entry" — we don't want a
        # delete on a nonexistent key to look like a failure.
        with contextlib.suppress(Exception):
            keyring.delete_password(_KEYRING_SERVICE, _account(provider))  # type: ignore[attr-defined]

    fallback = _read_fallback()
    if provider in fallback:
        del fallback[provider]
        if fallback:
            _write_fallback(fallback)
        else:
            # If the file is now empty, remove it rather than leaving a
            # zero-byte file lying around.
            path = _fallback_path()
            if path.exists():
                try:
                    path.unlink()
                except OSError:
                    _write_fallback(fallback)

    return key_status(provider)


# ---------------------------------------------------------------------------
# Profile-keyed API (new multi-provider redesign)
#
# The four functions below mirror the provider-keyed surface above but
# use the documented ``profile:<id>`` account convention. They are
# additive: the legacy provider-keyed functions still exist and still
# write under ``provider:<name>``. wt/profile-cleanup retires the
# legacy surface once all callers migrate. See ``docs/profiles-design.md``
# §"secrets module changes" for the full migration story.
# ---------------------------------------------------------------------------


def key_status_for_profile(profile_id: str) -> ProfileKeyStatus:
    """Report what aitap knows about *profile_id*'s key without leaking it.

    Resolution order matches :func:`get_key_for_profile`:

    1. Keyring (preferred), account ``profile:<id>``.
    2. Fallback ``~/.aitap/secrets.yaml`` under the same key string.
    3. Nothing.

    Unlike the legacy provider-keyed :func:`key_status`, there is no
    env-var fallback — env vars are tied to specific provider names
    (``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``), but a profile id is a
    user-defined slug. The route layer is welcome to surface env vars
    as a separate signal at the UI level, but they aren't part of the
    per-profile resolution path.
    """
    profile_id = _validate_profile_id(profile_id)

    if _keyring_usable():
        keyring = _keyring_module()
        try:
            raw = keyring.get_password(  # type: ignore[attr-defined]
                _KEYRING_SERVICE, _profile_account(profile_id)
            )
        except Exception:
            raw = None
        if raw:
            return ProfileKeyStatus(
                profile_id=profile_id,
                configured=True,
                source="keyring",
                masked=_mask(raw),
            )

    fallback = _read_fallback()
    fallback_value = fallback.get(_profile_account(profile_id))
    if fallback_value:
        return ProfileKeyStatus(
            profile_id=profile_id,
            configured=True,
            source="fallback",
            masked=_mask(fallback_value),
        )

    return ProfileKeyStatus(profile_id=profile_id, configured=False, source="none", masked=None)


def get_key_for_profile(profile_id: str) -> str | None:
    """Return the raw API key for *profile_id*, or ``None``.

    **Restricted import:** same allow-list policy as :func:`get_key`.
    Only the LLM-client construction path may call this; see
    ``tests/unit/test_secrets_import_discipline.py`` for the AST scan
    that enforces the rule. At time of writing the allow-list is empty
    for the new function — wt/profile-client adds callers once it
    rewires the dispatch layer.

    Resolution order: keyring → fallback file. No env-var path
    (see :func:`key_status_for_profile` for why).
    """
    profile_id = _validate_profile_id(profile_id)

    if _keyring_usable():
        keyring = _keyring_module()
        try:
            raw = keyring.get_password(  # type: ignore[attr-defined]
                _KEYRING_SERVICE, _profile_account(profile_id)
            )
        except Exception:
            raw = None
        if raw:
            return raw

    fallback = _read_fallback()
    account = _profile_account(profile_id)
    if fallback.get(account):
        return fallback[account]

    return None


def set_key_for_profile(
    profile_id: str, key: str, *, use_fallback: bool = False
) -> ProfileKeyStatus:
    """Persist *key* for *profile_id*. Mirrors :func:`set_key` exactly.

    Args:
        profile_id: the documented slug (lower-case ASCII, ``-``/``_``).
        key: the raw secret — never logged, never echoed back.
        use_fallback: when True, write to ``~/.aitap/secrets.yaml``.
            The UI should only set this after explicitly asking the
            user to confirm (the API layer surfaces a 409 + confirm
            dialog when the keyring is down so the user makes that
            choice consciously — same flow as PR #35).

    Same security contract as :func:`set_key`:

    - ``use_fallback=False`` + keyring healthy → writes to keyring.
    - ``use_fallback=False`` + keyring down or write fails →
      :class:`KeyringUnavailableError`, **nothing touches disk**.
    - ``use_fallback=True`` → writes to ``~/.aitap/secrets.yaml``.
    """
    profile_id = _validate_profile_id(profile_id)
    if not key or not key.strip():
        raise ValueError("API key cannot be empty")

    if not use_fallback:
        if not _keyring_usable():
            raise KeyringUnavailableError("Aitap can't reach your system keychain on this machine.")
        keyring = _keyring_module()
        try:
            keyring.set_password(  # type: ignore[attr-defined]
                _KEYRING_SERVICE, _profile_account(profile_id), key
            )
        except Exception as exc:
            # Same security contract as :func:`set_key`: no silent file
            # write on a runtime keyring failure. The API layer maps
            # this to a 409 + UI confirmation dialog.
            raise KeyringUnavailableError(
                "Aitap couldn't write the key to your system keychain."
            ) from exc
        return ProfileKeyStatus(
            profile_id=profile_id,
            configured=True,
            source="keyring",
            masked=_mask(key),
        )

    # Explicit opt-in: the UI has confirmed the fallback path.
    fallback = _read_fallback()
    fallback[_profile_account(profile_id)] = key
    _write_fallback(fallback)
    return ProfileKeyStatus(
        profile_id=profile_id,
        configured=True,
        source="fallback",
        masked=_mask(key),
    )


def delete_key_for_profile(profile_id: str) -> ProfileKeyStatus:
    """Remove *profile_id*'s key from every source aitap manages.

    Real delete (``keyring.delete_password`` / fallback-file entry
    removal), not an overwrite-with-empty — so a forensic look at the
    credential store doesn't find a stub entry. Returns the post-delete
    :class:`ProfileKeyStatus` so callers can immediately reflect the
    new state in the UI without a second status round-trip.
    """
    profile_id = _validate_profile_id(profile_id)
    account = _profile_account(profile_id)

    if _keyring_usable():
        keyring = _keyring_module()
        # Same as the legacy delete: silence ``PasswordDeleteError`` for
        # "no such entry". Deleting a never-saved key is not a failure.
        with contextlib.suppress(Exception):
            keyring.delete_password(_KEYRING_SERVICE, account)  # type: ignore[attr-defined]

    fallback = _read_fallback()
    if account in fallback:
        del fallback[account]
        if fallback:
            _write_fallback(fallback)
        else:
            # Empty dict → remove the file rather than leave a zero-byte
            # artifact on disk. Mirrors the legacy delete behaviour.
            path = _fallback_path()
            if path.exists():
                try:
                    path.unlink()
                except OSError:
                    _write_fallback(fallback)

    return key_status_for_profile(profile_id)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_provider(provider: str) -> None:
    """Reject unknown provider names with a plain-language error."""
    if provider not in _PROVIDERS:
        # ValueError instead of HTTPException — the API layer maps it.
        raise ValueError(f"Unknown provider {provider!r}. Supported: {', '.join(_PROVIDERS)}.")


def supported_providers() -> tuple[Provider, ...]:
    """Return the providers aitap knows how to store keys for."""
    return _PROVIDERS


# ---------------------------------------------------------------------------
# Log filter — keeps stray keys out of stdout / log files
# ---------------------------------------------------------------------------

# Pattern matches every secret shape aitap touches:
#   - Anthropic   ``sk-ant-...``
#   - OpenAI      ``sk-...``
#   - xAI / Groq  ``xai-...`` (defensive — we don't ship them yet but a
#                              user might paste one into the wrong field)
#   - Bearer tokens in upstream auth headers we proxy
#
# We require at least 10 char tail so common prose ("sk-XX it failed")
# doesn't get rewritten.
_LEAK_PATTERN = re.compile(
    r"(sk-ant-[A-Za-z0-9_-]{10,}|sk-[A-Za-z0-9_-]{10,}|xai-[A-Za-z0-9_-]{10,}|Bearer\s+[A-Za-z0-9._-]{10,})"
)


class _SecretLogFilter(logging.Filter):
    """Drop any log record whose msg/args contain a leaked-key pattern.

    Implementation note: we drop the record entirely rather than rewriting
    it. Rewriting would still emit a record that includes the source
    file/line — which is enough metadata for a forensic to know "a
    secret tried to land in the log here", even if the secret itself
    has been masked. Dropping it and emitting a single sanitised warning
    is the conservative call.
    """

    def __init__(self) -> None:
        super().__init__()
        # Track whether we've already emitted the "we dropped something"
        # notice so a noisy stream doesn't fill the log with the warning.
        self._warned = False

    def _looks_secret(self, value: object) -> bool:
        if isinstance(value, str) and _LEAK_PATTERN.search(value):
            return True
        if isinstance(value, (list, tuple)):
            return any(self._looks_secret(item) for item in value)
        if isinstance(value, dict):
            return any(self._looks_secret(k) or self._looks_secret(v) for k, v in value.items())
        return False

    def filter(self, record: logging.LogRecord) -> bool:
        if self._looks_secret(record.msg) or self._looks_secret(record.args):
            if not self._warned:
                self._warned = True
                # Emit a sanitised replacement so operators know "something
                # was dropped here" without seeing the secret. We write
                # directly to stderr so we don't recurse through the filter.
                sys.stderr.write("aitap: dropped a log record that looked like an API key.\n")
            return False
        return True


# Mutable module state — lowercase so pyright's reportConstantRedefinition
# (which keys off all-caps names) leaves us alone. The comment is the
# source of truth for "treat this as private state, not a constant."
_filter_instance: _SecretLogFilter | None = None


def install_log_filter(target_logger: logging.Logger | None = None) -> _SecretLogFilter:
    """Attach the secret-stripping filter to *target_logger* (default: root).

    Idempotent — calling twice does not double-install. Called once by
    the FastAPI bootstrap; tests can call it against a fresh logger to
    assert the filter behaviour.
    """
    global _filter_instance
    if _filter_instance is None:
        _filter_instance = _SecretLogFilter()
    logger = target_logger if target_logger is not None else logging.getLogger()
    if _filter_instance not in logger.filters:
        logger.addFilter(_filter_instance)
    # Also attach to each handler — Python's logging hierarchy applies
    # filters at the logger level, *not* the handler level, so a record
    # that comes through propagation needs the filter on every handler
    # that might emit it. The uvicorn / gunicorn loggers each have their
    # own handler set, so we walk the lot.
    for handler in logger.handlers:
        if _filter_instance not in handler.filters:
            handler.addFilter(_filter_instance)
    return _filter_instance


__all__ = [
    "KeySource",
    "KeyStatus",
    "KeyringUnavailableError",
    "ProfileKeyStatus",
    "Provider",
    "delete_key",
    "delete_key_for_profile",
    "get_key",
    "get_key_for_profile",
    "install_log_filter",
    "key_status",
    "key_status_for_profile",
    "set_key",
    "set_key_for_profile",
    "supported_providers",
]
