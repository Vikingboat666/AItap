"""Route tests for the new ``/api/profiles`` CRUD endpoints.

Coverage:

- ``GET /api/profiles`` lists what was created, with per-profile key
  status fields populated.
- ``POST /api/profiles`` creates a profile, slugifies the label into an
  id, accepts an optional ``api_key``, **never echoes the key** in the
  response.
- Slug collisions append ``-2`` / ``-3`` / etc.
- Unicode labels slugify to ASCII with diacritics stripped.
- ``PUT /api/profiles/{id}`` mutates everything but the id; new key
  replaces the old one.
- ``DELETE /api/profiles/{id}`` real-removes the keyring entry and the
  YAML row, and auto-nulls any ``defaults`` reference (Decision 1).
- ``POST /api/profiles/{id}/test`` resolves the key from the vault,
  builds a real :class:`LLMClient` via
  :func:`aitap.deep.factory.get_client_for_profile`, and exercises the
  full reason taxonomy (auth / rate_limit / network / other) via a
  scripted probe — added in ``wt/profile-client``.
- 409 when the keyring is unreachable and ``use_fallback`` is False —
  same security contract as PR #35's ``POST /api/settings/key``.

Tests use a tmp ``project_root`` so config writes don't pollute the
worktree's real ``.aitap/``, and a fake keyring so the user's actual
Credential Manager / Keychain stays untouched.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Literal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from aitap import secrets as secrets_module
from aitap.config import Settings
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
from aitap.server.routes import Profile
from aitap.server.routes import profiles as profiles_module
from aitap.server.routes._deps import get_settings

# Plant a known canary so any leak failure is unambiguous.
_FAKE_KEY = "sk-FAKE-PROFILE-CANARY-zzzzzzzzzzzz"


class _FakeKeyring:
    """In-memory keyring fake — mirrors test_routes_settings_keys.py."""

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
def settings_in_tmp(tmp_path: Path) -> Settings:
    """A throwaway Settings whose project_root is the tmp dir."""
    aitap_dir = tmp_path / ".aitap"
    aitap_dir.mkdir(parents=True, exist_ok=True)
    return Settings(project_root=tmp_path)


@pytest.fixture()
def client(
    settings_in_tmp: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, _FakeKeyring, Settings]]:
    """FastAPI client + fake keyring + the tmp Settings.

    The profiles module caches its config in-process between requests
    (``_PROFILES`` / ``_defaults`` / ``_initialised``); reset before AND
    after each test so concurrent test orderings don't bleed.
    """
    # Point ``~`` at tmp_path so the fallback file path lives under it.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    fake = _FakeKeyring()
    monkeypatch.setattr(secrets_module, "_keyring_module", lambda: fake)
    monkeypatch.setattr(secrets_module, "_keyring_usable", lambda: fake.usable)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    profiles_module.reset_state_for_tests()

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings_in_tmp

    with TestClient(app) as test_client:
        yield test_client, fake, settings_in_tmp

    profiles_module.reset_state_for_tests()


def _body_for(
    *,
    label: str = "DeepSeek",
    base_url: str = "https://api.deepseek.com/v1",
    protocol: str = "openai-compat",
    model_id: str = "deepseek-chat",
    api_key: str | None = None,
    use_fallback: bool = False,
    notes: str = "",
) -> dict[str, object]:
    body: dict[str, object] = {
        "label": label,
        "base_url": base_url,
        "protocol": protocol,
        "model_id": model_id,
        "notes": notes,
        "use_fallback": use_fallback,
    }
    if api_key is not None:
        body["api_key"] = api_key
    return body


# ---------------------------------------------------------------------------
# GET / POST happy paths
# ---------------------------------------------------------------------------


def test_list_profiles_empty_by_default(
    client: tuple[TestClient, _FakeKeyring, Settings],
) -> None:
    """No profiles seeded → empty list, not a 404 or 500."""
    c, _, _ = client
    res = c.get("/api/profiles")
    assert res.status_code == 200
    assert res.json() == []


def test_create_profile_without_key_persists_metadata(
    client: tuple[TestClient, _FakeKeyring, Settings],
) -> None:
    """Creating a profile with no ``api_key`` is allowed (key configured later)."""
    c, fake, _ = client
    res = c.post("/api/profiles", json=_body_for(label="DeepSeek prod"))
    assert res.status_code == 201, res.text
    body = res.json()

    assert body["id"] == "deepseek-prod"
    assert body["label"] == "DeepSeek prod"
    assert body["base_url"] == "https://api.deepseek.com/v1"
    assert body["protocol"] == "openai-compat"
    assert body["model_id"] == "deepseek-chat"
    assert body["key_configured"] is False
    assert body["key_source"] == "none"
    assert body["key_masked"] is None
    # Keyring stays empty when no key was provided.
    assert fake.store == {}


def test_create_profile_with_key_writes_keyring_and_never_echoes(
    client: tuple[TestClient, _FakeKeyring, Settings],
) -> None:
    """The submitted ``api_key`` lands in the keyring under ``profile:<id>``
    and never appears anywhere in the response body."""
    c, fake, _ = client
    res = c.post(
        "/api/profiles",
        json=_body_for(label="DeepSeek", api_key=_FAKE_KEY),
    )
    assert res.status_code == 201, res.text
    body = res.json()

    assert body["key_configured"] is True
    assert body["key_source"] == "keyring"
    assert body["key_masked"] is not None
    assert body["key_masked"] != _FAKE_KEY
    # Never-echo-key — the raw value isn't anywhere in the response.
    assert _FAKE_KEY not in res.text
    # Stored under the new account convention.
    assert ("aitap", f"profile:{body['id']}") in fake.store


def test_create_profile_id_collision_appends_numeric_suffix(
    client: tuple[TestClient, _FakeKeyring, Settings],
) -> None:
    """Two profiles with the same label get ``id`` and ``id-2`` (Decision 2)."""
    c, _, _ = client
    first = c.post("/api/profiles", json=_body_for(label="DeepSeek"))
    second = c.post("/api/profiles", json=_body_for(label="DeepSeek"))
    third = c.post("/api/profiles", json=_body_for(label="DeepSeek"))
    assert first.status_code == 201
    assert second.status_code == 201
    assert third.status_code == 201

    assert first.json()["id"] == "deepseek"
    assert second.json()["id"] == "deepseek-2"
    assert third.json()["id"] == "deepseek-3"


def test_create_profile_unicode_label_slugifies_to_ascii(
    client: tuple[TestClient, _FakeKeyring, Settings],
) -> None:
    """Decision 2: NFKD-strip diacritics, drop non-ASCII, keep label display."""
    c, _, _ = client
    res = c.post("/api/profiles", json=_body_for(label="Kimi 月之暗面 — café"))
    assert res.status_code == 201, res.text
    body = res.json()
    # Display label survives Unicode unchanged.
    assert body["label"] == "Kimi 月之暗面 — café"
    # ID is ASCII-only (no CJK, no diacritics).
    assert body["id"].isascii(), body["id"]
    # And it's a non-empty slug (didn't degenerate to "").
    assert body["id"]


# ---------------------------------------------------------------------------
# PUT (update)
# ---------------------------------------------------------------------------


def test_update_profile_keeps_id_when_label_changes(
    client: tuple[TestClient, _FakeKeyring, Settings],
) -> None:
    """The id is immutable; updating the label only changes the display name."""
    c, _, _ = client
    created = c.post("/api/profiles", json=_body_for(label="DeepSeek")).json()
    profile_id = created["id"]

    res = c.put(
        f"/api/profiles/{profile_id}",
        json=_body_for(label="DeepSeek (prod)", model_id="deepseek-coder"),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["id"] == profile_id  # id stable
    assert body["label"] == "DeepSeek (prod)"
    assert body["model_id"] == "deepseek-coder"


def test_update_profile_with_new_api_key_replaces_in_keyring(
    client: tuple[TestClient, _FakeKeyring, Settings],
) -> None:
    """Passing a new ``api_key`` to PUT replaces the keyring entry; the
    response never echoes the new key."""
    c, fake, _ = client
    created = c.post("/api/profiles", json=_body_for(label="DeepSeek", api_key="sk-old")).json()
    profile_id = created["id"]
    assert fake.store[("aitap", f"profile:{profile_id}")] == "sk-old"

    res = c.put(
        f"/api/profiles/{profile_id}",
        json=_body_for(label="DeepSeek", api_key=_FAKE_KEY),
    )
    assert res.status_code == 200
    assert fake.store[("aitap", f"profile:{profile_id}")] == _FAKE_KEY
    assert _FAKE_KEY not in res.text


def test_update_unknown_profile_returns_404(
    client: tuple[TestClient, _FakeKeyring, Settings],
) -> None:
    c, _, _ = client
    res = c.put(
        "/api/profiles/never-existed",
        json=_body_for(label="anything"),
    )
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


def test_delete_profile_removes_keyring_entry_and_yaml_row(
    client: tuple[TestClient, _FakeKeyring, Settings],
    settings_in_tmp: Settings,
) -> None:
    """The keyring tuple is removed (real ``delete_password``) and the
    profile no longer appears in the GET list."""
    c, fake, _ = client
    created = c.post("/api/profiles", json=_body_for(label="DeepSeek", api_key=_FAKE_KEY)).json()
    profile_id = created["id"]
    assert ("aitap", f"profile:{profile_id}") in fake.store

    res = c.delete(f"/api/profiles/{profile_id}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["id"] == profile_id
    assert body["key_configured"] is False  # final state after delete
    assert body["key_source"] == "none"

    # Real delete — tuple is gone, not blanked.
    assert ("aitap", f"profile:{profile_id}") not in fake.store
    # Gone from the list.
    assert c.get("/api/profiles").json() == []


def test_delete_profile_referenced_by_defaults_auto_nulls_defaults(
    client: tuple[TestClient, _FakeKeyring, Settings],
) -> None:
    """Decision 1: deleting the profile set as ``defaults.model_profile_id``
    or ``judge_profile_id`` auto-nulls the reference. (We assert via the
    in-process defaults state; the persisted-YAML check rides on the
    integration suite once the defaults endpoint lands.)"""
    c, _, _ = client
    profile = c.post("/api/profiles", json=_body_for(label="DeepSeek")).json()

    # Manually set defaults (we don't have the defaults PUT endpoint yet
    # in this checkpoint — we mutate the module state directly to set up
    # the precondition the delete handler must clear).
    from aitap.config import DefaultsConfig
    from aitap.server.routes import profiles as pm

    pm._defaults = DefaultsConfig(  # type: ignore[assignment]
        model_profile_id=profile["id"],
        judge_profile_id=profile["id"],
    )

    res = c.delete(f"/api/profiles/{profile['id']}")
    assert res.status_code == 200

    # Both defaults references must be cleared.
    assert pm._defaults.model_profile_id is None
    assert pm._defaults.judge_profile_id is None


def test_delete_unknown_profile_returns_404(
    client: tuple[TestClient, _FakeKeyring, Settings],
) -> None:
    c, _, _ = client
    res = c.delete("/api/profiles/never-existed")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/profiles/{id}/test — real probe (wt/profile-client CP4)
# ---------------------------------------------------------------------------


class _ScriptedProbeClient(LLMClient):
    """LLMClient that either echoes ``pong`` or raises a chosen error.

    Mirrors the same-named class in ``test_routes_settings_keys.py``; we
    swap the factory in :mod:`aitap.server.routes.profiles` so the
    handler builds one of these instead of a real OpenAICompatClient /
    AnthropicClient. Avoids any network call or SDK import in CI.
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
        # Record the constructor inputs so a test can assert the key
        # was forwarded verbatim from secrets → factory → SDK.
        self.received_api_key = api_key

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


def _patch_factory(raises: type[Exception] | None) -> patch[object]:
    """Swap ``get_client_for_profile`` for one that returns our scripted client.

    The handler in ``server/routes/profiles.py`` calls
    ``factory.get_client_for_profile`` to build the probe client; this
    helper makes that call return a controlled :class:`_ScriptedProbeClient`
    so each branch (ok / auth / rate_limit / network / other) is
    deterministic.
    """
    from aitap.server.routes import profiles as routes_profiles_module

    def fake_factory(profile: Profile, api_key: str) -> LLMClient:
        return _ScriptedProbeClient(profile.model_id, api_key, raises=raises)

    return patch.object(routes_profiles_module, "get_client_for_profile", fake_factory)


def test_test_profile_short_circuits_without_key_and_never_calls_provider(
    client: tuple[TestClient, _FakeKeyring, Settings],
) -> None:
    """No key configured → ok=false, reason=auth, plain-language detail.

    Crucially we do NOT patch the factory: the handler must short-circuit
    before any factory call so a missing key never reaches the SDK.
    """
    c, _, _ = client
    profile = c.post("/api/profiles", json=_body_for(label="DeepSeek")).json()

    res = c.post(f"/api/profiles/{profile['id']}/test")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["reason"] == "auth"
    # Plain-language: names the next action.
    assert "Settings" in body["detail"]
    assert "key" in body["detail"].lower()


def test_test_profile_ok_when_probe_succeeds(
    client: tuple[TestClient, _FakeKeyring, Settings],
) -> None:
    """Real probe path: key configured + scripted chat returns pong → ok=true."""
    c, _, _ = client
    profile = c.post("/api/profiles", json=_body_for(label="DeepSeek", api_key=_FAKE_KEY)).json()

    with _patch_factory(raises=None):
        res = c.post(f"/api/profiles/{profile['id']}/test")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["reason"] is None
    assert body["detail"]
    # Plain-language: names the endpoint and confirms reachability.
    assert "reachable" in body["detail"].lower() or "works" in body["detail"].lower()
    # Never echoes the key.
    assert _FAKE_KEY not in res.text


def test_test_profile_auth_when_probe_raises_provider_auth_error(
    client: tuple[TestClient, _FakeKeyring, Settings],
) -> None:
    """ProviderAuthError → reason=auth + plain-language remediation."""
    c, _, _ = client
    profile = c.post("/api/profiles", json=_body_for(label="DeepSeek", api_key=_FAKE_KEY)).json()

    with _patch_factory(raises=ProviderAuthError):
        res = c.post(f"/api/profiles/{profile['id']}/test")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["reason"] == "auth"
    # Plain-language: tells the user what to do next + names the screen.
    assert "Settings" in body["detail"]
    assert _FAKE_KEY not in res.text


def test_test_profile_rate_limit_when_probe_raises_rate_limit(
    client: tuple[TestClient, _FakeKeyring, Settings],
) -> None:
    """ProviderRateLimitError → reason=rate_limit + "wait a moment" detail."""
    c, _, _ = client
    profile = c.post("/api/profiles", json=_body_for(label="DeepSeek", api_key=_FAKE_KEY)).json()

    with _patch_factory(raises=ProviderRateLimitError):
        res = c.post(f"/api/profiles/{profile['id']}/test")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["reason"] == "rate_limit"
    # Plain-language: tells the user the key works + asks them to wait.
    assert "wait" in body["detail"].lower() or "try again" in body["detail"].lower()
    assert _FAKE_KEY not in res.text


def test_test_profile_network_when_probe_raises_provider_error(
    client: tuple[TestClient, _FakeKeyring, Settings],
) -> None:
    """ProviderError (bare) → reason=network + reachability advice."""
    c, _, _ = client
    profile = c.post("/api/profiles", json=_body_for(label="DeepSeek", api_key=_FAKE_KEY)).json()

    with _patch_factory(raises=ProviderError):
        res = c.post(f"/api/profiles/{profile['id']}/test")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["reason"] == "network"
    # Plain-language: names the next-action (check network).
    assert "network" in body["detail"].lower() or "reach" in body["detail"].lower()
    assert _FAKE_KEY not in res.text


def test_test_profile_other_when_probe_raises_unexpected_exception(
    client: tuple[TestClient, _FakeKeyring, Settings],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Anything not in the provider-error taxonomy → reason=other.

    Crucially the *exception string* must not appear in the response —
    SDK exceptions historically embedded auth headers in their str().
    The detail field uses static copy + the exception lands in the log
    with ``exc_info=True`` for the maintainer.
    """
    c, _, _ = client
    profile = c.post("/api/profiles", json=_body_for(label="DeepSeek", api_key=_FAKE_KEY)).json()

    leaky = "RuntimeError: Authorization: Bearer sk-LEAKY-CANARY-XXXXXXXXXX"

    class _LeakyError(Exception):
        pass

    def fake_factory(p: Profile, api_key: str) -> LLMClient:
        raise _LeakyError(leaky)

    from aitap.server.routes import profiles as routes_profiles_module

    with patch.object(routes_profiles_module, "get_client_for_profile", fake_factory):
        res = c.post(f"/api/profiles/{profile['id']}/test")

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["reason"] == "other"
    # The exception string must NOT appear in the response.
    assert "LEAKY" not in res.text
    assert "Bearer" not in res.text
    assert "Authorization" not in res.text
    # And neither does the configured key.
    assert _FAKE_KEY not in res.text


def test_test_profile_forwards_resolved_key_to_factory(
    client: tuple[TestClient, _FakeKeyring, Settings],
) -> None:
    """The handler resolves the key from the vault and passes it to the
    factory verbatim — this is the seam the rest of the multi-provider
    redesign rests on, so we pin it directly."""
    c, _, _ = client
    profile = c.post("/api/profiles", json=_body_for(label="DeepSeek", api_key=_FAKE_KEY)).json()

    captured: dict[str, object] = {}

    def fake_factory(p: Profile, api_key: str) -> LLMClient:
        captured["profile_id"] = p.id
        captured["api_key"] = api_key
        return _ScriptedProbeClient(p.model_id, api_key)

    from aitap.server.routes import profiles as routes_profiles_module

    with patch.object(routes_profiles_module, "get_client_for_profile", fake_factory):
        res = c.post(f"/api/profiles/{profile['id']}/test")
    assert res.status_code == 200, res.text
    assert captured["profile_id"] == profile["id"]
    assert captured["api_key"] == _FAKE_KEY


def test_test_unknown_profile_returns_404(
    client: tuple[TestClient, _FakeKeyring, Settings],
) -> None:
    c, _, _ = client
    res = c.post("/api/profiles/never-existed/test")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# 409 keyring-unavailable contract (parity with PR #35)
# ---------------------------------------------------------------------------


def test_create_profile_with_key_409s_when_keyring_unavailable_no_opt_in(
    client: tuple[TestClient, _FakeKeyring, Settings],
) -> None:
    """When the keyring is unreachable and the caller hasn't opted into
    the file fallback, ``POST /api/profiles`` returns 409 with a plain-
    language detail — same contract as PR #35's ``POST /api/settings/key``."""
    c, fake, _ = client
    fake.usable = False

    res = c.post(
        "/api/profiles",
        json=_body_for(label="DeepSeek", api_key=_FAKE_KEY),
    )
    assert res.status_code == 409, res.text
    detail = res.json()["detail"]
    # Plain-language remediation.
    assert "keychain" in detail.lower() or "key" in detail.lower()
    # The raw key is not in the body.
    assert _FAKE_KEY not in res.text
    # And the profile wasn't half-created.
    assert c.get("/api/profiles").json() == []


# ---------------------------------------------------------------------------
# Checkpoint 4: PUT /api/settings/defaults + GET /api/settings exposes them
# ---------------------------------------------------------------------------


def test_get_settings_exposes_defaults_field(
    client: tuple[TestClient, _FakeKeyring, Settings],
) -> None:
    """``GET /api/settings`` includes a ``defaults`` block — additive field."""
    c, _, _ = client
    res = c.get("/api/settings")
    assert res.status_code == 200
    body = res.json()
    assert "defaults" in body
    assert body["defaults"] == {
        "model_profile_id": None,
        "judge_profile_id": None,
    }


def test_put_defaults_picks_a_configured_profile_and_persists(
    client: tuple[TestClient, _FakeKeyring, Settings],
) -> None:
    """The UI's "save defaults" round-trip: PUT writes; GET reflects."""
    c, _, _ = client
    profile = c.post("/api/profiles", json=_body_for(label="DeepSeek")).json()
    pid = profile["id"]

    res = c.put(
        "/api/settings/defaults",
        json={"model_profile_id": pid, "judge_profile_id": pid},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body == {"model_profile_id": pid, "judge_profile_id": pid}

    # GET sees the same.
    later = c.get("/api/settings").json()
    assert later["defaults"] == {
        "model_profile_id": pid,
        "judge_profile_id": pid,
    }


def test_put_defaults_422s_when_referenced_profile_doesnt_exist(
    client: tuple[TestClient, _FakeKeyring, Settings],
) -> None:
    """Pointing defaults at a non-existent profile id is a 422 with a
    plain-language remediation, not a 500."""
    c, _, _ = client
    res = c.put(
        "/api/settings/defaults",
        json={"model_profile_id": "ghost-profile", "judge_profile_id": None},
    )
    assert res.status_code == 422, res.text
    detail = res.json()["detail"]
    assert "ghost-profile" in detail
    assert "Settings" in detail  # tells the user where to go next


def test_put_defaults_accepts_null_judge_to_reuse_default(
    client: tuple[TestClient, _FakeKeyring, Settings],
) -> None:
    """``judge_profile_id: null`` is the documented "reuse default" sentinel."""
    c, _, _ = client
    profile = c.post("/api/profiles", json=_body_for(label="DeepSeek")).json()

    res = c.put(
        "/api/settings/defaults",
        json={"model_profile_id": profile["id"], "judge_profile_id": None},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["model_profile_id"] == profile["id"]
    assert body["judge_profile_id"] is None


def test_create_profile_with_explicit_fallback_succeeds_when_keyring_down(
    client: tuple[TestClient, _FakeKeyring, Settings],
    tmp_path: Path,
) -> None:
    """Explicit opt-in (use_fallback=True) writes the key to
    ``~/.aitap/secrets.yaml`` even with the keyring unreachable, same as
    PR #35's behaviour for ``POST /api/settings/key``."""
    c, fake, _ = client
    fake.usable = False

    res = c.post(
        "/api/profiles",
        json=_body_for(label="DeepSeek", api_key=_FAKE_KEY, use_fallback=True),
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["key_configured"] is True
    assert body["key_source"] == "fallback"
    # Raw key not in response.
    assert _FAKE_KEY not in res.text
