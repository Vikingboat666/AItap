"""Integration tests for the runs + settings API surface (Wave 3).

The tests run against an in-process FastAPI app via ``httpx.AsyncClient``
+ ``ASGITransport`` — no network, no uvicorn. We override the Settings
dependency so every test gets its own tmp_path-rooted database, keeping
the suite hermetic and parallel-safe.

What's covered:

- ``POST /api/runs`` accepts a valid payload, returns ``RunResponse``, and
  persists a row in the ``runs`` table.
- ``GET /api/runs`` lists runs and respects the ``target_id`` filter.
- ``GET /api/runs/{id}`` returns 200 with the contract shape and 404 on
  unknown ids.
- ``POST /api/runs/{id}/feedback`` writes a row to ``feedback`` and
  returns the autoincremented id.
- ``POST /api/runs/{id}/iterate`` writes a new ``prompt_versions`` row
  attributed to ``created_by='iteration'``.
- ``GET /api/settings`` reflects the active Settings and detected providers.
- ``PUT /api/settings`` updates the override layer; subsequent GETs see it.
- ``GET /api/settings/cost-estimate`` returns a >0 USD estimate for a known
  model and 400 for an unpriced one.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import cast

import pytest
from httpx import ASGITransport, AsyncClient

from aitap.config import Settings
from aitap.server.app import create_app
from aitap.server.deps import get_conn, get_settings
from aitap.server.routes import settings as settings_routes
from aitap.store import db as store_db


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    """A throwaway Settings rooted at tmp_path with .aitap pre-created."""
    aitap_dir = tmp_path / ".aitap"
    (aitap_dir / "prompts").mkdir(parents=True)
    (aitap_dir / "pipelines").mkdir()
    (aitap_dir / "datasets").mkdir()
    (aitap_dir / "runs").mkdir()
    return Settings(project_root=tmp_path)


@pytest.fixture()
def app_with_settings(settings: Settings):
    """Build a fresh FastAPI app and inject our tmp Settings via DI override."""
    application = create_app()
    application.dependency_overrides[get_settings] = lambda: settings
    return application


@pytest.fixture(autouse=True)
def _reset_mutable_state() -> Iterator[None]:
    """Clear the in-memory settings override layer between tests.

    ``settings_routes._MUTABLE_STATE`` is module-level by design (single
    process holding overrides) but bleeds across tests if we don't reset.
    """
    settings_routes._MUTABLE_STATE.clear()
    yield
    settings_routes._MUTABLE_STATE.clear()


@pytest.fixture()
async def client(app_with_settings) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app_with_settings)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


def _run_payload(target_id: str = "prompt-abc", version: int = 1) -> dict[str, object]:
    """Build a minimal RunCreate payload accepted by the API."""
    return {
        "target_kind": "prompt",
        "target_id": target_id,
        "target_version": version,
        "cases": [],
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "parameters": {"temperature": 0.2},
    }


def _open_conn(settings: Settings) -> sqlite3.Connection:
    conn = store_db.connect(settings.db_path)
    store_db.init_db(conn)
    return conn


def _seed_prompt(settings: Settings, prompt_id: str = "prompt-abc") -> None:
    """Insert a minimal ``prompts`` row so FK-bound writes don't 500.

    The scanner normally fills this in during ``aitap scan``; tests that
    skip the scanner need to provide their own placeholder.
    """
    conn = _open_conn(settings)
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO prompts
                (id, name, provider, file, line_start, line_end,
                 confidence, payload_json)
            VALUES (?, 'test_prompt', 'anthropic', 'x.py', 1, 5, 'high', '{}')
            """,
            (prompt_id,),
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


async def test_post_run_persists_row_and_returns_run_id(
    client: AsyncClient,
    settings: Settings,
) -> None:
    resp = await client.post("/api/runs", json=_run_payload())
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert "run_id" in body
    assert body["status"] in {"running", "done"}

    # The row landed in SQLite with our payload's metadata.
    conn = _open_conn(settings)
    try:
        cur = conn.execute("SELECT * FROM runs WHERE id = ?", (body["run_id"],))
        row = cur.fetchone()
        assert row is not None
        assert row["target_id"] == "prompt-abc"
        assert row["target_version"] == 1
        assert row["provider"] == "anthropic"
        assert row["model"] == "claude-sonnet-4-6"
        # parameters are stored as serialised JSON; round-trip the temperature.
        assert "0.2" in cast(str, row["parameters_json"])
    finally:
        conn.close()


async def test_list_runs_filters_by_target(client: AsyncClient) -> None:
    await client.post("/api/runs", json=_run_payload("prompt-aaa", 1))
    await client.post("/api/runs", json=_run_payload("prompt-bbb", 1))
    await client.post("/api/runs", json=_run_payload("prompt-aaa", 2))

    resp = await client.get("/api/runs", params={"target_id": "prompt-aaa"})
    assert resp.status_code == 200
    ids = [r["run_id"] for r in resp.json()["runs"]]
    assert len(ids) == 2

    resp_all = await client.get("/api/runs")
    assert resp_all.status_code == 200
    assert len(resp_all.json()["runs"]) == 3


async def test_get_run_404_for_unknown(client: AsyncClient) -> None:
    resp = await client.get("/api/runs/does-not-exist")
    assert resp.status_code == 404


async def test_get_run_returns_detail_shape(client: AsyncClient) -> None:
    posted = (await client.post("/api/runs", json=_run_payload())).json()
    resp = await client.get(f"/api/runs/{posted['run_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == posted["run_id"]
    assert body["target_id"] == "prompt-abc"
    assert body["target_version"] == 1
    assert body["status"] in {"running", "done", "failed"}
    assert body["outputs"] == []
    assert body["cost_usd"] == 0.0
    assert body["started_at"]


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------


async def test_post_feedback_writes_row_and_returns_id(
    client: AsyncClient,
    settings: Settings,
) -> None:
    posted = (await client.post("/api/runs", json=_run_payload())).json()
    feedback_resp = await client.post(
        f"/api/runs/{posted['run_id']}/feedback",
        json={
            "case_index": 0,
            "rating": 1,
            "ideal_answer": "Hello world.",
            "critique": "Closer but still verbose.",
        },
    )
    assert feedback_resp.status_code == 201, feedback_resp.text
    assert feedback_resp.json()["feedback_id"] >= 1

    conn = _open_conn(settings)
    try:
        cur = conn.execute("SELECT * FROM feedback WHERE run_id = ?", (posted["run_id"],))
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0]["rating"] == 1
        assert rows[0]["critique"] == "Closer but still verbose."
    finally:
        conn.close()


async def test_post_feedback_404_for_unknown_run(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/runs/unknown-run/feedback",
        json={"case_index": 0, "rating": 0},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Iterate
# ---------------------------------------------------------------------------


async def test_post_iterate_creates_new_prompt_version(
    client: AsyncClient,
    settings: Settings,
) -> None:
    _seed_prompt(settings)
    posted = (await client.post("/api/runs", json=_run_payload())).json()
    await client.post(
        f"/api/runs/{posted['run_id']}/feedback",
        json={"case_index": 0, "rating": -1, "critique": "wrong tone"},
    )

    resp = await client.post(
        f"/api/runs/{posted['run_id']}/iterate",
        json={"max_iterations": 1, "convergence_threshold": 0.85},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["new_version"] == 1
    assert body["converged"] is False
    assert body["downstream_impact"] == []

    conn = _open_conn(settings)
    try:
        cur = conn.execute(
            """
            SELECT * FROM prompt_versions
            WHERE prompt_id = ? ORDER BY version DESC LIMIT 1
            """,
            ("prompt-abc",),
        )
        row = cur.fetchone()
        assert row is not None
        assert row["created_by"] == "iteration"
        assert row["version"] == 1
        # The stub copies parent_version through so the audit trail
        # can reconstruct which run/version drove the iteration.
        assert row["parent_version"] == 1
    finally:
        conn.close()


async def test_iterate_400_for_unknown_run(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/runs/never-existed/iterate",
        json={"max_iterations": 1},
    )
    assert resp.status_code == 400


async def test_iterate_bumps_version_on_subsequent_calls(
    client: AsyncClient,
    settings: Settings,
) -> None:
    """Two iterate calls against the same target advance the version monotonically."""
    _seed_prompt(settings)
    posted = (await client.post("/api/runs", json=_run_payload())).json()
    first = (
        await client.post(
            f"/api/runs/{posted['run_id']}/iterate",
            json={"max_iterations": 1},
        )
    ).json()
    second = (
        await client.post(
            f"/api/runs/{posted['run_id']}/iterate",
            json={"max_iterations": 1},
        )
    ).json()
    assert second["new_version"] == first["new_version"] + 1

    conn = _open_conn(settings)
    try:
        rows = conn.execute(
            "SELECT version FROM prompt_versions WHERE prompt_id = ? ORDER BY version",
            ("prompt-abc",),
        ).fetchall()
        assert [r["version"] for r in rows] == [1, 2]
    finally:
        conn.close()


async def test_iterate_concurrent_calls_do_not_collide(
    app_with_settings,
    settings: Settings,
) -> None:
    """Concurrent ``POST /iterate`` calls don't collide on (prompt_id, version).

    Regression test for B6: ``iterate_one_round`` used to read
    ``MAX(version)`` and then INSERT ``MAX+1`` in two separate statements
    on an autocommit connection. With ten concurrent calls both readers
    would compute the same ``next_version`` and the loser would hit a
    primary-key collision (HTTP 500). The fix wraps the read-modify-write
    in a ``BEGIN IMMEDIATE`` transaction so each request sees the previous
    one's commit before computing its slot. We exercise a higher fan-out
    than two here to stress the lock-ordering, and require *all* requests
    to succeed with distinct, monotonically increasing versions.
    """
    _seed_prompt(settings)
    # Each request needs its own client because httpx.AsyncClient
    # serialises requests through a single connection pool — the goal is
    # to interleave at the server / DB layer, not the transport layer.
    n_calls = 5

    async def _one_iterate() -> dict[str, object]:
        transport = ASGITransport(app=app_with_settings)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            posted = (await ac.post("/api/runs", json=_run_payload())).json()
            resp = await ac.post(
                f"/api/runs/{posted['run_id']}/iterate",
                json={"max_iterations": 1},
            )
        return {"status": resp.status_code, "body": resp.json()}

    results = await asyncio.gather(*[_one_iterate() for _ in range(n_calls)])

    statuses = [r["status"] for r in results]
    assert statuses == [201] * n_calls, results

    bodies = [cast(dict[str, object], r["body"]) for r in results]
    versions = sorted(int(cast(int, b["new_version"])) for b in bodies)
    # No duplicates and the set covers exactly 1..n.
    assert versions == list(range(1, n_calls + 1)), versions

    conn = _open_conn(settings)
    try:
        rows = conn.execute(
            "SELECT version FROM prompt_versions WHERE prompt_id = ? ORDER BY version",
            ("prompt-abc",),
        ).fetchall()
        assert [r["version"] for r in rows] == list(range(1, n_calls + 1))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


async def test_get_settings_reflects_defaults(client: AsyncClient) -> None:
    resp = await client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "anthropic"
    assert body["model"] == "claude-sonnet-4-6"
    assert body["cost_per_run_usd"] == 1.00
    assert body["cost_per_session_usd"] == 10.00
    # No scan has been run in this fixture, so no providers were detected.
    assert body["providers_available"] == []


async def test_put_settings_partial_update(client: AsyncClient) -> None:
    resp = await client.put(
        "/api/settings",
        json={"model": "claude-opus-4-7", "cost_per_run_usd": 2.50},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["model"] == "claude-opus-4-7"
    assert body["cost_per_run_usd"] == 2.50
    # Untouched fields fall through to defaults.
    assert body["provider"] == "anthropic"

    # Subsequent GET sees the override.
    persisted = await client.get("/api/settings")
    assert persisted.json()["model"] == "claude-opus-4-7"


async def test_get_settings_lists_detected_providers(
    client: AsyncClient,
    settings: Settings,
) -> None:
    """Seed providers_detected directly and ensure GET surfaces them."""
    from aitap.scanner.models import (
        CodeLocation,
        Provider,
        ProviderEvidence,
    )

    conn = _open_conn(settings)
    try:
        store_db.record_provider_evidence(
            conn,
            str(settings.project_root),
            ProviderEvidence(
                provider=Provider.OPENAI,
                source=".env",
                location=CodeLocation(file=".env", line_start=1, line_end=1),
                key_var_name="OPENAI_API_KEY",
            ),
        )
    finally:
        conn.close()

    body = (await client.get("/api/settings")).json()
    assert any(p["provider"] == "openai" for p in body["providers_available"])


async def test_cost_estimate_known_model(
    client: AsyncClient,
    settings: Settings,
) -> None:
    """Seed a prompt_versions row so the estimator has template text to hash.

    The default project provider is anthropic (see :class:`ProviderConfig`),
    so we query a known anthropic model to stay on-provider — the
    cost-estimate endpoint refuses to silently price cross-provider since
    the B5 fix.
    """
    conn = _open_conn(settings)
    try:
        conn.execute(
            """
            INSERT INTO prompts (id, name, provider, file, line_start, line_end,
                                 confidence, payload_json)
            VALUES ('prompt-cost', 'cost_target', 'anthropic', 'x.py', 1, 5,
                    'high', '{}')
            """
        )
        conn.execute(
            """
            INSERT INTO prompt_versions
                (prompt_id, version, template_json, parameters_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                "prompt-cost",
                1,
                '[{"role":"user","template_text":"Summarise this email politely."}]',
                "{}",
            ),
        )
    finally:
        conn.close()

    resp = await client.get(
        "/api/settings/cost-estimate",
        params={"prompt_id": "prompt-cost", "model": "claude-sonnet-4-6"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["model"] == "claude-sonnet-4-6"
    assert body["estimated_tokens"] > 0
    assert body["estimated_usd"] >= 0


async def test_cost_estimate_400_on_provider_mismatch(
    client: AsyncClient,
    settings: Settings,
) -> None:
    """Asking for an OpenAI model on an anthropic-configured project 400s.

    Regression test for B5: the previous implementation silently re-priced
    the request against the OpenAI table and returned a number the user
    would then treat as authoritative. The fix surfaces the misconfig as
    a 400 whose ``detail`` names both the offending model and the
    configured provider so the user can switch one or the other.
    """
    conn = _open_conn(settings)
    try:
        conn.execute(
            """
            INSERT INTO prompts (id, name, provider, file, line_start, line_end,
                                 confidence, payload_json)
            VALUES ('prompt-cost', 'cost_target', 'anthropic', 'x.py', 1, 5,
                    'high', '{}')
            """
        )
        conn.execute(
            """
            INSERT INTO prompt_versions
                (prompt_id, version, template_json, parameters_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                "prompt-cost",
                1,
                '[{"role":"user","template_text":"Pick a date."}]',
                "{}",
            ),
        )
    finally:
        conn.close()

    resp = await client.get(
        "/api/settings/cost-estimate",
        params={"prompt_id": "prompt-cost", "model": "gpt-4o-mini"},
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert "'gpt-4o-mini'" in detail
    assert "'anthropic'" in detail


async def test_cost_estimate_unknown_model_returns_400(
    client: AsyncClient,
    settings: Settings,
) -> None:
    conn = _open_conn(settings)
    try:
        conn.execute(
            """
            INSERT INTO prompts (id, name, provider, file, line_start, line_end,
                                 confidence, payload_json)
            VALUES ('prompt-cost', 'cost_target', 'openai', 'x.py', 1, 5,
                    'high', '{}')
            """
        )
        conn.execute(
            """
            INSERT INTO prompt_versions
                (prompt_id, version, template_json, parameters_json)
            VALUES ('prompt-cost', 1, '[]', '{}')
            """
        )
    finally:
        conn.close()
    resp = await client.get(
        "/api/settings/cost-estimate",
        params={"prompt_id": "prompt-cost", "model": "no-such-model"},
    )
    assert resp.status_code == 400


async def test_cost_estimate_404_for_unknown_prompt(client: AsyncClient) -> None:
    resp = await client.get(
        "/api/settings/cost-estimate",
        params={"prompt_id": "missing", "model": "gpt-4o-mini"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Smoke test for the deps helper itself
# ---------------------------------------------------------------------------


def test_get_conn_initialises_schema(settings: Settings) -> None:
    """A direct call to get_conn must yield a connection with all tables ready."""
    with get_conn(settings) as conn:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = {row["name"] for row in cur.fetchall()}
        assert {"runs", "scores", "feedback", "prompt_versions"}.issubset(tables)
