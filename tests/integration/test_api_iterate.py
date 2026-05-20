"""Integration tests for the Wave 4 ``/api/iterate`` session endpoints.

The route module under test wires :func:`aitap.iterate.loop.iterate_loop`
into the HTTP API as four endpoints:

    POST   /api/iterate                          -> 202 IterateSessionResponse
    GET    /api/iterations/{session_id}          -> IterateSessionResponse
    GET    /api/iterations/{session_id}/latest   -> IterationView | None
    GET    /api/iterations/by-prompt/{prompt_id} -> list[IterationView]

The loop itself has full coverage in ``tests/unit/test_loop.py``; these
tests focus on the *route layer* concerns:

- ``POST`` is non-blocking — 202 returns before the background task
  finishes, with a session_id and a baseline-placeholder row already
  written so a GET poll immediately after the POST never 404s.
- GET on a session id observed by the placeholder writer surfaces the
  correct ``status`` derivation (``running`` / ``converged`` / ``failed``)
  based on iteration rows.
- GET on a non-existent session id is a 404 (not an empty list — the
  contract distinguishes "session never created" from "session has no
  rows yet"; the placeholder ensures the latter never legitimately
  happens).
- Mode validation — ``guided`` mode without an instruction is rejected
  at the route layer (400) so the background task never sees an
  invalid payload.
- Manual mode wires the per-round revision text through to
  ``iterate_loop`` without a critic call.
- The ``latest`` and ``by-prompt`` shortcuts are convenience views over
  the same underlying ``iterations`` table.

Test strategy: we monkeypatch :func:`aitap.iterate.loop.iterate_loop` with
a synchronous stub that writes a small, deterministic set of iteration
rows. This keeps the integration tests fast and focused on the HTTP
plumbing — running the actual loop here would duplicate the unit-level
coverage in ``test_loop.py`` and require a stack of mocked judge / critic
replies that's not the point of this test file.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from aitap.config import Settings
from aitap.iterate.loop import IterationOutcome
from aitap.scanner.models import (
    CodeLocation,
    Confidence,
    Message,
    PromptSite,
    Provider,
    Role,
    TemplateKind,
)
from aitap.server.app import create_app
from aitap.server.routes._deps import get_settings
from aitap.store import db as store_db
from aitap.store import runs as runs_dao
from aitap.store.iterations import (
    Iteration,
    insert_iteration,
    new_session_id,
)

PROMPT_ID = "prompt-iter-1"
DATASET_ID = "iter-cases"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    """Throwaway Settings rooted at tmp_path with .aitap pre-created."""
    aitap_dir = tmp_path / ".aitap"
    for child in ("prompts", "pipelines", "datasets", "runs"):
        (aitap_dir / child).mkdir(parents=True, exist_ok=True)
    return Settings(project_root=tmp_path)


@pytest.fixture()
def app_with_settings(settings: Settings):
    """Fresh app instance with the tmp Settings injected via DI override."""
    application = create_app()
    application.dependency_overrides[get_settings] = lambda: settings
    return application


@pytest.fixture()
async def client(app_with_settings) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app_with_settings)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


def _open_conn(settings: Settings) -> sqlite3.Connection:
    conn = store_db.connect(settings.db_path)
    store_db.init_db(conn)
    return conn


def _seed_prompt(settings: Settings, prompt_id: str = PROMPT_ID) -> PromptSite:
    """Persist a PromptSite row + a v1 prompt_versions seed so the loop has a baseline."""
    site = PromptSite(
        id=prompt_id,
        name="iter_target",
        provider=Provider.ANTHROPIC,
        location=CodeLocation(file="x.py", line_start=1, line_end=5),
        messages=[
            Message(
                role=Role.USER,
                template_text="Summarise this email.",
                template_kind=TemplateKind.LITERAL,
            )
        ],
        confidence=Confidence.HIGH,
    )
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
        runs_dao.insert_prompt_version(
            conn,
            prompt_id=prompt_id,
            version=1,
            template_json=json.dumps([m.model_dump(mode="json") for m in site.messages]),
            parameters_json=site.parameters.model_dump_json(),
            note="seed v1",
            created_by="human",
            parent_version=None,
        )
    finally:
        conn.close()
    return site


def _write_iteration_row(
    conn: sqlite3.Connection,
    *,
    prompt_id: str,
    session_id: str,
    round_: int,
    is_baseline: bool,
    weighted_score: float,
    revise_mode: str | None,
    converged_reason: str | None = None,
    started_at: datetime | None = None,
    new_version: int | None = None,
) -> str:
    """Direct DAO call so tests can pre-stage iteration rows.

    Mirrors the columns ``iterate_loop`` writes — keeps the stub small
    enough to read inline yet still exercises the read path against real
    SQLite text-JSON, not a Python dict shim.
    """
    return insert_iteration(
        conn,
        prompt_id=prompt_id,
        session_id=session_id,
        round=round_,
        is_baseline=is_baseline,
        parent_version=None if is_baseline else 1,
        new_version=new_version,
        revise_mode=revise_mode,  # type: ignore[arg-type]
        revise_instruction=None,
        critique_text=None,
        weighted_score=weighted_score,
        per_dim_scores={"accuracy": weighted_score},
        downstream_status=None,
        converged_reason=converged_reason,  # type: ignore[arg-type]
        started_at=started_at or datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc) if converged_reason or revise_mode else None,
    )


# ---------------------------------------------------------------------------
# Iterate-loop monkeypatch helpers
# ---------------------------------------------------------------------------


def _make_stub_iterate_loop(
    *,
    final_version: int = 2,
    converged_reason: str | None = "delta",
    rounds: list[tuple[int, bool, float, str | None]] | None = None,
    capture: dict[str, Any] | None = None,
):
    """Build an ``iterate_loop`` stub the route layer can ``await``.

    The stub writes the deterministic set of iteration rows the test
    expects, then returns a matching :class:`IterationOutcome` so the
    background task's post-hoc bookkeeping (placeholder delete, etc.)
    sees the same shape the real loop would produce.
    """
    from aitap.iterate import loop as loop_module
    from aitap.store import db as store_db_module

    default_rounds: list[tuple[int, bool, float, str | None]] = rounds or [
        (1, True, 0.50, None),
        (2, False, 0.85, "auto"),
    ]

    async def stub(
        *,
        settings,
        prompt_id,
        dataset_id,
        client,
        judge_client=None,
        critic_client=None,
        mode="auto",
        instruction=None,
        manual_revisions=None,
        user_thumbs=None,
        user_notes=None,
        convergence=None,
        dimensions_override=None,
    ):
        if capture is not None:
            capture["called"] = True
            capture["mode"] = mode
            capture["instruction"] = instruction
            capture["manual_revisions"] = manual_revisions
            capture["prompt_id"] = prompt_id
            capture["dataset_id"] = dataset_id

        session_id = loop_module.new_session_id()
        iterations_out: list[Iteration] = []
        conn = store_db_module.connect(settings.db_path)
        try:
            store_db_module.init_db(conn)
            with store_db_module.transaction(conn, immediate=True):
                for round_, is_baseline, score, mode_str in default_rounds:
                    iter_id = _write_iteration_row(
                        conn,
                        prompt_id=prompt_id,
                        session_id=session_id,
                        round_=round_,
                        is_baseline=is_baseline,
                        weighted_score=score,
                        revise_mode=mode_str,
                        converged_reason=(
                            converged_reason
                            if (round_, is_baseline) == default_rounds[-1][:2]
                            else None
                        ),
                        new_version=(
                            final_version
                            if round_ == default_rounds[-1][0] and not is_baseline
                            else None
                        ),
                    )
                    from aitap.store.iterations import read_iteration

                    persisted = read_iteration(conn, iter_id)
                    if persisted is not None:
                        iterations_out.append(persisted)
        finally:
            conn.close()

        return IterationOutcome(
            session_id=session_id,
            iterations=iterations_out,
            converged_reason=converged_reason,  # type: ignore[arg-type]
            final_version=final_version,
        )

    return stub


def _payload(
    *,
    prompt_id: str = PROMPT_ID,
    dataset_id: str = DATASET_ID,
    mode: str = "auto",
    instruction: str | None = None,
    manual_revisions: dict[int, str] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "prompt_id": prompt_id,
        "dataset_id": dataset_id,
        "mode": mode,
    }
    if instruction is not None:
        body["instruction"] = instruction
    if manual_revisions is not None:
        # JSON keys must be strings; manual_revisions ships {round -> text}.
        body["manual_revisions"] = {str(k): v for k, v in manual_revisions.items()}
    return body


# ---------------------------------------------------------------------------
# POST /api/iterate
# ---------------------------------------------------------------------------


async def test_post_iterate_returns_202_with_session_id_and_running_status(
    client: AsyncClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST is non-blocking: returns 202 with session_id immediately.

    The route writes a baseline-placeholder row synchronously before
    returning so a GET poll fired immediately after POST never 404s.
    """
    _seed_prompt(settings)
    monkeypatch.setattr(
        "aitap.server.routes.iterate.iterate_loop",
        _make_stub_iterate_loop(),
    )

    resp = await client.post("/api/iterate", json=_payload())
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert "session_id" in body
    # The session_id is a ULID — 26 characters.
    assert len(body["session_id"]) == 26
    # Background task runs to completion inside the request lifecycle
    # for FastAPI BackgroundTasks; we therefore expect the stub-written
    # rows to be visible by now.
    assert body["status"] in {"running", "converged"}


async def test_post_iterate_persists_placeholder_row_so_get_does_not_404(
    client: AsyncClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A placeholder row must exist after POST so polling GETs succeed.

    Use a stub that writes ZERO rows so we can isolate the placeholder
    behaviour from the loop's own writes.
    """
    _seed_prompt(settings)

    async def empty_stub(**kwargs: Any) -> IterationOutcome:
        # Mimics a loop that has not yet produced iterations.
        from aitap.iterate import loop as loop_module

        return IterationOutcome(
            session_id=loop_module.new_session_id(),
            iterations=[],
            converged_reason=None,
            final_version=1,
        )

    monkeypatch.setattr("aitap.server.routes.iterate.iterate_loop", empty_stub)

    resp = await client.post("/api/iterate", json=_payload())
    assert resp.status_code == 202
    body = resp.json()
    session_id = body["session_id"]

    # GET right after POST: must succeed (placeholder visible) — not 404.
    get_resp = await client.get(f"/api/iterations/{session_id}")
    assert get_resp.status_code == 200, get_resp.text


async def test_get_unknown_session_returns_404(client: AsyncClient) -> None:
    resp = await client.get("/api/iterations/UNKNOWN0000000000000000000")
    assert resp.status_code == 404


async def test_post_iterate_404_when_prompt_missing(client: AsyncClient) -> None:
    """A POST against an unknown prompt id is a 404 — the loop would also
    raise, but rejecting at the route layer avoids spinning up a doomed
    background task that just writes a failed sentinel row."""
    resp = await client.post("/api/iterate", json=_payload(prompt_id="never-existed"))
    assert resp.status_code == 404, resp.text


async def test_post_iterate_400_when_guided_mode_without_instruction(
    client: AsyncClient,
    settings: Settings,
) -> None:
    _seed_prompt(settings)
    resp = await client.post(
        "/api/iterate",
        json=_payload(mode="guided", instruction=None),
    )
    assert resp.status_code == 400, resp.text
    assert "instruction" in resp.json()["detail"].lower()


async def test_post_iterate_400_when_manual_mode_without_revisions(
    client: AsyncClient,
    settings: Settings,
) -> None:
    _seed_prompt(settings)
    resp = await client.post(
        "/api/iterate",
        json=_payload(mode="manual", manual_revisions=None),
    )
    assert resp.status_code == 400, resp.text
    assert "manual_revisions" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Mode dispatch
# ---------------------------------------------------------------------------


async def test_post_iterate_manual_mode_threads_revisions(
    client: AsyncClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manual mode plumbs ``manual_revisions`` straight to ``iterate_loop``."""
    _seed_prompt(settings)
    capture: dict[str, Any] = {}
    monkeypatch.setattr(
        "aitap.server.routes.iterate.iterate_loop",
        _make_stub_iterate_loop(capture=capture),
    )

    resp = await client.post(
        "/api/iterate",
        json=_payload(
            mode="manual",
            manual_revisions={2: "USER-EDITED PROMPT BODY"},
        ),
    )
    assert resp.status_code == 202, resp.text
    assert capture["mode"] == "manual"
    assert capture["manual_revisions"] == {2: "USER-EDITED PROMPT BODY"}


async def test_post_iterate_guided_mode_threads_instruction(
    client: AsyncClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_prompt(settings)
    capture: dict[str, Any] = {}
    monkeypatch.setattr(
        "aitap.server.routes.iterate.iterate_loop",
        _make_stub_iterate_loop(capture=capture),
    )

    resp = await client.post(
        "/api/iterate",
        json=_payload(mode="guided", instruction="make tone professional"),
    )
    assert resp.status_code == 202
    assert capture["mode"] == "guided"
    assert capture["instruction"] == "make tone professional"


# ---------------------------------------------------------------------------
# GET /api/iterations/{session_id} — session status views
# ---------------------------------------------------------------------------


async def test_get_session_converged_returns_converged_status_and_final_version(
    client: AsyncClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_prompt(settings)
    monkeypatch.setattr(
        "aitap.server.routes.iterate.iterate_loop",
        _make_stub_iterate_loop(
            final_version=3,
            converged_reason="delta",
            rounds=[
                (1, True, 0.40, None),
                (2, False, 0.65, "auto"),
                (3, False, 0.85, "auto"),
            ],
        ),
    )

    posted = (await client.post("/api/iterate", json=_payload())).json()
    session_id = posted["session_id"]

    resp = await client.get(f"/api/iterations/{session_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "converged"
    assert body["converged_reason"] == "delta"
    assert body["final_version"] == 3
    rounds = [it["round"] for it in body["iterations"]]
    # placeholder is filtered out; only real loop rounds surface.
    assert rounds == [1, 2, 3]
    # The baseline row is is_baseline=True.
    assert body["iterations"][0]["is_baseline"] is True


async def test_get_session_critic_failed_surfaces_failed_status(
    client: AsyncClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed-sentinel iteration row (revise_mode='failed') maps to status='failed'."""
    _seed_prompt(settings)
    monkeypatch.setattr(
        "aitap.server.routes.iterate.iterate_loop",
        _make_stub_iterate_loop(
            converged_reason="critic_failed",
            rounds=[
                (1, True, 0.40, None),
                (2, False, 0.0, "failed"),
            ],
        ),
    )

    posted = (await client.post("/api/iterate", json=_payload())).json()
    resp = await client.get(f"/api/iterations/{posted['session_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["converged_reason"] == "critic_failed"


async def test_get_session_in_progress_returns_running_status(
    client: AsyncClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-stage a session_id with only the placeholder row + one baseline-ish
    row whose ``converged_reason`` is NULL — the route must classify it as
    still running, not converged."""
    _seed_prompt(settings)

    # Pre-stage rows directly so we can assert mid-flight behaviour. No
    # need to run the loop stub at all.
    session_id = new_session_id()
    conn = _open_conn(settings)
    try:
        _write_iteration_row(
            conn,
            prompt_id=PROMPT_ID,
            session_id=session_id,
            round_=1,
            is_baseline=True,
            weighted_score=0.50,
            revise_mode=None,
            converged_reason=None,
        )
    finally:
        conn.close()

    resp = await client.get(f"/api/iterations/{session_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["converged_reason"] is None
    assert body["final_version"] is None


# ---------------------------------------------------------------------------
# GET /api/iterations/{session_id}/latest
# ---------------------------------------------------------------------------


async def test_get_session_latest_returns_highest_round(
    client: AsyncClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_prompt(settings)
    monkeypatch.setattr(
        "aitap.server.routes.iterate.iterate_loop",
        _make_stub_iterate_loop(
            final_version=3,
            rounds=[
                (1, True, 0.40, None),
                (2, False, 0.65, "auto"),
                (3, False, 0.85, "auto"),
            ],
        ),
    )

    posted = (await client.post("/api/iterate", json=_payload())).json()
    session_id = posted["session_id"]

    resp = await client.get(f"/api/iterations/{session_id}/latest")
    assert resp.status_code == 200
    body = resp.json()
    assert body is not None
    assert body["round"] == 3
    assert body["new_version"] == 3


async def test_get_session_latest_returns_null_for_unknown_session(
    client: AsyncClient,
) -> None:
    resp = await client.get("/api/iterations/UNKNOWN0000000000000000000/latest")
    # 404 — same shape as the session lookup; "null body" is reserved for
    # the case where the session exists but has zero non-placeholder rows.
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/iterations/by-prompt/{prompt_id}
# ---------------------------------------------------------------------------


async def test_list_iterations_for_prompt_returns_rows_across_sessions(
    client: AsyncClient,
    settings: Settings,
) -> None:
    """The by-prompt view aggregates iterations across all sessions for one prompt.

    UI uses it for the History tab: each row is a distinct iteration
    event, regardless of which /iterate invocation produced it.
    """
    _seed_prompt(settings)

    # Two sessions, three iteration rows total.
    sess_a = new_session_id()
    sess_b = new_session_id()
    conn = _open_conn(settings)
    try:
        _write_iteration_row(
            conn,
            prompt_id=PROMPT_ID,
            session_id=sess_a,
            round_=1,
            is_baseline=True,
            weighted_score=0.50,
            revise_mode=None,
            started_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        )
        _write_iteration_row(
            conn,
            prompt_id=PROMPT_ID,
            session_id=sess_a,
            round_=2,
            is_baseline=False,
            weighted_score=0.70,
            revise_mode="auto",
            converged_reason="delta",
            started_at=datetime(2026, 5, 18, 1, tzinfo=timezone.utc),
        )
        _write_iteration_row(
            conn,
            prompt_id=PROMPT_ID,
            session_id=sess_b,
            round_=1,
            is_baseline=True,
            weighted_score=0.55,
            revise_mode=None,
            started_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        )
    finally:
        conn.close()

    resp = await client.get(f"/api/iterations/by-prompt/{PROMPT_ID}")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    # Newest first by started_at — sess_b's lone row sorts before either
    # of sess_a's because its started_at is on 2026-05-19.
    assert body[0]["session_id"] == sess_b
    # The other two are sess_a's rows in newest-first order.
    assert {b["session_id"] for b in body} == {sess_a, sess_b}


async def test_list_iterations_for_prompt_empty_when_no_sessions(
    client: AsyncClient,
    settings: Settings,
) -> None:
    _seed_prompt(settings)
    resp = await client.get(f"/api/iterations/by-prompt/{PROMPT_ID}")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_iterations_for_prompt_respects_limit(
    client: AsyncClient,
    settings: Settings,
) -> None:
    _seed_prompt(settings)
    sess = new_session_id()
    conn = _open_conn(settings)
    try:
        for round_ in range(1, 6):
            _write_iteration_row(
                conn,
                prompt_id=PROMPT_ID,
                session_id=sess,
                round_=round_,
                is_baseline=(round_ == 1),
                weighted_score=0.5 + round_ * 0.05,
                revise_mode=None if round_ == 1 else "auto",
                started_at=datetime(2026, 5, round_ + 1, tzinfo=timezone.utc),
            )
    finally:
        conn.close()

    resp = await client.get(f"/api/iterations/by-prompt/{PROMPT_ID}", params={"limit": 3})
    assert resp.status_code == 200
    assert len(resp.json()) == 3


# ---------------------------------------------------------------------------
# Background task error handling
# ---------------------------------------------------------------------------


async def test_post_iterate_background_exception_marks_session_failed(
    client: AsyncClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the background task itself raises (e.g. provider unreachable),
    the session must surface as ``failed`` via a sentinel row rather than
    stay perpetually ``running`` from the UI's point of view."""
    _seed_prompt(settings)

    async def exploding_stub(**kwargs: Any) -> IterationOutcome:
        raise RuntimeError("provider unreachable")

    monkeypatch.setattr("aitap.server.routes.iterate.iterate_loop", exploding_stub)

    posted = (await client.post("/api/iterate", json=_payload())).json()
    session_id = posted["session_id"]

    # GET after the failure: a sentinel row was written, and status='failed'.
    resp = await client.get(f"/api/iterations/{session_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed", body
