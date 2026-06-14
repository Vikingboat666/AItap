"""Shared pytest fixtures for the test suite.

Currently exposes one fixture — :func:`isolated_secrets_home` — that
relocates :func:`Path.home` to a temporary directory so per-test
secret-resolution code paths can't pick up the developer's *real*
``~/.aitap/secrets.yaml`` fallback file. Tests opt in by naming the
fixture in their parameter list; we deliberately do NOT autouse it,
so tests that actually want to exercise the real fallback path keep
working.

Why this exists
---------------

:func:`aitap.secrets.get_key` walks three sources in order: OS
keyring → ``~/.aitap/secrets.yaml`` fallback file → environment
variable. The auth-error tests in ``test_anthropic_client.py`` /
``test_openai_client.py`` only ``monkeypatch.delenv`` the env var,
so on a developer machine that has set a real key in the fallback
file (a common cc-project / multi-provider eval setup), the
"missing key" assertion stops being missing — the test fails locally
but passes in CI's fresh-home environment.

Five PRs (#59, #60, #61, #62, #63) flagged this as a follow-up to
harden; this fixture lands the fix.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_secrets_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Relocate ``Path.home()`` to a fresh tmp dir for the duration of
    one test, and force ``aitap.secrets._keyring_usable`` to return
    ``False`` so the keyring branch is bypassed too.

    Returns the fake home path in case the test wants to plant
    a deliberate ``~/.aitap/secrets.yaml`` to exercise the fallback
    path (write the file under ``<returned_path>/.aitap/secrets.yaml``).

    Implementation notes:

    - ``Path.home()`` reads ``$HOME`` on POSIX and ``$USERPROFILE`` on
      Windows. We monkeypatch both so the fixture works regardless of
      the test runner's platform.
    - We also reset ``$USERPROFILE`` *before* the keyring patch so a
      stray ``Path.home()`` resolution during fixture setup doesn't
      see the developer's real profile dir.
    - The keyring stub is module-level (``aitap.secrets._keyring_usable``),
      not per-call, so this fixture covers every code path that calls
      :func:`aitap.secrets.get_key` or :func:`aitap.secrets.get_key_for_profile`
      during the test body — including indirect calls from
      :class:`AnthropicClient.chat` / :class:`OpenAIClient.chat` /
      :class:`OpenAICompatClient.chat`.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    # Force the keyring branch closed even if the test runner is on a
    # workstation that has Credential Manager / Keychain / Secret
    # Service set up. The fallback / env path is what the test cases
    # care about; we don't want a real keyring read to leak in here.
    from aitap import secrets as _secrets

    monkeypatch.setattr(_secrets, "_keyring_usable", lambda: False)
    return fake_home
