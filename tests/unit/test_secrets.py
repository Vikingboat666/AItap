"""Tests for :mod:`aitap.secrets` — the only door to the API-key vault.

We exercise:

- Keyring round-trip (with a fake in-memory backend so tests are
  hermetic and don't touch the user's real Credential Manager).
- Fallback file round-trip (under a temp HOME so we don't write into
  the user's ``~/.aitap``).
- Provider validation.
- The :func:`mask` UX (last four chars + canonical prefix).
- :func:`delete_key` truly removes the entry instead of overwriting it.
- The log-filter drops records that smell like leaked keys.
- The fallback file lands at 0600 on POSIX (best-effort on Windows).

The "import discipline" test (``test_secrets_import_discipline.py``)
lives in a sibling file because it scans the *whole tree* and benefits
from being isolated from the in-process patches we apply here.
"""

from __future__ import annotations

import logging
import stat
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from aitap import secrets as secrets_module

# ---------------------------------------------------------------------------
# A fake in-memory keyring backend the tests use instead of the real one.
# ---------------------------------------------------------------------------


class _FakeKeyring:
    """In-memory replacement for the ``keyring`` package surface.

    Only implements the four functions ``aitap.secrets`` actually calls:
    ``get_keyring``, ``get_password``, ``set_password``, ``delete_password``.
    """

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}
        self.usable = True
        # When True, ``set_password`` raises — mimics a reachable but
        # broken keyring (Keychain locked, SecretService daemon crashed
        # mid-write, etc.). Used by the security tests to prove that a
        # runtime write failure surfaces as ``KeyringUnavailableError``
        # rather than a silent file fallback.
        self.fail_on_set = False

    # The real package exposes a Keyring instance whose module path is
    # used to decide "is this backend usable?". We mimic that.
    class _Backend:
        pass

    def get_keyring(self) -> _FakeKeyring._Backend:
        return self._Backend()

    def get_password(self, service: str, account: str) -> str | None:
        return self.store.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        if self.fail_on_set:
            raise RuntimeError("simulated keyring write failure")
        self.store[(service, account)] = value

    def delete_password(self, service: str, account: str) -> None:
        if (service, account) not in self.store:
            raise KeyError("no such password")
        del self.store[(service, account)]


@pytest.fixture()
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> _FakeKeyring:
    """Replace the keyring backend with an in-memory fake.

    We patch ``_keyring_module`` so ``aitap.secrets`` sees our fake, and
    we force ``_keyring_usable`` to return ``True`` so the fallback path
    isn't taken unless we explicitly disable the fake.
    """
    fake = _FakeKeyring()
    monkeypatch.setattr(secrets_module, "_keyring_module", lambda: fake)
    monkeypatch.setattr(secrets_module, "_keyring_usable", lambda: fake.usable)
    return fake


@pytest.fixture()
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point ``Path.home()`` at a temp directory.

    ``aitap.secrets._fallback_path`` resolves on every call, so swapping
    ``Path.home`` is enough — no need to monkey-patch anything in the
    secrets module itself. This keeps the user's real ``~/.aitap``
    untouched no matter what the tests do.
    """
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    yield tmp_path


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip the provider env vars so they don't accidentally satisfy ``get_key``."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


# ---------------------------------------------------------------------------
# Provider validation
# ---------------------------------------------------------------------------


def test_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="Unknown provider"):
        secrets_module.key_status("bogus")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Unknown provider"):
        secrets_module.get_key("bogus")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Unknown provider"):
        secrets_module.set_key("bogus", "k")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Unknown provider"):
        secrets_module.delete_key("bogus")  # type: ignore[arg-type]


def test_supported_providers_lists_both() -> None:
    assert set(secrets_module.supported_providers()) == {"anthropic", "openai"}


# ---------------------------------------------------------------------------
# Keyring round-trip
# ---------------------------------------------------------------------------


def test_set_then_get_via_keyring(
    fake_keyring: _FakeKeyring, isolated_home: Path, clean_env: None
) -> None:
    """Happy path: write through keyring, read back the same value."""
    status = secrets_module.set_key("anthropic", "sk-ant-fake-roundtrip-1234")

    assert status.configured is True
    assert status.source == "keyring"
    assert status.masked == "sk-ant-...1234"

    assert secrets_module.get_key("anthropic") == "sk-ant-fake-roundtrip-1234"

    again = secrets_module.key_status("anthropic")
    assert again.source == "keyring"
    assert again.masked == "sk-ant-...1234"


def test_unconfigured_provider_returns_none(
    fake_keyring: _FakeKeyring, isolated_home: Path, clean_env: None
) -> None:
    status = secrets_module.key_status("openai")
    assert status.configured is False
    assert status.source == "none"
    assert status.masked is None
    assert secrets_module.get_key("openai") is None


def test_keyring_delete_truly_removes(
    fake_keyring: _FakeKeyring, isolated_home: Path, clean_env: None
) -> None:
    """Delete must call ``delete_password``, not write an empty entry.

    The fake backend's ``store`` lets us assert that — after delete, the
    ``(service, account)`` tuple shouldn't be in the dict at all.
    """
    secrets_module.set_key("openai", "sk-real-openai-xyz0987654")
    assert ("aitap", "provider:openai") in fake_keyring.store

    after = secrets_module.delete_key("openai")
    assert after.configured is False
    assert after.source == "none"
    assert ("aitap", "provider:openai") not in fake_keyring.store


# ---------------------------------------------------------------------------
# Fallback round-trip
# ---------------------------------------------------------------------------


def test_set_without_opt_in_raises_when_keyring_unusable(
    fake_keyring: _FakeKeyring, isolated_home: Path, clean_env: None
) -> None:
    """When the keyring is unusable and the caller didn't opt into the file
    fallback, ``set_key`` raises and writes **nothing**. The security model
    forbids a silent file-write (see docs/settings-ui-design.md §Security)."""
    fake_keyring.usable = False
    with pytest.raises(secrets_module.KeyringUnavailableError):
        secrets_module.set_key("anthropic", "sk-ant-fallback-xyzz9876")

    # Nothing landed on disk — the fallback path must not exist.
    fallback_path = isolated_home / ".aitap" / "secrets.yaml"
    assert not fallback_path.exists()

    # And the resolver still reports unconfigured.
    assert secrets_module.key_status("anthropic").configured is False


def test_set_without_opt_in_raises_when_keyring_write_fails(
    fake_keyring: _FakeKeyring, isolated_home: Path, clean_env: None
) -> None:
    """Same security guarantee when the keyring is *reachable* but the
    underlying write throws (Keychain locked, SecretService crashed, …)."""
    fake_keyring.usable = True
    fake_keyring.fail_on_set = True

    with pytest.raises(secrets_module.KeyringUnavailableError):
        secrets_module.set_key("anthropic", "sk-ant-write-fail-12345678")

    fallback_path = isolated_home / ".aitap" / "secrets.yaml"
    assert not fallback_path.exists()
    assert secrets_module.key_status("anthropic").configured is False


def test_set_via_explicit_fallback_opt_in(
    fake_keyring: _FakeKeyring, isolated_home: Path, clean_env: None
) -> None:
    """``use_fallback=True`` writes to the file even when keyring is healthy."""
    status = secrets_module.set_key("openai", "sk-fake-openai-opted-in-987654", use_fallback=True)
    assert status.source == "fallback"

    # Keyring shouldn't see the value.
    assert fake_keyring.store == {}

    fallback_path = isolated_home / ".aitap" / "secrets.yaml"
    assert fallback_path.is_file()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits only")
def test_fallback_file_is_user_only_on_posix(
    fake_keyring: _FakeKeyring, isolated_home: Path, clean_env: None
) -> None:
    fake_keyring.usable = False
    # use_fallback=True is the explicit opt-in path the new contract
    # requires; without it the call raises (see the security tests above).
    secrets_module.set_key("openai", "sk-fallback-mode-1234567890abc", use_fallback=True)

    fallback_path = isolated_home / ".aitap" / "secrets.yaml"
    mode = fallback_path.stat().st_mode & 0o777
    # Tolerate the umask leaving the group/other bits already off; the
    # critical assertion is "no read for group or other".
    assert mode & stat.S_IRGRP == 0
    assert mode & stat.S_IROTH == 0


def test_fallback_delete_removes_entry(
    fake_keyring: _FakeKeyring, isolated_home: Path, clean_env: None
) -> None:
    fake_keyring.usable = False
    # Both writes use the explicit opt-in path now that silent fallback
    # is forbidden by the security contract.
    secrets_module.set_key("anthropic", "sk-ant-fb-2222222222a", use_fallback=True)
    secrets_module.set_key("openai", "sk-fb-3333333333b", use_fallback=True)

    secrets_module.delete_key("anthropic")
    assert secrets_module.get_key("anthropic") is None
    # The other provider is untouched.
    assert secrets_module.get_key("openai") == "sk-fb-3333333333b"

    secrets_module.delete_key("openai")
    fallback_path = isolated_home / ".aitap" / "secrets.yaml"
    # File should be gone (or at minimum, not contain either key).
    if fallback_path.exists():
        assert "sk-" not in fallback_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# env var compatibility — preserves legacy CI / docker setups
# ---------------------------------------------------------------------------


def test_env_var_falls_through_when_nothing_stored(
    fake_keyring: _FakeKeyring,
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-aaaaabbbbb1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    status = secrets_module.key_status("anthropic")
    assert status.configured is True
    assert status.source == "env"
    assert status.masked == "sk-ant-...bbb1"
    assert secrets_module.get_key("anthropic") == "sk-ant-env-aaaaabbbbb1"

    # Openai still reports nothing.
    assert secrets_module.key_status("openai").configured is False


def test_keyring_wins_over_env(
    fake_keyring: _FakeKeyring,
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolution order is documented: keyring > fallback > env."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-aaaaabbbbb1")
    secrets_module.set_key("anthropic", "sk-ant-keychain-9999988888")

    status = secrets_module.key_status("anthropic")
    assert status.source == "keyring"
    assert secrets_module.get_key("anthropic") == "sk-ant-keychain-9999988888"


# ---------------------------------------------------------------------------
# Set validation
# ---------------------------------------------------------------------------


def test_set_rejects_empty_key(
    fake_keyring: _FakeKeyring, isolated_home: Path, clean_env: None
) -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        secrets_module.set_key("anthropic", "")
    with pytest.raises(ValueError, match="cannot be empty"):
        secrets_module.set_key("anthropic", "   ")


# ---------------------------------------------------------------------------
# Log filter — keys never make it into log streams
# ---------------------------------------------------------------------------


def _capture_logger(name: str) -> tuple[logging.Logger, list[logging.LogRecord]]:
    """Build a logger that captures records into an in-memory list.

    We return both the logger (so the test can install the filter on it)
    and the buffer so the test can inspect what actually got emitted.
    """
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.filters.clear()
    logger.setLevel(logging.DEBUG)
    captured: list[logging.LogRecord] = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    logger.addHandler(_CaptureHandler())
    logger.propagate = False
    return logger, captured


def test_log_filter_drops_anthropic_key_in_message() -> None:
    logger, captured = _capture_logger("aitap.secrets.test.anthropic")
    secrets_module.install_log_filter(logger)

    logger.info("Calling Anthropic with key sk-ant-fakefakefakefake")

    assert captured == [], "leaked key record was not dropped"


def test_log_filter_drops_openai_key_in_args() -> None:
    logger, captured = _capture_logger("aitap.secrets.test.openai")
    secrets_module.install_log_filter(logger)

    logger.info("auth header: %s", "Bearer sk-1234567890abcdef")

    assert captured == [], "Bearer token args were not dropped"


def test_log_filter_keeps_innocent_records() -> None:
    """A normal log line about 'sk-' that isn't a real key should pass.

    The pattern requires >=10 chars of payload, so an error message like
    "sk-" or "the sk-set is empty" is not dropped.
    """
    logger, captured = _capture_logger("aitap.secrets.test.normal")
    secrets_module.install_log_filter(logger)

    logger.info("normal message about nothing in particular")
    logger.info("sk-short")
    logger.info("the provider returned status 401")

    assert len(captured) == 3


def test_log_filter_is_idempotent() -> None:
    """Calling install_log_filter twice doesn't double-attach."""
    logger, _ = _capture_logger("aitap.secrets.test.idem")
    secrets_module.install_log_filter(logger)
    secrets_module.install_log_filter(logger)

    # The filter list should hold exactly one instance.
    secret_filters = [f for f in logger.filters if isinstance(f, secrets_module._SecretLogFilter)]
    assert len(secret_filters) == 1


# ---------------------------------------------------------------------------
# Mask behaviour — the only "key-like" thing the API ever returns
# ---------------------------------------------------------------------------


def test_mask_handles_known_prefixes() -> None:
    assert secrets_module._mask("sk-ant-1234567890wxyz") == "sk-ant-...wxyz"
    assert secrets_module._mask("sk-openai-abcdef1234") == "sk-...1234"
    assert secrets_module._mask("plain-key-value-zzzz") == "...zzzz"
    assert secrets_module._mask("ab") == "..."


# ---------------------------------------------------------------------------
# Persistence-leak scan: nothing aitap writes should contain a key prefix
# ---------------------------------------------------------------------------


def test_set_key_does_not_log_the_key(
    fake_keyring: _FakeKeyring,
    isolated_home: Path,
    clean_env: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The set_key call itself must not log the secret value anywhere."""
    secrets_module.install_log_filter(logging.getLogger())
    with caplog.at_level(logging.DEBUG, logger="aitap"):
        secrets_module.set_key("anthropic", "sk-ant-secret-not-logged-1234")
    # Whatever we logged, none of it should contain the raw key.
    for record in caplog.records:
        assert "sk-ant-secret-not-logged-1234" not in record.getMessage()


def test_fallback_file_path_is_under_home(fake_keyring: _FakeKeyring, isolated_home: Path) -> None:
    """Defence in depth: the fallback path resolves to a child of ``~``."""
    path = secrets_module._fallback_path()
    home = Path.home().resolve()
    assert home in path.resolve().parents
