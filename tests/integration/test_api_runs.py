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
from aitap.deep.testing import MockLLMClient
from aitap.playground import dispatch as playground_dispatch
from aitap.scanner.models import (
    CodeLocation,
    Confidence,
    EdgeKind,
    Message,
    Pipeline,
    PipelineEdge,
    PipelineNode,
    PromptSite,
    Provider,
    Role,
    TemplateKind,
)
from aitap.server.app import create_app
from aitap.server.routes._deps import get_db, get_settings
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
def _reset_profiles_state() -> Iterator[None]:
    """Drop the profiles router's in-memory cache between tests.

    Contract v3 removed the legacy ``settings_routes._MUTABLE_STATE``
    override layer. The profiles router holds the live ``_PROFILES`` +
    ``_defaults`` cache now; resetting it gives every test the
    documented "no profiles configured" starting state.
    """
    from aitap.server.routes import profiles as profiles_routes

    profiles_routes.reset_state_for_tests()
    yield
    profiles_routes.reset_state_for_tests()


@pytest.fixture(autouse=True)
def _mock_invoke_run_client() -> Iterator[None]:
    """Inject a :class:`MockLLMClient` into ``invoke_run`` for the test run.

    Without this every ``POST /api/runs`` would try to construct a real
    Anthropic client. Construction itself is cheap (no network) but the
    test suite must stay hermetic and provider-key-free. Tests that care
    about actual chat behaviour can override this fixture locally; the
    default ``cases=[]`` payload makes zero calls anyway, so the mock
    just covers the "construction doesn't talk to the network" promise.
    """
    playground_dispatch.set_client_factory(lambda provider, model: MockLLMClient(model=model))
    yield
    playground_dispatch.set_client_factory(None)


@pytest.fixture(autouse=True)
def _seed_default_prompts(settings: Settings) -> None:
    """Pre-seed prompt rows that every test's ``_run_payload`` references.

    Wave 3 ``invoke_run`` resolves ``payload.target_id`` against the
    ``prompts`` table and raises (causing the run to flip to ``failed``)
    when no row exists. The integration suite uses synthetic ids
    (``prompt-abc``, ``prompt-aaa``, ``prompt-bbb``, ``prompt-cost``) so
    we seed all of them up front rather than scatter ``_seed_prompt``
    calls across every test.
    """
    for prompt_id in ("prompt-abc", "prompt-aaa", "prompt-bbb"):
        _seed_prompt(settings, prompt_id)


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


def _build_prompt_site(prompt_id: str, name: str = "test_prompt") -> PromptSite:
    """Construct a minimal valid PromptSite the dispatch adapter can load.

    The fields kept here are the union of what
    :func:`aitap.playground.dispatch._load_prompt_site` validates and
    what :func:`aitap.playground.runner.run_prompt` reads — anything else
    can stay defaulted.
    """
    return PromptSite(
        id=prompt_id,
        name=name,
        provider=Provider.ANTHROPIC,
        location=CodeLocation(file="x.py", line_start=1, line_end=5),
        messages=[
            Message(
                role=Role.USER,
                template_text="Hello, world.",
                template_kind=TemplateKind.LITERAL,
            )
        ],
        confidence=Confidence.HIGH,
    )


def _seed_prompt(settings: Settings, prompt_id: str = "prompt-abc") -> None:
    """Insert a real PromptSite row so the dispatch adapter can resolve it.

    The scanner normally fills this in during ``aitap scan``; tests that
    skip the scanner need to provide their own row whose
    ``payload_json`` deserialises back into a valid PromptSite.
    """
    site = _build_prompt_site(prompt_id)
    conn = _open_conn(settings)
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO prompts
                (id, name, provider, file, line_start, line_end,
                 confidence, payload_json)
            VALUES (?, ?, 'anthropic', 'x.py', 1, 5, 'high', ?)
            """,
            (prompt_id, site.name, site.model_dump_json()),
        )
    finally:
        conn.close()


def _seed_two_node_pipeline(settings: Settings, pipeline_id: str = "pipe-api") -> Pipeline:
    """Seed two prompt sites + a two-node A→B pipeline row.

    The pipeline-mode validation tests need a real pipeline in the store so
    a *valid* request 202s end-to-end (the dispatch adapter resolves the
    target). The 422 path short-circuits in the route before dispatch, but
    seeding keeps every test consistent.
    """
    _seed_prompt(settings, "node-a")
    _seed_prompt(settings, "node-b")
    pipeline = Pipeline(
        id=pipeline_id,
        name="api_two_step",
        nodes=[PipelineNode(prompt_id="node-a"), PipelineNode(prompt_id="node-b")],
        edges=[PipelineEdge(source="node-a", target="node-b", kind=EdgeKind.VARIABLE, via="value")],
        entry_points=["node-a"],
        exit_points=["node-b"],
    )
    conn = _open_conn(settings)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO pipelines (id, name, payload_json) VALUES (?, ?, ?)",
            (pipeline.id, pipeline.name, pipeline.model_dump_json()),
        )
    finally:
        conn.close()
    return pipeline


def _pipeline_run_payload(
    pipeline_id: str = "pipe-api",
    *,
    mode: str | None = None,
    node_id: str | None = None,
    segment: list[str] | None = None,
) -> dict[str, object]:
    body: dict[str, object] = {
        "target_kind": "pipeline",
        "target_id": pipeline_id,
        "target_version": 1,
        "cases": [],
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "parameters": {"temperature": 0.0},
    }
    if mode is not None:
        body["pipeline_mode"] = mode
    if node_id is not None:
        body["pipeline_node_id"] = node_id
    if segment is not None:
        body["pipeline_segment"] = segment
    return body


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
    # After B3, the dispatch adapter runs synchronously: the run must be
    # terminal by the time the route returns. ``done`` is the success path;
    # a stuck-``running`` status would mean the adapter never executed.
    assert body["status"] == "done"

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
        assert row["status"] == "done"
        assert row["finished_at"] is not None
        # parameters are stored as serialised JSON; round-trip the temperature.
        assert "0.2" in cast(str, row["parameters_json"])
    finally:
        conn.close()


async def test_post_run_with_profile_id_routes_through_profile_factory(
    client: AsyncClient,
    settings: Settings,
) -> None:
    """``profile_id`` on the payload dispatches via the profile-path seam.

    A2-P1 wires the profile branch into ``invoke_run`` without removing
    the legacy ``provider`` / ``model`` fields yet. This test installs a
    profile-path factory that returns a scripted MockLLMClient and
    asserts:

    1. The run row records the ``profile_id`` (schema v3 column).
    2. The chat happened against the profile mock — *not* the legacy
       mock the autouse fixture installs. The legacy ``provider`` /
       ``model`` fields ride along but are ignored on this branch.
    3. The per-case output text comes from the profile mock's scripted
       reply, proving the dispatch routed through the new branch.
    """
    profile_mock = MockLLMClient(model="profile-mock", scripted=["from-profile"])
    legacy_calls: list[tuple[str, str]] = []
    playground_dispatch.set_client_factory(
        lambda provider, model: (
            legacy_calls.append((provider, model)),
            MockLLMClient(model=model),
        )[1]
    )
    playground_dispatch.set_profile_client_factory(lambda settings, profile_id: profile_mock)
    try:
        payload = _run_payload()
        payload["profile_id"] = "ds-default"
        payload["cases"] = [{"inputs": {"x": "ping"}}]

        resp = await client.post("/api/runs", json=payload)
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "done"
        run_id = body["run_id"]

        detail = await client.get(f"/api/runs/{run_id}")
        assert detail.status_code == 200
        outputs = detail.json()["outputs"]
        assert len(outputs) == 1
        assert outputs[0]["text"] == "from-profile"

        conn = _open_conn(settings)
        try:
            cur = conn.execute("SELECT profile_id FROM runs WHERE id = ?", (run_id,))
            row = cur.fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row["profile_id"] == "ds-default"

        assert legacy_calls == []
        assert len(profile_mock.calls) == 1
    finally:
        playground_dispatch.set_client_factory(None)
        playground_dispatch.set_profile_client_factory(None)


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
    # The dispatch adapter persisted the terminal status before this GET.
    assert body["status"] == "done"
    # No cases on this payload, so the runner makes zero chat calls and
    # the rolled-up cost stays at 0.0. The sidecar is still written
    # (zero lines) so the reader returns an empty outputs list — the
    # absent-file case is reserved for run-level failures.
    assert body["outputs"] == []
    assert body["cost_usd"] == 0.0
    assert body["started_at"]
    assert body["finished_at"]


async def test_get_run_returns_per_case_outputs_from_sidecar(
    client: AsyncClient,
    settings: Settings,
) -> None:
    """A run with cases populates the detail endpoint's ``outputs`` list.

    Wave 4 prerequisite: the sidecar writer in
    :func:`aitap.playground.dispatch._write_outputs_sidecar` must
    produce JSONL that :func:`aitap.server.routes.runs._load_outputs`
    reads back into :class:`RunOutput` objects. This test exercises the
    full HTTP path end-to-end.
    """
    # Override the per-test MockLLMClient with one that returns scripted
    # replies so we can assert per-case text content lands in the
    # response. The autouse ``_mock_invoke_run_client`` fixture installs
    # a default-reply mock; we replace it here for this specific test.
    scripted_mock = MockLLMClient(
        model="claude-sonnet-4-6",
        scripted=["case-0-output", "case-1-output", "case-2-output"],
    )
    playground_dispatch.set_client_factory(lambda provider, model: scripted_mock)
    try:
        payload = _run_payload()
        payload["cases"] = [
            {"inputs": {"x": "a"}},
            {"inputs": {"x": "b"}},
            {"inputs": {"x": "c"}},
        ]
        posted = (await client.post("/api/runs", json=payload)).json()
        assert posted["status"] == "done"

        resp = await client.get(f"/api/runs/{posted['run_id']}")
        assert resp.status_code == 200
        body = resp.json()
        outputs = body["outputs"]
        assert len(outputs) == 3
        assert [o["case_index"] for o in outputs] == [0, 1, 2]
        assert [o["text"] for o in outputs] == [
            "case-0-output",
            "case-1-output",
            "case-2-output",
        ]
        assert all(o["error"] is None for o in outputs)
        # The rolled-up run-level cost reflects the three MockLLMClient calls.
        assert body["cost_usd"] > 0.0
    finally:
        # Reinstate the autouse fixture's default so other tests in the
        # session aren't affected.
        playground_dispatch.set_client_factory(lambda provider, model: MockLLMClient(model=model))


# ---------------------------------------------------------------------------
# Pipeline run-mode validation (A·D1 / A·D3)
# ---------------------------------------------------------------------------


async def test_pipeline_node_mode_missing_node_id_422(
    client: AsyncClient,
    settings: Settings,
) -> None:
    """mode='node' without pipeline_node_id is rejected before dispatch."""
    _seed_two_node_pipeline(settings)
    resp = await client.post(
        "/api/runs",
        json=_pipeline_run_payload(mode="node"),
    )
    assert resp.status_code == 422, resp.text
    assert "pipeline_node_id" in resp.json()["detail"]


async def test_pipeline_node_mode_blank_node_id_422(
    client: AsyncClient,
    settings: Settings,
) -> None:
    """mode='node' with a blank pipeline_node_id 422s like a missing one.

    A blank string would otherwise slip past the route check and only fail
    deep in the runner as a 500 ("node not found"); we treat ``""`` as
    missing, symmetric with the empty-segment guard.
    """
    _seed_two_node_pipeline(settings)
    resp = await client.post(
        "/api/runs",
        json=_pipeline_run_payload(mode="node", node_id=""),
    )
    assert resp.status_code == 422, resp.text
    assert "pipeline_node_id" in resp.json()["detail"]


async def test_pipeline_segment_mode_none_segment_422(
    client: AsyncClient,
    settings: Settings,
) -> None:
    """mode='segment' without a segment list is rejected (no implicit e2e)."""
    _seed_two_node_pipeline(settings)
    resp = await client.post(
        "/api/runs",
        json=_pipeline_run_payload(mode="segment"),
    )
    assert resp.status_code == 422, resp.text
    assert "pipeline_segment" in resp.json()["detail"]


async def test_pipeline_segment_mode_empty_segment_422(
    client: AsyncClient,
    settings: Settings,
) -> None:
    """mode='segment' with an empty list 422s — the zero-node footgun (A·D3)."""
    _seed_two_node_pipeline(settings)
    resp = await client.post(
        "/api/runs",
        json=_pipeline_run_payload(mode="segment", segment=[]),
    )
    assert resp.status_code == 422, resp.text
    assert "pipeline_segment" in resp.json()["detail"]


async def test_pipeline_node_mode_with_segment_conflict_422(
    client: AsyncClient,
    settings: Settings,
) -> None:
    """mode='node' must not also carry pipeline_segment (ambiguous request)."""
    _seed_two_node_pipeline(settings)
    resp = await client.post(
        "/api/runs",
        json=_pipeline_run_payload(mode="node", node_id="node-a", segment=["node-a"]),
    )
    assert resp.status_code == 422, resp.text
    assert "pipeline_segment" in resp.json()["detail"]


async def test_pipeline_segment_mode_with_node_id_conflict_422(
    client: AsyncClient,
    settings: Settings,
) -> None:
    """mode='segment' must not also carry pipeline_node_id (ambiguous request)."""
    _seed_two_node_pipeline(settings)
    resp = await client.post(
        "/api/runs",
        json=_pipeline_run_payload(mode="segment", segment=["node-a"], node_id="node-b"),
    )
    assert resp.status_code == 422, resp.text
    assert "pipeline_node_id" in resp.json()["detail"]


async def test_pipeline_node_mode_valid_202(
    client: AsyncClient,
    settings: Settings,
) -> None:
    """A consistent node-mode request is accepted (202) and runs."""
    _seed_two_node_pipeline(settings)
    resp = await client.post(
        "/api/runs",
        json=_pipeline_run_payload(mode="node", node_id="node-a"),
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "done"


async def test_pipeline_segment_mode_valid_202(
    client: AsyncClient,
    settings: Settings,
) -> None:
    """A consistent segment-mode request is accepted (202) and runs."""
    _seed_two_node_pipeline(settings)
    resp = await client.post(
        "/api/runs",
        json=_pipeline_run_payload(mode="segment", segment=["node-a", "node-b"]),
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "done"


async def test_pipeline_mode_none_still_runs_end_to_end_202(
    client: AsyncClient,
    settings: Settings,
) -> None:
    """No pipeline_mode behaves like end_to_end and is accepted (regression)."""
    _seed_two_node_pipeline(settings)
    resp = await client.post(
        "/api/runs",
        json=_pipeline_run_payload(),
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "done"


async def test_pipeline_end_to_end_ignores_selectors_202(
    client: AsyncClient,
    settings: Settings,
) -> None:
    """end_to_end ignores any stray node_id/segment and is accepted.

    A·D1: ``None``/``end_to_end`` preserve today's behaviour; selectors are
    not consistency-checked in these modes (the UI simply won't send them,
    but a lenient backend keeps the additive change non-breaking).
    """
    _seed_two_node_pipeline(settings)
    resp = await client.post(
        "/api/runs",
        json=_pipeline_run_payload(mode="end_to_end", node_id="node-a", segment=["node-a"]),
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "done"


async def test_prompt_run_ignores_pipeline_mode_fields(
    client: AsyncClient,
) -> None:
    """Pipeline-mode validation never touches prompt runs."""
    payload = _run_payload()
    # A prompt payload that nonsensically carries a node mode but no node id
    # must NOT 422 — the validation is scoped to target_kind == 'pipeline'.
    payload["pipeline_mode"] = "node"
    resp = await client.post("/api/runs", json=payload)
    assert resp.status_code == 202, resp.text


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


# The four ``test_put_settings_*`` cases that lived here used to exercise
# ``PUT /api/settings`` — the legacy provider-keyed mutation endpoint
# removed in contract v3 (wt/profile-cleanup). The multi-provider
# redesign moves "pick a default" to ``PUT /api/settings/defaults`` (which
# operates on profile ids and is covered by
# ``tests/unit/test_routes_profiles.py``); free-form provider/model edits
# are no longer a UI affordance, so there's nothing left to assert here.


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
# Smoke test for the shared deps helper itself
# ---------------------------------------------------------------------------


def test_get_db_initialises_schema(settings: Settings) -> None:
    """The shared ``get_db`` dependency must yield a connection with all tables ready.

    ``get_db`` is a FastAPI yield-style dependency; we drive it manually
    here (no Depends machinery) because the contract under test is
    "schema is initialised on first connect" rather than the FastAPI
    binding itself.
    """
    iterator = get_db(settings)
    conn = next(iterator)
    try:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = {row["name"] for row in cur.fetchall()}
        assert {"runs", "scores", "feedback", "prompt_versions"}.issubset(tables)
    finally:
        # Exhaust the generator to trigger the ``finally`` close in get_db.
        try:
            next(iterator)
        except StopIteration:
            pass
