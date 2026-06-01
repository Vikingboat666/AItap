"""End-to-end canary test for the multi-provider profile flow.

Plants a known fake key ``sk-fake-profile-CANARY-zzz...`` through the
new ``/api/profiles`` HTTP API, exercises ``create -> list -> test ->
delete`` on the live FastAPI ``TestClient``, then asserts the canary
appears in **exactly one** place: the recording LLM client's seen-keys
list. Specifically the canary must **not** appear:

- in any HTTP response body the server returned to us;
- in any log record emitted during the run
  (:func:`aitap.secrets.install_log_filter` should drop them);
- inside the project-level ``.aitap/`` directory (filesystem grep +
  SQLite column grep);
- inside the per-profile masked preview the API hands back.

This is the profile-id counterpart to ``test_secure_settings_e2e.py``
(PR #35). Once ``wt/profile-cleanup`` deletes the legacy provider-keyed
routes, that older test goes away — this one stays as the single
integration-level safety net for the profile-id secrets contract.
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
from aitap.deep import factory as factory_module
from aitap.deep.client import (
    ChatMessage,
    ChatResponse,
    CostEstimate,
    LLMClient,
    TokenUsage,
)
from aitap.server.app import create_app
from aitap.server.routes import Profile
from aitap.server.routes import profiles as profiles_routes

# Canary picked so a positive grep in any failure is immediately readable.
_CANARY = "sk-fake-profile-CANARY-zzzzzzzzzzzzzzzz1234"


class _FakeKeyring:
    """In-memory keyring backend, same shape as the PR #35 fake."""

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

    Mirrors PR #35's recording pattern. The profile flow resolves the
    key in ``routes/profiles.py`` and hands it to the factory; the
    factory then constructs this client with the key as the ``api_key``
    kwarg. By recording on chat() we pin both that the vault wired the
    key through *and* that no logging side-effect surfaced it.
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
        type(self).seen_keys.append(self.api_key)
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

    # 3. Strip env vars so they don't satisfy ``get_key_for_profile``.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    # 4. Project root for the .aitap/ tree.
    project_root = tmp_path / "proj"
    project_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AITAP_PROJECT_ROOT", str(project_root))

    # 5. Reset module-level profile cache so each test starts blank.
    profiles_routes.reset_state_for_tests()

    # 6. Build a fresh FastAPI app — the bootstrap installs the secret
    # log filter on the root logger, so we don't have to.
    app = create_app()
    with TestClient(app) as client:
        yield client, project_root, fake

    # Reset the recording client between tests.
    _RecordingClient.seen_keys = []
    _RecordingClient.seen_messages = []
    profiles_routes.reset_state_for_tests()


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


def test_canary_never_appears_outside_outbound_llm_call(
    isolated_e2e: tuple[TestClient, Path, _FakeKeyring],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plant the canary into a profile, exercise create/list/test/delete,
    assert the canary never leaks outside the recording LLM call.
    """
    client, project_root, _fake = isolated_e2e

    response_bodies: list[str] = []

    def _record(res: object) -> object:
        body = getattr(res, "text", "")
        if isinstance(body, str):
            response_bodies.append(body)
        return res

    caplog.set_level(logging.DEBUG)

    # 1. Inject the recording client at the factory seam so the probe
    # path constructs it instead of a real SDK client. The factory takes
    # ``(profile, api_key) -> LLMClient``; we ignore the profile shape
    # and return a recording client with the resolved key in tow.
    def _spy_factory(profile: Profile, api_key: str) -> LLMClient:
        return _RecordingClient(model=profile.model_id, api_key=api_key)

    monkeypatch.setattr(
        factory_module,
        "get_client_for_profile",
        _spy_factory,
    )
    # The route module imports the symbol at module load — patch that too.
    monkeypatch.setattr(
        profiles_routes,
        "get_client_for_profile",
        _spy_factory,
    )

    # 2. POST /api/profiles — plant the canary as the api_key.
    res = client.post(
        "/api/profiles",
        json={
            "label": "Recording",
            "base_url": "https://api.example.com/v1",
            "protocol": "openai-compat",
            "model_id": "fake-model",
            "api_key": _CANARY,
        },
    )
    _record(res)
    assert res.status_code in (200, 201), res.text
    created = res.json()
    profile_id = created["id"]
    assert created["key_configured"] is True
    assert created["key_source"] == "keyring"
    # The masked preview shows the last 4 chars but never the full key.
    assert created["key_masked"] != _CANARY
    assert _CANARY not in res.text

    # 3. GET /api/profiles — list must show the row without the raw key.
    res = client.get("/api/profiles")
    _record(res)
    assert res.status_code == 200
    assert _CANARY not in res.text
    listed = res.json()
    assert any(p["id"] == profile_id for p in listed)

    # 4. POST /api/profiles/{id}/test — the recording client gets the key.
    res = client.post(f"/api/profiles/{profile_id}/test")
    _record(res)
    assert res.status_code == 200, res.text
    test_body = res.json()
    assert test_body["ok"] is True
    assert _CANARY not in res.text

    # 5. DELETE /api/profiles/{id} — wipes the profile + the keyring entry.
    res = client.delete(f"/api/profiles/{profile_id}")
    _record(res)
    assert res.status_code == 200
    assert _CANARY not in res.text

    # ------------------------------------------------------------------
    # Assertions: where the canary may appear, and where it may not.
    # ------------------------------------------------------------------

    # The recording client *must* have seen the canary — proves the vault
    # actually wired the key into the SDK call site.
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
    # dependency override. It must not contain the canary in any file or
    # SQLite column.
    aitap_dir = project_root / ".aitap"
    file_hits = _scan_dir_for(aitap_dir, _CANARY)
    assert file_hits == [], f"canary leaked into project-level .aitap/ files: {file_hits}"
    db_path = aitap_dir / "db.sqlite"
    db_hits = _scan_sqlite_for(db_path, _CANARY)
    assert db_hits == [], f"canary leaked into project .aitap SQLite columns: {db_hits}"

    # Defence-in-depth: nothing outside ``~/.aitap/secrets.yaml`` may carry
    # the canary either (and even that one only exists if the keyring
    # backend was unreachable — in this test it wasn't, so the file
    # shouldn't be there at all).
    home_dir = Path.home()
    aitap_in_home = home_dir / ".aitap"
    allowed = {(aitap_in_home / "secrets.yaml").resolve()}
    home_hits = [p for p in _scan_dir_for(aitap_in_home, _CANARY) if p.resolve() not in allowed]
    assert home_hits == [], f"canary leaked into ~/.aitap files outside secrets.yaml: {home_hits}"


def test_test_endpoint_short_circuits_when_profile_has_no_key(
    isolated_e2e: tuple[TestClient, Path, _FakeKeyring],
) -> None:
    """A profile created without an api_key must surface a plain-language
    reason instead of trying to instantiate the LLM client.
    """
    client, _, _ = isolated_e2e

    # Create a profile but omit the api_key field.
    res = client.post(
        "/api/profiles",
        json={
            "label": "Keyless",
            "base_url": "https://api.example.com/v1",
            "protocol": "openai-compat",
            "model_id": "fake-model",
        },
    )
    assert res.status_code in (200, 201), res.text
    profile_id = res.json()["id"]
    assert res.json()["key_configured"] is False

    # The test endpoint should short-circuit without ever hitting the
    # factory — if it did try to construct an SDK client without a key,
    # this test would fail loudly (the real factory would 500).
    res = client.post(f"/api/profiles/{profile_id}/test")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is False
    assert body["reason"] == "auth"
    # Plain-language: mentions Settings as the next step.
    assert "Settings" in body["detail"]


def test_list_endpoint_never_serialises_a_raw_key(
    isolated_e2e: tuple[TestClient, Path, _FakeKeyring],
) -> None:
    """Even with several profiles configured, GET /api/profiles never
    serialises a raw key — only the masked preview.
    """
    client, _, _ = isolated_e2e

    canaries = {
        "Alpha": "sk-fake-AAAAAAAAAAAAAAAAAAAAAAAA1",
        "Beta": "sk-fake-BBBBBBBBBBBBBBBBBBBBBBBB2",
    }
    for label, key in canaries.items():
        res = client.post(
            "/api/profiles",
            json={
                "label": label,
                "base_url": "https://api.example.com/v1",
                "protocol": "openai-compat",
                "model_id": "fake-model",
                "api_key": key,
            },
        )
        assert res.status_code in (200, 201), res.text

    res = client.get("/api/profiles")
    assert res.status_code == 200
    raw = res.text
    for label, key in canaries.items():
        assert key not in raw, f"GET /api/profiles leaked the raw key for {label}"

    body = res.json()
    # Every row must report configured + a masked preview ending with the
    # last 4 chars of its canary.
    by_label = {p["label"]: p for p in body}
    for label, key in canaries.items():
        masked = by_label[label]["key_masked"]
        assert masked is not None
        assert masked.endswith(key[-4:])
        assert masked != key
