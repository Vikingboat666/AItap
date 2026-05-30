"""End-to-end canary test for the secure-settings flow.

Plants a known fake key ``sk-fake-anthropic-E2E-CANARY-zzz...`` through
the HTTP API, exercises save -> test -> delete on the live FastAPI
``TestClient``, runs a tiny prompt via the playground dispatch path
(with the LLM client mocked so no real HTTP is made), then asserts the
canary appears in **exactly one** place: the mocked LLM client's
recorded outbound calls.

Specifically we assert it does **not** appear:

- in any HTTP response body the server returned to us;
- in any log record emitted during the run (the
  :func:`aitap.secrets.install_log_filter` filter is on);
- inside the project-level ``.aitap/`` directory (filesystem grep +
  SQLite column grep);
- inside the masked-preview the API hands back to the client.

The test is the single integration-level safety net for the secrets
contract — every individual unit test exercises one face of it; this
one ties them together by running an end-to-end realistic scenario.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar, Literal

import pytest
from fastapi.testclient import TestClient

from aitap import secrets as secrets_module
from aitap.deep.client import (
    ChatMessage,
    ChatResponse,
    CostEstimate,
    LLMClient,
    TokenUsage,
)
from aitap.server.app import create_app

# Canary picked so a positive grep in any failure is immediately readable.
_CANARY = "sk-ant-FAKE-CANARY-zzzzzzzzzzzzzzzz1234"


class _FakeKeyring:
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


class _RecordingClient(LLMClient):
    """LLMClient that records the resolved api_key at chat() time.

    Real SDK clients resolve the key lazily inside the chat body
    (``_resolve_api_key`` -> ``secrets.get_key``). We mirror that here so
    the test exercises the same code path the production clients use.
    By recording the resolved key on chat() we verify two things at once:

    1. The vault successfully wires the key through to the SDK
       call point (so a UI-saved key reaches a real run).
    2. The dispatch + settings layer never logs / persists the key on
       its way through — only the recorded ``seen_keys`` list (which we
       don't grep) has it.
    """

    seen_keys: ClassVar[list[str | None]] = []
    seen_messages: ClassVar[list[list[ChatMessage]]] = []

    def __init__(self, model: str, api_key: str | None = None) -> None:
        super().__init__(model, api_key)

    @property
    def provider_name(self) -> str:
        return "recording"

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        response_format: Literal["text", "json"] | None = None,
    ) -> ChatResponse:
        # Resolve the key the same way the real SDK clients do —
        # constructor-arg first, then vault. This is the *only*
        # production-realistic moment when the key needs to be in
        # memory; recording it here pins the round-trip.
        resolved = self.api_key or secrets_module.get_key("anthropic")
        type(self).seen_keys.append(resolved)
        type(self).seen_messages.append(list(messages))
        return ChatResponse(
            text="ok",
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


@pytest.fixture()
def isolated_e2e(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, Path, _FakeKeyring]]:
    """Spin up an isolated FastAPI app with the vault under tmp_path."""
    # 1. Redirect Path.home() so the vault writes into tmp_path.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    # 2. Plug an in-memory keyring fake.
    fake = _FakeKeyring()
    monkeypatch.setattr(secrets_module, "_keyring_module", lambda: fake)
    monkeypatch.setattr(secrets_module, "_keyring_usable", lambda: fake.usable)

    # 3. Strip env vars so they don't satisfy ``get_key`` accidentally.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    # 4. Project-level .aitap/ goes under a fresh dir so the persistence
    # grep test has a clean target.
    project_root = tmp_path / "proj"
    project_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AITAP_PROJECT_ROOT", str(project_root))

    # 5. Build a fresh FastAPI app — the bootstrap installs the secret
    # log filter on the root logger, so we don't have to.
    app = create_app()
    with TestClient(app) as client:
        yield client, project_root, fake

    # Reset the recording client between tests.
    _RecordingClient.seen_keys = []
    _RecordingClient.seen_messages = []


def _scan_dir_for(directory: Path, needle: str) -> list[Path]:
    out: list[Path] = []
    if not directory.exists():
        return out
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if needle.encode() in data:
            out.append(path)
    return out


def _scan_sqlite_for(db_path: Path, needle: str) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    if not db_path.exists():
        return hits
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        tables = [row[0] for row in cur.fetchall()]
        for table in tables:
            cur = conn.execute(f"PRAGMA table_info({table})")
            columns = [row[1] for row in cur.fetchall()]
            for col in columns:
                try:
                    cur = conn.execute(
                        f"SELECT 1 FROM {table} WHERE CAST({col} AS TEXT) LIKE ? LIMIT 1",
                        (f"%{needle}%",),
                    )
                except sqlite3.Error:
                    continue
                if cur.fetchone() is not None:
                    hits.append((table, col))
    finally:
        conn.close()
    return hits


def test_canary_never_appears_outside_outbound_llm_calls(
    isolated_e2e: tuple[TestClient, Path, _FakeKeyring],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, project_root, _fake = isolated_e2e

    # Capture every response body so we can grep them at the end.
    response_bodies: list[str] = []

    def _record(res: object) -> object:
        # We can't easily intercept response bodies via TestClient, so
        # the test just stashes ``res.text`` after each call. The helper
        # is purely for ergonomics.
        body = getattr(res, "text", "")
        if isinstance(body, str):
            response_bodies.append(body)
        return res

    caplog.set_level(logging.DEBUG)

    # 1. GET /api/settings — baseline shows unconfigured.
    _record(client.get("/api/settings"))

    # 2. POST /api/settings/key — plant the canary.
    res = client.post(
        "/api/settings/key",
        json={"provider": "anthropic", "key": _CANARY},
    )
    _record(res)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["configured"] is True
    assert body["source"] == "keyring"
    # The masked preview is the only key-like string we should see.
    assert body["masked"] != _CANARY
    assert _CANARY not in res.text

    # 3. POST /api/settings/test/anthropic — patch the registry so the
    # outbound call goes through the recording client.
    from aitap.deep import client as client_module

    real_get = client_module.get_client

    def _spy_factory(provider: str, model: str, api_key: str | None = None) -> LLMClient:
        return _RecordingClient(model=model, api_key=api_key)

    client_module.get_client = _spy_factory  # type: ignore[assignment]
    try:
        res = client.post("/api/settings/test/anthropic")
        _record(res)
        assert res.status_code == 200, res.text
        test_body = res.json()
        assert test_body["ok"] is True
        assert _CANARY not in res.text
    finally:
        client_module.get_client = real_get

    # 4. DELETE /api/settings/key/anthropic — wipe the canary.
    res = client.delete("/api/settings/key/anthropic")
    _record(res)
    assert res.status_code == 200
    assert _CANARY not in res.text

    # ------------------------------------------------------------------
    # Assertions: where the canary may appear, and where it may not.
    # ------------------------------------------------------------------

    # The recording client *must* have seen the key — that's the whole
    # point of plumbing it through. Without this assertion we're not
    # proving the happy path works, only that nothing leaked.
    assert _CANARY in _RecordingClient.seen_keys, (
        "the vault never handed the canary to the LLM client constructor"
    )

    # No response body may include the canary.
    for idx, raw in enumerate(response_bodies):
        assert _CANARY not in raw, f"response body #{idx} leaked the canary:\n{raw}"

    # No log record may include the canary — the secret log filter
    # should have dropped any that tried.
    for record in caplog.records:
        message = record.getMessage()
        assert _CANARY not in message, (
            f"log record leaked the canary: {record.levelname} {message!r}"
        )

    # The project-level .aitap/ tree was created by the FastAPI app's
    # dependency override (Settings + db init). It must not contain the
    # canary in any file or SQLite column.
    aitap_dir = project_root / ".aitap"
    file_hits = _scan_dir_for(aitap_dir, _CANARY)
    assert file_hits == [], f"canary leaked into project-level .aitap/ files: {file_hits}"
    db_path = aitap_dir / "db.sqlite"
    db_hits = _scan_sqlite_for(db_path, _CANARY)
    assert db_hits == [], f"canary leaked into project .aitap SQLite columns: {db_hits}"

    # The fallback file (if any) doesn't apply here because we used the
    # keyring path; but as defence-in-depth we also assert no
    # non-secrets.yaml file under the temp HOME has the canary.
    home_dir = Path.home()
    aitap_in_home = home_dir / ".aitap"
    allowed = {(aitap_in_home / "secrets.yaml").resolve()}
    home_hits = [p for p in _scan_dir_for(aitap_in_home, _CANARY) if p.resolve() not in allowed]
    assert home_hits == [], f"canary leaked into ~/.aitap files outside secrets.yaml: {home_hits}"


def test_keys_field_is_round_trip_via_generated_contract(
    isolated_e2e: tuple[TestClient, Path, _FakeKeyring],
) -> None:
    """Smoke-test the additive contract: GET /api/settings must include
    a ``keys`` array whose entries match the new ProviderKeyStatus
    shape, and the masked field for an unconfigured provider is null.
    """
    client, _, _ = isolated_e2e
    res = client.get("/api/settings")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body["keys"], list)
    assert {k["provider"] for k in body["keys"]} == {"anthropic", "openai"}
    for entry in body["keys"]:
        # Every key must report all four required fields.
        assert set(entry.keys()) >= {"provider", "configured", "source", "masked"}
        if not entry["configured"]:
            assert entry["source"] == "none"
            assert entry["masked"] is None


def test_test_endpoint_short_circuits_when_no_key(
    isolated_e2e: tuple[TestClient, Path, _FakeKeyring],
) -> None:
    """If no key is configured, the test endpoint must reach a plain-language
    reason without trying to instantiate the LLM client."""
    client, _, _ = isolated_e2e
    # Don't patch the registry — if we hit the real ``get_client`` and
    # try to construct the SDK without a key, this test fails loudly.
    res = client.post("/api/settings/test/anthropic")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is False
    assert body["reason"] == "auth"
    # Plain-language: mentions Settings as the next step.
    assert "Settings" in body["detail"]


def test_no_keys_array_contains_a_raw_key_for_any_provider(
    isolated_e2e: tuple[TestClient, Path, _FakeKeyring],
) -> None:
    """Even with both providers configured, GET /api/settings.keys never
    serialises the raw key — only the masked preview."""
    client, _, _ = isolated_e2e

    # Plant two distinct canaries so the assertion below can pin which
    # value leaked, if any.
    a_canary = "sk-ant-FAKE-AAAAAAAAAAAAAAAAAAAAAA1"
    o_canary = "sk-FAKE-BBBBBBBBBBBBBBBBBBBBBBBBBB2"
    client.post("/api/settings/key", json={"provider": "anthropic", "key": a_canary})
    client.post("/api/settings/key", json={"provider": "openai", "key": o_canary})

    res = client.get("/api/settings")
    assert res.status_code == 200
    raw = res.text
    assert a_canary not in raw, "GET /api/settings leaked Anthropic raw key"
    assert o_canary not in raw, "GET /api/settings leaked OpenAI raw key"

    body = res.json()
    # Both keys must report configured + a masked preview that ends with
    # the last 4 chars of the canary — not the full canary.
    by_provider = {k["provider"]: k for k in body["keys"]}
    assert by_provider["anthropic"]["masked"] == "sk-ant-...AAA1"
    assert by_provider["openai"]["masked"] == "sk-...BBB2"
