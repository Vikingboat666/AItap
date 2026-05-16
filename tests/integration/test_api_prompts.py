"""End-to-end HTTP tests for prompts / pipelines / history endpoints.

Each test runs a real :func:`scan_project` against a tmp-copy of a
fixture project, persists the scan into a temp ``.aitap/``, then
exercises the FastAPI app via :class:`httpx.AsyncClient` with an
:class:`ASGITransport` so no socket is opened.

A single ``app.dependency_overrides[get_settings]`` redirects every
request's :func:`get_db` to the temp project's SQLite — that's the
only piece of plumbing required to make ``app`` test-scoped.
"""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from typer.testing import CliRunner

from aitap.cli import app as cli_app
from aitap.config import Settings
from aitap.scanner.engine import scan_project
from aitap.scanner.models import CallParameters, Message, Role, TemplateKind
from aitap.server.app import app as fastapi_app
from aitap.server.routes._deps import get_settings
from aitap.store import db as db_module
from aitap.store import history, persist_scan_result

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture()
def project_settings(tmp_path: Path) -> Iterator[Settings]:
    """Set up a tmp project that mimics ``aitap init`` + ``aitap scan``.

    We copy the openai_basic fixture into tmp_path so the scanned
    file paths are relative to a directory we own, then run
    ``aitap init`` to materialise the .aitap/ skeleton and call
    ``persist_scan_result`` directly. The yielded :class:`Settings`
    is what the FastAPI dependency override hands back.
    """
    project_root = tmp_path / "proj"
    shutil.copytree(FIXTURES_DIR / "openai_basic", project_root)

    runner = CliRunner()
    init_result = runner.invoke(cli_app, ["init", str(project_root)])
    assert init_result.exit_code == 0, init_result.output

    settings = Settings(project_root=project_root)
    scan = scan_project(project_root)
    persist_scan_result(settings, scan)

    yield settings


@pytest_asyncio.fixture()
async def api_client(project_settings: Settings) -> AsyncIterator[AsyncClient]:
    """Yield an httpx client wired into the FastAPI app via ASGITransport.

    The dependency override is scoped per-test so parallel runs (or
    follow-up tests with different settings) never see stale state.
    """
    fastapi_app.dependency_overrides[get_settings] = lambda: project_settings
    transport = ASGITransport(app=fastapi_app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    finally:
        fastapi_app.dependency_overrides.pop(get_settings, None)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


async def test_list_prompts_returns_scan_results(
    api_client: AsyncClient, project_settings: Settings
) -> None:
    response = await api_client.get("/api/prompts")
    assert response.status_code == 200, response.text

    body = response.json()
    assert "prompts" in body

    scan = scan_project(project_settings.project_root)
    assert len(body["prompts"]) == len(scan.prompts)
    # Every contract field shows up — guards against the response model
    # silently dropping a field when we refactor the route handler.
    for entry in body["prompts"]:
        assert set(entry.keys()) == {
            "id",
            "name",
            "provider",
            "file",
            "line_start",
            "purpose",
            "confidence",
            "latest_version",
        }
        assert entry["latest_version"] == 0  # no versions recorded yet


async def test_prompt_detail_returns_site_and_empty_versions(
    api_client: AsyncClient, project_settings: Settings
) -> None:
    list_resp = await api_client.get("/api/prompts")
    prompt_id = list_resp.json()["prompts"][0]["id"]

    detail = await api_client.get(f"/api/prompts/{prompt_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()

    assert body["site"]["id"] == prompt_id
    assert body["versions"] == []


async def test_prompt_detail_404s_on_unknown_id(api_client: AsyncClient) -> None:
    resp = await api_client.get("/api/prompts/does-not-exist")
    assert resp.status_code == 404


async def test_create_prompt_version_assigns_monotonic_numbers(
    api_client: AsyncClient,
) -> None:
    list_resp = await api_client.get("/api/prompts")
    prompt_id = list_resp.json()["prompts"][0]["id"]

    payload = {
        "messages": [
            {
                "role": "system",
                "template_text": "be brief",
                "template_kind": "literal",
                "variables": [],
            }
        ],
        "parameters": {"model": "gpt-4o-mini", "temperature": 0.3},
        "note": "first edit",
    }
    first = await api_client.post(f"/api/prompts/{prompt_id}/versions", json=payload)
    assert first.status_code == 201, first.text
    assert first.json() == {"prompt_id": prompt_id, "version": 1}

    second = await api_client.post(
        f"/api/prompts/{prompt_id}/versions",
        json={**payload, "note": "second edit", "parent_version": 1},
    )
    assert second.status_code == 201, second.text
    assert second.json()["version"] == 2

    # Detail view reflects the new versions
    detail = await api_client.get(f"/api/prompts/{prompt_id}")
    versions = detail.json()["versions"]
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[1]["parent_version"] == 1
    assert versions[1]["created_by"] == "human"


# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------


async def test_list_pipelines_uses_var_chain_fixture(tmp_path: Path) -> None:
    """The openai_basic fixture has no pipeline; var_chain does.

    Standing up a separate fixture so the pipeline assertions are
    meaningful — empty-list responses are exercised by the absence of
    pipelines in the openai_basic suite above.
    """
    project_root = tmp_path / "proj"
    shutil.copytree(FIXTURES_DIR / "var_chain", project_root)
    CliRunner().invoke(cli_app, ["init", str(project_root)])

    settings = Settings(project_root=project_root)
    scan = scan_project(project_root)
    persist_scan_result(settings, scan)
    assert scan.pipelines, "var_chain fixture should yield at least one pipeline"

    fastapi_app.dependency_overrides[get_settings] = lambda: settings
    transport = ASGITransport(app=fastapi_app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            list_resp = await client.get("/api/pipelines")
            assert list_resp.status_code == 200, list_resp.text
            body = list_resp.json()
            assert len(body["pipelines"]) == len(scan.pipelines)
            pipeline_id = body["pipelines"][0]["id"]

            detail = await client.get(f"/api/pipelines/{pipeline_id}")
            assert detail.status_code == 200, detail.text
            payload = detail.json()
            assert payload["pipeline"]["id"] == pipeline_id
            assert isinstance(payload["site_index"], dict)
            # Every node in the pipeline should resolve to a prompt
            # summary in the site_index (var_chain has both prompts
            # detected by the L1 scanner).
            for node in payload["pipeline"]["nodes"]:
                assert node["prompt_id"] in payload["site_index"]
    finally:
        fastapi_app.dependency_overrides.pop(get_settings, None)


async def test_pipeline_detail_404s_on_unknown_id(api_client: AsyncClient) -> None:
    resp = await api_client.get("/api/pipelines/missing-id")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


async def test_history_lists_versions_with_score_placeholder(
    api_client: AsyncClient, project_settings: Settings
) -> None:
    prompt_id = (await api_client.get("/api/prompts")).json()["prompts"][0]["id"]
    payload = {
        "messages": [
            {
                "role": "user",
                "template_text": "hello",
                "template_kind": "literal",
                "variables": [],
            }
        ],
        "parameters": {},
        "note": "init",
    }
    await api_client.post(f"/api/prompts/{prompt_id}/versions", json=payload)

    history_resp = await api_client.get(f"/api/history/{prompt_id}")
    assert history_resp.status_code == 200, history_resp.text
    body = history_resp.json()
    assert body["prompt_id"] == prompt_id
    assert len(body["entries"]) == 1
    entry = body["entries"][0]
    assert entry["version"] == 1
    assert entry["created_by"] == "human"
    # No runs/scores recorded yet, so avg_score must be JSON null
    assert entry["avg_score"] is None


async def test_history_404s_on_unknown_prompt(api_client: AsyncClient) -> None:
    resp = await api_client.get("/api/history/does-not-exist")
    assert resp.status_code == 404


async def test_rollback_creates_new_head_pointing_at_target(
    api_client: AsyncClient, project_settings: Settings
) -> None:
    prompt_id = (await api_client.get("/api/prompts")).json()["prompts"][0]["id"]

    v1_body = {
        "messages": [
            {
                "role": "system",
                "template_text": "original system prompt",
                "template_kind": "literal",
                "variables": [],
            }
        ],
        "parameters": {"temperature": 0.1},
        "note": "v1",
    }
    v2_body = {
        "messages": [
            {
                "role": "system",
                "template_text": "edited system prompt",
                "template_kind": "literal",
                "variables": [],
            }
        ],
        "parameters": {"temperature": 0.9},
        "note": "v2",
    }
    r1 = await api_client.post(f"/api/prompts/{prompt_id}/versions", json=v1_body)
    r2 = await api_client.post(f"/api/prompts/{prompt_id}/versions", json=v2_body)
    assert r1.json()["version"] == 1
    assert r2.json()["version"] == 2

    # Roll back to v1 — should create v3 with v1's content.
    rollback = await api_client.post(
        f"/api/history/{prompt_id}/rollback", json={"target_version": 1}
    )
    assert rollback.status_code == 200, rollback.text
    new_version = rollback.json()["version"]
    assert new_version == 3

    # Verify lineage + content via the DAO so we don't rely on a
    # follow-up GET going through the same code path.
    conn = db_module.connect(project_settings.db_path)
    try:
        head = history.read_version(conn, prompt_id, new_version)
        assert head is not None
        assert head["parent_version"] == 1
        v1_row = history.read_version(conn, prompt_id, 1)
        assert v1_row is not None
        assert head["template_json"] == v1_row["template_json"]
        assert head["parameters_json"] == v1_row["parameters_json"]
    finally:
        conn.close()


async def test_rollback_404s_on_missing_target(
    api_client: AsyncClient,
) -> None:
    prompt_id = (await api_client.get("/api/prompts")).json()["prompts"][0]["id"]
    resp = await api_client.post(f"/api/history/{prompt_id}/rollback", json={"target_version": 99})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DAO-level coverage that the CLI / iteration loop will exercise.
# ---------------------------------------------------------------------------


def test_history_dao_round_trip(project_settings: Settings) -> None:
    """record_version + read_versions form a closed loop."""
    conn = db_module.connect(project_settings.db_path)
    db_module.init_db(conn)
    try:
        prompts = db_module.read_prompts(conn)
        assert prompts, "fixture scan should have populated prompts"
        prompt_id = prompts[0]["id"]

        msgs = [Message(role=Role.USER, template_text="hi", template_kind=TemplateKind.LITERAL)]
        params = CallParameters(model="claude-sonnet-4-6", temperature=0.5)
        with db_module.transaction(conn):
            v = history.record_version(conn, prompt_id, messages=msgs, parameters=params)
        assert v == 1

        rows = history.read_versions(conn, prompt_id)
        assert len(rows) == 1
        assert rows[0]["version"] == 1
        assert rows[0]["created_by"] == "human"
    finally:
        conn.close()


def test_history_compute_diff_emits_unified_diff(
    project_settings: Settings,
) -> None:
    conn = db_module.connect(project_settings.db_path)
    db_module.init_db(conn)
    try:
        prompt_id = db_module.read_prompts(conn)[0]["id"]
        v1_msgs = [Message(role=Role.SYSTEM, template_text="alpha")]
        v2_msgs = [Message(role=Role.SYSTEM, template_text="omega")]
        params = CallParameters()
        with db_module.transaction(conn):
            history.record_version(conn, prompt_id, messages=v1_msgs, parameters=params)
            history.record_version(conn, prompt_id, messages=v2_msgs, parameters=params)

        diff_text = history.compute_diff(conn, prompt_id, 1, 2)
        assert "alpha" in diff_text
        assert "omega" in diff_text
        assert diff_text.startswith("---")
    finally:
        conn.close()
