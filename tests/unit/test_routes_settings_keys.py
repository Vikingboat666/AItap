"""Route tests for the new ``/api/settings`` key-management endpoints.

Coverage:

- ``GET /api/settings`` includes per-provider key status (additive).
- ``POST /api/settings/key`` saves the key and **never echoes it** —
  not in the response body, not in any log record we can see.
- ``DELETE /api/settings/key/{provider}`` truly removes the key.
- ``POST /api/settings/test/{provider}`` returns a plain-language
  detail and reports the right reason for auth / rate-limit / network
  / other failures. The probe call is mocked so no real HTTP is made.
- Unknown providers return a 400 with a plain-language error.

All tests run against a fresh :class:`FastAPI` app via :class:`TestClient`,
with the vault patched into a temp HOME so the user's real Credential
Manager is never touched.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Literal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from aitap import secrets as secrets_module
from aitap.deep.client import (
    ChatMessage,
    ChatResponse,
    CostEstimate,
    LLMClient,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    TokenUsage,
)
from aitap.server.app import create_app

# Plant a known canary string so any leak test failure is unambiguous.
_FAKE_ANTHROPIC = "sk-ant-FAKE-TESTKEY-aaaaaaaaaaaaaaaa"
_FAKE_OPENAI = "sk-FAKE-OPENAI-bbbbbbbbbbbbbbbbbb"


class _FakeKeyring:
    """Tiny in-memory keyring fake; see test_secrets.py for the pattern."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}
        self.usable = True

    class _Backend:
        pass

    def get_keyring(self) -> _FakeKeyring._Backend:
        return self._Backend()

    def get_password(self, service: str, account: str) -> str | None:
        return self.store.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        self.store[(service, account)] = value

    def delete_password(self, service: str, account: str) -> None:
        if (service, account) not in self.store:
            raise KeyError("no such password")
        del self.store[(service, account)]


# ---------------------------------------------------------------------------
# Fake LLM client harness used to script the connectivity probe responses
# ---------------------------------------------------------------------------


class _ScriptedProbeClient(LLMClient):
    """LLMClient that either returns a canned reply or raises a chosen error.

    The settings test endpoint imports ``get_client`` from
    ``aitap.deep.client`` inside the handler body. We patch the registry
    so ``get_client("anthropic", ...)`` returns one of these instead of
    the real Anthropic SDK client.
    """

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        *,
        raises: type[Exception] | None = None,
    ) -> None:
        super().__init__(model, api_key)
        self._raises = raises

    @property
    def provider_name(self) -> str:
        return "scripted"

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        response_format: Literal["text", "json"] | None = None,
    ) -> ChatResponse:
        if self._raises is not None:
            raise self._raises("scripted failure")
        return ChatResponse(
            text="pong",
            model=self.model,
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            cost_usd=0.0,
        )

    def estimate_cost(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int | None = None,
    ) -> CostEstimate:
        return CostEstimate(
            input_tokens=1,
            estimated_output_tokens=4,
            usd=0.0,
            model=self.model,
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def vaulted_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, _FakeKeyring]]:
    """Build a FastAPI app whose vault writes into tmp_path."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    fake = _FakeKeyring()
    monkeypatch.setattr(secrets_module, "_keyring_module", lambda: fake)
    monkeypatch.setattr(secrets_module, "_keyring_usable", lambda: fake.usable)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    app = create_app()
    with TestClient(app) as client:
        yield client, fake


# ---------------------------------------------------------------------------
# GET /api/settings — additive ``keys`` field
# ---------------------------------------------------------------------------


def test_get_settings_returns_per_provider_key_status(
    vaulted_app: tuple[TestClient, _FakeKeyring],
) -> None:
    client, _ = vaulted_app
    res = client.get("/api/settings")
    assert res.status_code == 200, res.text
    body = res.json()
    assert "keys" in body, body
    providers = {k["provider"]: k for k in body["keys"]}
    assert set(providers) == {"anthropic", "openai"}
    for entry in providers.values():
        assert entry["configured"] is False
        assert entry["source"] == "none"
        assert entry["masked"] is None


# ---------------------------------------------------------------------------
# POST /api/settings/key — saves the key but never echoes it
# ---------------------------------------------------------------------------


def test_set_key_returns_status_and_never_echoes_the_key(
    vaulted_app: tuple[TestClient, _FakeKeyring],
) -> None:
    client, fake = vaulted_app
    res = client.post(
        "/api/settings/key",
        json={"provider": "anthropic", "key": _FAKE_ANTHROPIC},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # The key MUST NOT be in the response body, anywhere.
    raw_text = res.text
    assert _FAKE_ANTHROPIC not in raw_text, (
        "POST /api/settings/key echoed the raw key in the response body"
    )
    # We got back the metadata triple.
    assert body["provider"] == "anthropic"
    assert body["configured"] is True
    assert body["source"] == "keyring"
    assert body["masked"] == "sk-ant-...aaaa"
    # And the keyring backend was actually written to.
    assert fake.store[("aitap", "provider:anthropic")] == _FAKE_ANTHROPIC


def test_set_key_rejects_empty_key(
    vaulted_app: tuple[TestClient, _FakeKeyring],
) -> None:
    client, _ = vaulted_app
    # FastAPI's pydantic validator catches min_length=1 with a 422.
    res = client.post(
        "/api/settings/key",
        json={"provider": "anthropic", "key": ""},
    )
    assert res.status_code == 422


def test_set_key_rejects_unknown_provider(
    vaulted_app: tuple[TestClient, _FakeKeyring],
) -> None:
    client, _ = vaulted_app
    res = client.post(
        "/api/settings/key",
        json={"provider": "bogus", "key": "sk-anything"},
    )
    assert res.status_code == 422  # pydantic Literal type rejects it


def test_get_settings_after_save_shows_masked_only(
    vaulted_app: tuple[TestClient, _FakeKeyring],
) -> None:
    client, _ = vaulted_app
    client.post(
        "/api/settings/key",
        json={"provider": "anthropic", "key": _FAKE_ANTHROPIC},
    )
    res = client.get("/api/settings")
    assert res.status_code == 200
    raw = res.text
    assert _FAKE_ANTHROPIC not in raw, "GET /api/settings leaked the raw key"
    body = res.json()
    anthropic = next(k for k in body["keys"] if k["provider"] == "anthropic")
    assert anthropic["configured"] is True
    assert anthropic["source"] == "keyring"
    assert anthropic["masked"] == "sk-ant-...aaaa"


# ---------------------------------------------------------------------------
# DELETE /api/settings/key/{provider}
# ---------------------------------------------------------------------------


def test_delete_truly_removes_the_key(
    vaulted_app: tuple[TestClient, _FakeKeyring],
) -> None:
    client, fake = vaulted_app
    client.post(
        "/api/settings/key",
        json={"provider": "openai", "key": _FAKE_OPENAI},
    )
    assert ("aitap", "provider:openai") in fake.store

    res = client.delete("/api/settings/key/openai")
    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is False
    assert body["source"] == "none"
    # Real delete — the keyring entry is gone, not zeroed.
    assert ("aitap", "provider:openai") not in fake.store


def test_delete_unknown_provider_returns_400(
    vaulted_app: tuple[TestClient, _FakeKeyring],
) -> None:
    client, _ = vaulted_app
    res = client.delete("/api/settings/key/bogus")
    assert res.status_code == 400
    detail = res.json()["detail"].lower()
    assert "unknown provider" in detail
    assert "anthropic" in detail and "openai" in detail


# ---------------------------------------------------------------------------
# POST /api/settings/test/{provider}
# ---------------------------------------------------------------------------


def _patch_probe_client(raises: type[Exception] | None) -> patch[object]:
    """Helper: swap the registry so ``get_client`` returns our scripted one."""
    from aitap.deep import client as client_module

    def fake_factory(provider: str, model: str, api_key: str | None = None) -> LLMClient:
        return _ScriptedProbeClient(model, api_key, raises=raises)

    return patch.object(client_module, "get_client", fake_factory)


def test_test_endpoint_ok_when_probe_succeeds(
    vaulted_app: tuple[TestClient, _FakeKeyring],
) -> None:
    client, _ = vaulted_app
    client.post(
        "/api/settings/key",
        json={"provider": "anthropic", "key": _FAKE_ANTHROPIC},
    )
    with _patch_probe_client(raises=None):
        res = client.post("/api/settings/test/anthropic")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["reason"] is None
    assert body["detail"]
    # Detail is plain language — no stack trace, no status code, no key.
    assert _FAKE_ANTHROPIC not in res.text
    assert "401" not in body["detail"]
    assert "Anthropic" in body["detail"]


def test_test_endpoint_auth_when_provider_rejects(
    vaulted_app: tuple[TestClient, _FakeKeyring],
) -> None:
    client, _ = vaulted_app
    client.post(
        "/api/settings/key",
        json={"provider": "anthropic", "key": _FAKE_ANTHROPIC},
    )
    with _patch_probe_client(raises=ProviderAuthError):
        res = client.post("/api/settings/test/anthropic")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["reason"] == "auth"
    assert "Anthropic" in body["detail"]
    assert "Settings" in body["detail"]  # plain-language next-step hint
    assert _FAKE_ANTHROPIC not in res.text


def test_test_endpoint_rate_limit(
    vaulted_app: tuple[TestClient, _FakeKeyring],
) -> None:
    client, _ = vaulted_app
    client.post(
        "/api/settings/key",
        json={"provider": "openai", "key": _FAKE_OPENAI},
    )
    with _patch_probe_client(raises=ProviderRateLimitError):
        res = client.post("/api/settings/test/openai")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["reason"] == "rate_limit"


def test_test_endpoint_network_when_provider_error(
    vaulted_app: tuple[TestClient, _FakeKeyring],
) -> None:
    client, _ = vaulted_app
    client.post(
        "/api/settings/key",
        json={"provider": "openai", "key": _FAKE_OPENAI},
    )
    with _patch_probe_client(raises=ProviderError):
        res = client.post("/api/settings/test/openai")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["reason"] == "network"


def test_test_endpoint_reports_missing_key_without_calling_provider(
    vaulted_app: tuple[TestClient, _FakeKeyring],
) -> None:
    """No key configured -> short-circuit with a plain-language message.

    Notably this must work *without* the probe client being patched —
    we should never reach the LLM call.
    """
    client, _ = vaulted_app
    res = client.post("/api/settings/test/anthropic")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["reason"] == "auth"
    assert "Settings" in body["detail"]


def test_test_endpoint_unknown_provider(
    vaulted_app: tuple[TestClient, _FakeKeyring],
) -> None:
    client, _ = vaulted_app
    res = client.post("/api/settings/test/bogus")
    assert res.status_code == 400


# ---------------------------------------------------------------------------
# Log discipline — saved/tested key never appears in any log record
# ---------------------------------------------------------------------------


def test_no_key_leakage_to_logs(
    vaulted_app: tuple[TestClient, _FakeKeyring],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, _ = vaulted_app
    with caplog.at_level(logging.DEBUG):
        client.post(
            "/api/settings/key",
            json={"provider": "anthropic", "key": _FAKE_ANTHROPIC},
        )
        client.get("/api/settings")
        with _patch_probe_client(raises=ProviderAuthError):
            client.post("/api/settings/test/anthropic")
        client.delete("/api/settings/key/anthropic")

    for record in caplog.records:
        msg = record.getMessage()
        assert _FAKE_ANTHROPIC not in msg, (
            f"raw key leaked into a {record.levelname} log record: {msg!r}"
        )
