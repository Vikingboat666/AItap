"""Tests for the profile-id flavour of :mod:`aitap.secrets`.

The multi-provider redesign (``docs/profiles-design.md``) keeps the same
keyring service name (``aitap``) but switches the account convention
from ``provider:<name>`` to ``profile:<id>``. This file exercises the
parallel API surface:

- :func:`get_key_for_profile`
- :func:`key_status_for_profile`
- :func:`set_key_for_profile`
- :func:`delete_key_for_profile`

The original provider-keyed functions are kept for now (the wt/profile-cleanup
worktree removes them once the callers are migrated). These tests are
additive and do not touch the provider-keyed coverage in test_secrets.py.

Test matrix:

- Keyring round-trip under a ``profile:<id>`` account.
- Fallback file round-trip — explicit opt-in, no silent fallback.
- ``key_status_for_profile`` resolves keyring > fallback > env-style
  miss (there is no env-var convention per-profile, so unconfigured
  is the steady state when nothing has been written).
- Empty / blank id rejected with a plain-language error.
- Empty / blank key rejected (same contract as the provider variant).
- ``delete_key_for_profile`` truly removes the entry instead of
  overwriting with an empty string.
- Round-trip works for ids with slug edge characters (``-``, ``_``,
  long ASCII, lower-case digits) — anything that survives the
  documented slugify algorithm in profiles-design.md Decision 2.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from aitap import secrets as secrets_module


class _FakeKeyring:
    """In-memory replacement for the ``keyring`` package surface.

    Identical pattern to ``test_secrets.py._FakeKeyring`` so the two
    test files can be read side-by-side.
    """

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}
        self.usable = True
        self.fail_on_set = False

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
    fake = _FakeKeyring()
    monkeypatch.setattr(secrets_module, "_keyring_module", lambda: fake)
    monkeypatch.setattr(secrets_module, "_keyring_usable", lambda: fake.usable)
    return fake


@pytest.fixture()
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    yield tmp_path


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # The profile-keyed API has no per-profile env var fallback — but we
    # still strip these so a stray env in the dev's shell can't influence
    # provider-keyed code paths that share helpers.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


# ---------------------------------------------------------------------------
# Keyring round-trip
# ---------------------------------------------------------------------------


def test_profile_set_then_get_via_keyring(
    fake_keyring: _FakeKeyring, isolated_home: Path, clean_env: None
) -> None:
    status = secrets_module.set_key_for_profile("openai-prod", "sk-profile-roundtrip-aaaaaaaa")

    assert status.configured is True
    assert status.source == "keyring"
    assert status.masked == "sk-...aaaa"

    # Account name follows the new ``profile:<id>`` convention.
    assert ("aitap", "profile:openai-prod") in fake_keyring.store

    assert secrets_module.get_key_for_profile("openai-prod") == "sk-profile-roundtrip-aaaaaaaa"

    status_again = secrets_module.key_status_for_profile("openai-prod")
    assert status_again.source == "keyring"
    assert status_again.masked == "sk-...aaaa"
    assert status_again.profile_id == "openai-prod"


def test_profile_unconfigured_returns_none(
    fake_keyring: _FakeKeyring, isolated_home: Path, clean_env: None
) -> None:
    status = secrets_module.key_status_for_profile("never-written")
    assert status.configured is False
    assert status.source == "none"
    assert status.masked is None
    assert secrets_module.get_key_for_profile("never-written") is None


def test_profile_delete_truly_removes(
    fake_keyring: _FakeKeyring, isolated_home: Path, clean_env: None
) -> None:
    """Real delete — the keyring tuple is gone, not zeroed."""
    secrets_module.set_key_for_profile("kimi-main", "sk-kimi-1234567890abcd")
    assert ("aitap", "profile:kimi-main") in fake_keyring.store

    after = secrets_module.delete_key_for_profile("kimi-main")
    assert after.configured is False
    assert after.source == "none"
    assert ("aitap", "profile:kimi-main") not in fake_keyring.store


# ---------------------------------------------------------------------------
# Fallback opt-in
# ---------------------------------------------------------------------------


def test_profile_set_without_opt_in_raises_when_keyring_unusable(
    fake_keyring: _FakeKeyring, isolated_home: Path, clean_env: None
) -> None:
    fake_keyring.usable = False
    with pytest.raises(secrets_module.KeyringUnavailableError):
        secrets_module.set_key_for_profile("deepseek-1", "sk-deepseek-12345678abcd")

    fallback_path = isolated_home / ".aitap" / "secrets.yaml"
    assert not fallback_path.exists()
    assert secrets_module.key_status_for_profile("deepseek-1").configured is False


def test_profile_set_without_opt_in_raises_when_write_fails(
    fake_keyring: _FakeKeyring, isolated_home: Path, clean_env: None
) -> None:
    fake_keyring.usable = True
    fake_keyring.fail_on_set = True

    with pytest.raises(secrets_module.KeyringUnavailableError):
        secrets_module.set_key_for_profile("groq-fast", "sk-groq-fail-write-87654321")

    fallback_path = isolated_home / ".aitap" / "secrets.yaml"
    assert not fallback_path.exists()
    assert secrets_module.key_status_for_profile("groq-fast").configured is False


def test_profile_set_via_explicit_fallback_opt_in(
    fake_keyring: _FakeKeyring, isolated_home: Path, clean_env: None
) -> None:
    status = secrets_module.set_key_for_profile(
        "together-1", "sk-together-zzzzzzzzzz", use_fallback=True
    )
    assert status.source == "fallback"
    assert status.masked == "sk-...zzzz"

    # Keyring untouched on explicit fallback.
    assert fake_keyring.store == {}

    fallback_path = isolated_home / ".aitap" / "secrets.yaml"
    assert fallback_path.is_file()
    text = fallback_path.read_text(encoding="utf-8")
    # The dict key is the new ``profile:<id>`` form, not the old
    # ``provider:<name>``.
    assert "profile:together-1" in text


def test_profile_fallback_delete_removes_entry(
    fake_keyring: _FakeKeyring, isolated_home: Path, clean_env: None
) -> None:
    fake_keyring.usable = False
    secrets_module.set_key_for_profile(
        "anthropic-personal", "sk-ant-fbk-aaaaaaaaaa", use_fallback=True
    )
    secrets_module.set_key_for_profile(
        "openai-personal", "sk-fbk-openai-bbbbbbbbbb", use_fallback=True
    )

    secrets_module.delete_key_for_profile("anthropic-personal")
    assert secrets_module.get_key_for_profile("anthropic-personal") is None
    assert secrets_module.get_key_for_profile("openai-personal") == "sk-fbk-openai-bbbbbbbbbb"

    secrets_module.delete_key_for_profile("openai-personal")
    fallback_path = isolated_home / ".aitap" / "secrets.yaml"
    if fallback_path.exists():
        # If still around it must not carry either secret.
        assert "sk-" not in fallback_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_profile_set_rejects_empty_id(
    fake_keyring: _FakeKeyring, isolated_home: Path, clean_env: None
) -> None:
    with pytest.raises(ValueError, match="profile id"):
        secrets_module.set_key_for_profile("", "sk-anything-here-1234567890")
    with pytest.raises(ValueError, match="profile id"):
        secrets_module.set_key_for_profile("   ", "sk-anything-here-1234567890")


def test_profile_set_rejects_empty_key(
    fake_keyring: _FakeKeyring, isolated_home: Path, clean_env: None
) -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        secrets_module.set_key_for_profile("openai-main", "")
    with pytest.raises(ValueError, match="cannot be empty"):
        secrets_module.set_key_for_profile("openai-main", "   ")


def test_profile_status_rejects_empty_id(
    fake_keyring: _FakeKeyring, isolated_home: Path, clean_env: None
) -> None:
    with pytest.raises(ValueError, match="profile id"):
        secrets_module.key_status_for_profile("")


def test_profile_get_rejects_empty_id(
    fake_keyring: _FakeKeyring, isolated_home: Path, clean_env: None
) -> None:
    with pytest.raises(ValueError, match="profile id"):
        secrets_module.get_key_for_profile("")


def test_profile_delete_rejects_empty_id(
    fake_keyring: _FakeKeyring, isolated_home: Path, clean_env: None
) -> None:
    with pytest.raises(ValueError, match="profile id"):
        secrets_module.delete_key_for_profile("")


# ---------------------------------------------------------------------------
# Id slug edge characters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "profile_id",
    [
        "a",  # one-char minimum
        "ollama-local",
        "openai_compat_v1",
        "vendor-x-mode-7",
        "abc123",
        "long-" + "x" * 60,
    ],
)
def test_profile_round_trip_handles_slug_characters(
    profile_id: str,
    fake_keyring: _FakeKeyring,
    isolated_home: Path,
    clean_env: None,
) -> None:
    """Any string surviving the documented slug algorithm round-trips
    cleanly through the keyring backend."""
    secrets_module.set_key_for_profile(profile_id, "sk-roundtrip-XXXXXXXXXX")
    assert secrets_module.get_key_for_profile(profile_id) == "sk-roundtrip-XXXXXXXXXX"
    status = secrets_module.key_status_for_profile(profile_id)
    assert status.configured is True
    assert status.source == "keyring"
    assert status.profile_id == profile_id


# ---------------------------------------------------------------------------
# Isolation between profile- and provider-keyed entries
# ---------------------------------------------------------------------------


def test_profile_and_provider_namespaces_are_independent(
    fake_keyring: _FakeKeyring, isolated_home: Path, clean_env: None
) -> None:
    """Writing under ``profile:anthropic-personal`` must not affect the
    legacy ``provider:anthropic`` entry, and vice versa. This keeps the
    staged migration safe — the two account namespaces coexist until
    wt/profile-cleanup removes the legacy one."""
    secrets_module.set_key_for_profile("anthropic-personal", "sk-ant-pf-aaaaaaaaaa")
    secrets_module.set_key("anthropic", "sk-ant-legacy-bbbbbbbbbb")

    assert secrets_module.get_key("anthropic") == "sk-ant-legacy-bbbbbbbbbb"
    assert secrets_module.get_key_for_profile("anthropic-personal") == "sk-ant-pf-aaaaaaaaaa"

    secrets_module.delete_key_for_profile("anthropic-personal")
    # Legacy entry untouched.
    assert secrets_module.get_key("anthropic") == "sk-ant-legacy-bbbbbbbbbb"
