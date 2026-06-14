"""HTTP routes for the Wave 4 self-iteration session API.

This module is the *session-based* sibling of
``POST /api/runs/{run_id}/iterate`` (the Wave 3 single-round stub which
remains in :mod:`aitap.server.routes.runs` for backward compatibility).
It exposes four endpoints:

    POST   /api/iterate                          -> 202 IterateSessionResponse
    GET    /api/iterations/{session_id}          -> IterateSessionResponse
    GET    /api/iterations/{session_id}/latest   -> IterationView | None
    GET    /api/iterations/by-prompt/{prompt_id} -> list[IterationView]

Why two endpoints?
    The runs-scoped endpoint stays a no-op-equivalent (one new prompt
    version, no real LLM rewrite) for callers that pinned the Wave 3
    contract. The new endpoints here drive the full critique-and-revise
    loop in :mod:`aitap.iterate.loop` and are the surface the M4 UI
    (Auto-iterate panel) consumes.

Background task strategy
------------------------
``iterate_loop`` is potentially long-running (multiple LLM round-trips
per round, multiple rounds). We refuse to hold the HTTP request open
for the duration — instead:

1. Synchronously mint a session_id (ULID) + write a baseline-placeholder
   ``iterations`` row keyed on it at round=0. This guarantees a GET poll
   fired immediately after the POST never 404s.
2. Schedule the loop via FastAPI's :class:`BackgroundTasks`. The loop
   writes real iteration rows under the same session_id starting at
   round=1.
3. On success the placeholder stays in place — it is filtered out of
   every API response (round=0 is the API-layer's transient marker), so
   the UI never sees it; keeping the row means a GET racing the loop
   always finds at least one row keyed by session_id.
4. If the background task raises (e.g. provider unreachable, dataset
   missing) we UPDATE the placeholder in place to a failed-sentinel
   shape (``revise_mode='failed'``, ``converged_reason='critic_failed'``)
   so the UI surfaces ``status="failed"`` rather than spinning forever.

In-process FastAPI ``BackgroundTasks`` is intentional (no celery, no rq).
A single ``aitap ui`` is a local-dev tool: we cannot assume a broker is
running, and the loop's own SQLite writes are already transactional so
the worst case under a kill -9 is a stuck "running" status that
``aitap reset`` could later clean up.

session_id pinning
------------------
The route layer pre-mints a session_id so it can write the placeholder
row and return the id in the 202 body before the background task
starts. We pass that id straight through to
:func:`aitap.iterate.loop.iterate_loop` via its ``session_id`` kwarg;
the loop uses the supplied id verbatim (and skips its internal
:func:`new_session_id` call), guaranteeing the placeholder and the
loop's first real iteration row share the same primary-key triple.
This is the *only* correct pinning strategy under concurrent requests:
two simultaneous POSTs each have their own kwarg-bound local, so they
cannot stomp on each other the way a shared module-level monkey-patch
would.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from aitap.config import Settings
from aitap.iterate.loop import ConvergenceConfig, iterate_loop
from aitap.playground import dispatch as playground_dispatch
from aitap.server.routes._deps import get_db, get_settings
from aitap.store import db as store_db
from aitap.store.iterations import (
    ConvergedReason,
    Iteration,
    ReviseMode,
    insert_iteration,
    new_session_id,
    read_iterations_for,
    read_session,
)

_LOGGER = logging.getLogger(__name__)

router = APIRouter(tags=["iterate"])

# Sentinel placeholder values — the placeholder row is round=0, distinct
# from any real loop round (which start at 1). We leave the row in
# place on success (it is filtered out of every API response, so the UI
# never sees it). On background-task failure we UPDATE it in place to a
# sentinel that surfaces ``failed`` status without violating the
# UNIQUE(prompt_id, session_id, round) constraint.
_PLACEHOLDER_ROUND = 0


# ---------------------------------------------------------------------------
# Request / response models (kept local — the frozen contract in
# ``routes/__init__.py`` is not edited, per Wave 4 worktree rules).
# ---------------------------------------------------------------------------


class IterateSessionRequest(BaseModel):
    """Inbound body for ``POST /api/iterate``.

    ``provider`` / ``model`` selection is intentionally absent — the
    background task constructs an :class:`LLMClient` via the same
    factory the playground uses, which already reads project Settings.
    Tests substitute via
    :func:`aitap.playground.dispatch.set_profile_client_factory`.
    """

    model_config = ConfigDict(extra="ignore")

    prompt_id: str
    dataset_id: str
    mode: Literal["auto", "guided", "manual"] = "auto"
    instruction: str | None = None
    manual_revisions: dict[int, str] | None = None
    user_thumbs: dict[int, dict[int, Literal["up", "down"]]] | None = None
    user_notes: dict[int, dict[int, str]] | None = None
    convergence: ConvergenceConfig | None = None


class IterationView(BaseModel):
    """API projection of one ``iterations`` row.

    Datetimes serialise as ISO-8601 strings. The shape is deliberately
    flat — no nested objects — so the React UI's table rendering
    consumes it without further normalisation.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    prompt_id: str
    round: int
    session_id: str
    is_baseline: bool
    parent_version: int | None = None
    new_version: int | None = None
    revise_mode: ReviseMode | None = None
    revise_instruction: str | None = None
    critique_text: str | None = None
    weighted_score: float
    per_dim_scores: dict[str, float] = Field(default_factory=dict)
    downstream_status: dict[str, str] | None = None
    converged_reason: ConvergedReason | None = None
    started_at: str
    finished_at: str | None = None

    @classmethod
    def from_row(cls, it: Iteration) -> IterationView:
        """Project a typed :class:`Iteration` into the API shape.

        The DAO already parsed JSON columns into Python dicts; we just
        re-emit the timestamps as ISO-8601 strings since the contract
        prefers explicit strings over datetimes (the OpenAPI schema for
        a string is friendlier to non-Python clients).
        """
        return cls(
            id=it.id,
            prompt_id=it.prompt_id,
            round=it.round,
            session_id=it.session_id,
            is_baseline=it.is_baseline,
            parent_version=it.parent_version,
            new_version=it.new_version,
            revise_mode=it.revise_mode,
            revise_instruction=it.revise_instruction,
            critique_text=it.critique_text,
            weighted_score=it.weighted_score,
            per_dim_scores=it.per_dim_scores,
            downstream_status=it.downstream_status,
            converged_reason=it.converged_reason,
            started_at=it.started_at.isoformat(),
            finished_at=it.finished_at.isoformat() if it.finished_at is not None else None,
        )


class IterateSessionResponse(BaseModel):
    """Aggregated session view: status + all iteration rows."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    status: Literal["running", "converged", "failed"]
    converged_reason: str | None = None
    iterations: list[IterationView] = Field(default_factory=list)
    final_version: int | None = None


# ---------------------------------------------------------------------------
# POST /api/iterate — start a session
# ---------------------------------------------------------------------------


SettingsDep = Annotated[Settings, Depends(get_settings)]
ConnDep = Annotated[sqlite3.Connection, Depends(get_db)]


@router.post("/iterate", status_code=status.HTTP_202_ACCEPTED)
async def start_iterate_session(
    payload: IterateSessionRequest,
    background_tasks: BackgroundTasks,
    settings: SettingsDep,
    conn: ConnDep,
) -> IterateSessionResponse:
    """Kick off an iterate session.

    Returns 202 + a fresh ``session_id`` *before* the background task
    runs the full critique-and-revise loop. The route writes a
    placeholder row so an immediate ``GET /api/iterations/{session_id}``
    succeeds; the placeholder is deleted (or replaced with a sentinel)
    when the background task finishes.

    Mode validation happens here (not in the loop) so a malformed
    request never spins up a task that has to write a sentinel row to
    surface the failure — a 400 is friendlier and matches the
    request-validation conventions of the rest of the API surface.
    """
    # Mode preconditions — fail fast at the route layer.
    if payload.mode == "guided" and not payload.instruction:
        raise HTTPException(
            status_code=400,
            detail="guided mode requires a non-empty 'instruction' field",
        )
    if payload.mode == "manual" and not payload.manual_revisions:
        raise HTTPException(
            status_code=400,
            detail="manual mode requires at least one entry in 'manual_revisions'",
        )

    # The loop later raises if the prompt doesn't exist; checking here
    # lets us return a 404 synchronously instead of accepting + then
    # writing a sentinel row that the UI has to interpret.
    if not _prompt_exists(conn, payload.prompt_id):
        raise HTTPException(
            status_code=404,
            detail=f"prompt {payload.prompt_id!r} not found",
        )

    session_id = new_session_id()
    _insert_placeholder(conn, session_id=session_id, prompt_id=payload.prompt_id)

    # Schedule the background task. FastAPI will await it after sending
    # the 202 response to the client (so an immediate GET poll is racing
    # against a not-yet-started loop — that's exactly why we wrote the
    # placeholder above).
    background_tasks.add_task(
        _run_iterate_in_background,
        settings=settings,
        session_id=session_id,
        payload=payload,
    )

    # Return whatever we can see RIGHT NOW. The background task hasn't
    # written any real iterations yet, so the response contains the
    # placeholder only (which we surface to the UI as "running").
    return _build_session_response(conn, session_id)


# ---------------------------------------------------------------------------
# GET /api/iterations/{session_id} — full session status
# ---------------------------------------------------------------------------


@router.get("/iterations/{session_id}", response_model=IterateSessionResponse)
def get_iterate_session(
    session_id: str,
    conn: ConnDep,
) -> IterateSessionResponse:
    """Return the full session state — every iteration row + derived status."""
    rows = read_session(conn, session_id)
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"session {session_id!r} not found",
        )
    return _session_response_from_rows(session_id, rows)


# ---------------------------------------------------------------------------
# GET /api/iterations/{session_id}/latest — convenience for polling
# ---------------------------------------------------------------------------


@router.get(
    "/iterations/{session_id}/latest",
    response_model=IterationView,
)
def get_iterate_session_latest(
    session_id: str,
    conn: ConnDep,
) -> IterationView:
    """Return the highest-round iteration row in the session.

    This is the polling shortcut for UIs that don't want to fetch the
    whole list every second. Placeholder rows (round=0) are excluded so
    the result tracks the latest *real* loop progress.
    """
    rows = read_session(conn, session_id)
    non_placeholder = [r for r in rows if r.round > _PLACEHOLDER_ROUND]
    if not non_placeholder:
        # Session may exist (placeholder only) but no real rows yet,
        # OR session truly missing. 404 keeps both paths simple — the
        # UI re-polls the full session endpoint to disambiguate.
        raise HTTPException(
            status_code=404,
            detail=f"no real iteration rows yet for session {session_id!r}",
        )
    latest = max(non_placeholder, key=lambda it: it.round)
    return IterationView.from_row(latest)


# ---------------------------------------------------------------------------
# GET /api/iterations/by-prompt/{prompt_id} — History UI feed
# ---------------------------------------------------------------------------


@router.get(
    "/iterations/by-prompt/{prompt_id}",
    response_model=list[IterationView],
)
def list_iterations_for_prompt(
    prompt_id: str,
    conn: ConnDep,
    limit: int = 50,
) -> list[IterationView]:
    """Return iterations for *prompt_id*, newest first, capped at *limit*.

    Sorted by ``started_at DESC`` (then id DESC for ties) by the DAO.
    Placeholders are filtered so the History UI never shows the
    transient round=0 marker.
    """
    capped = max(1, min(int(limit), 200))
    rows = read_iterations_for(conn, prompt_id, limit=capped)
    return [IterationView.from_row(r) for r in rows if r.round > _PLACEHOLDER_ROUND]


# ---------------------------------------------------------------------------
# Background task — runs the actual loop
# ---------------------------------------------------------------------------


async def _run_iterate_in_background(
    *,
    settings: Settings,
    session_id: str,
    payload: IterateSessionRequest,
) -> None:
    """Drive :func:`iterate_loop` to completion, then clean up the placeholder.

    Exception strategy: any failure (loop raised, provider unreachable,
    DB write error mid-flight) is caught and converted into a sentinel
    iteration row so the UI's status derivation can read it as
    ``failed``. We re-raise *nothing* — a background task that bubbles
    an exception to the FastAPI runner produces an uncatchable 500 in
    a context the caller has already disconnected from. Logging the
    traceback here is the only useful diagnostic the user gets.
    """
    try:
        # A2-P3: dispatch is profile-keyed. The iterate background task
        # uses the configured default profile
        # (``settings.defaults.model_profile_id``); when no default is
        # configured it raises ``ProfileDispatchError`` and we fall
        # through to the failure-marker path below — same as the
        # provider-unreachable case the docstring promised.
        default_profile_id = settings.defaults.model_profile_id
        if not default_profile_id:
            raise playground_dispatch.ProfileDispatchError(
                "No default profile configured. Open Settings and pick a "
                "default model profile, then re-run."
            )
        # The dispatch module's ``set_profile_client_factory`` is the
        # designated test seam — we read the live factory off the
        # module attribute directly so tests' swap keeps working
        # without forcing dispatch to publish a new accessor.
        client = playground_dispatch._profile_client_factory(  # pyright: ignore[reportPrivateUsage]
            settings, default_profile_id
        )
        # Pin the loop to our pre-minted session_id via the loop's
        # explicit kwarg. This is concurrency-safe — two simultaneous
        # POSTs each pass their own id through the call stack, so
        # neither can observe the other's id (unlike a shared
        # module-level monkey-patch, which would race).
        await iterate_loop(
            settings=settings,
            prompt_id=payload.prompt_id,
            dataset_id=payload.dataset_id,
            client=client,
            mode=payload.mode,
            instruction=payload.instruction,
            manual_revisions=payload.manual_revisions,
            user_thumbs=payload.user_thumbs,
            user_notes=payload.user_notes,
            convergence=payload.convergence,
            session_id=session_id,
        )
    except Exception:
        _LOGGER.exception(
            "iterate background task failed for session %s (prompt %s)",
            session_id,
            payload.prompt_id,
        )
        _replace_placeholder_with_failed_sentinel(settings=settings, session_id=session_id)
        return
    # Success path: leave the placeholder in place. It is filtered out
    # of every API response (round=0 is treated as transient), so the
    # UI never sees it; keeping the row means a GET racing the loop
    # (placeholder written, loop midway through) always finds at least
    # one row keyed by session_id and can derive a valid status.


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _prompt_exists(conn: sqlite3.Connection, prompt_id: str) -> bool:
    cur = conn.execute("SELECT 1 FROM prompts WHERE id = ? LIMIT 1", (prompt_id,))
    return cur.fetchone() is not None


def _insert_placeholder(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    prompt_id: str,
) -> str:
    """Write the round=0 placeholder row so polling GETs succeed.

    Kept transactional via :func:`store.db.transaction` so a concurrent
    GET sees either "no rows" or "placeholder present" — never a
    half-written row. ``weighted_score=0.0`` is conventional; the route
    layer never surfaces this row to the UI (placeholder rows are
    filtered out of every response).
    """
    started = datetime.now(timezone.utc)
    with store_db.transaction(conn, immediate=True):
        iter_id = insert_iteration(
            conn,
            prompt_id=prompt_id,
            session_id=session_id,
            round=_PLACEHOLDER_ROUND,
            is_baseline=False,
            parent_version=None,
            new_version=None,
            revise_mode=None,
            revise_instruction=None,
            critique_text=None,
            weighted_score=0.0,
            per_dim_scores={},
            downstream_status=None,
            converged_reason=None,
            started_at=started,
            finished_at=None,
        )
    return iter_id


def _replace_placeholder_with_failed_sentinel(
    *,
    settings: Settings,
    session_id: str,
) -> None:
    """Mark a session as failed by overwriting its placeholder row.

    Schema constraint: ``UNIQUE(prompt_id, session_id, round)`` means we
    can keep the same primary key. We UPDATE the row in place rather
    than INSERT a separate sentinel: there is exactly one placeholder
    per session, and a UPDATE leaves the iterations log smaller (and
    avoids the round=0 vs round=1 collision question entirely).
    """
    conn = store_db.connect(settings.db_path)
    try:
        store_db.init_db(conn)
        now = datetime.now(timezone.utc).isoformat()
        with store_db.transaction(conn, immediate=True):
            conn.execute(
                """
                UPDATE iterations
                SET revise_mode = 'failed',
                    converged_reason = 'critic_failed',
                    finished_at = ?
                WHERE session_id = ? AND round = ?
                """,
                (now, session_id, _PLACEHOLDER_ROUND),
            )
    except sqlite3.Error:
        _LOGGER.exception("failed to write sentinel for failed iterate session %s", session_id)
    finally:
        conn.close()


def _build_session_response(
    conn: sqlite3.Connection,
    session_id: str,
) -> IterateSessionResponse:
    """Read + project the current state of *session_id* for the POST response.

    Used by the 202 path where we have just inserted the placeholder
    and want to surface the session_id alongside whatever rows are
    visible to a GET racing us. Returns an explicit "running" status
    when only the placeholder is present.
    """
    rows = read_session(conn, session_id)
    return _session_response_from_rows(session_id, rows)


def _session_response_from_rows(
    session_id: str,
    rows: list[Iteration],
) -> IterateSessionResponse:
    """Centralised projection so every read path returns the same shape.

    Status derivation precedence:

    1. Any non-placeholder row with ``revise_mode == 'failed'`` (or a
       placeholder UPDATEd to that sentinel) → ``failed``.
    2. Last real row has a ``converged_reason`` set → ``converged``.
    3. Otherwise → ``running``.
    """
    # Filter placeholder out of the surfaced list — UI never sees round=0.
    real_rows = [r for r in rows if r.round > _PLACEHOLDER_ROUND]
    placeholder_rows = [r for r in rows if r.round == _PLACEHOLDER_ROUND]

    # A placeholder that was UPDATEd to revise_mode='failed' indicates
    # the background task crashed before any real round committed.
    if any(p.revise_mode == "failed" for p in placeholder_rows):
        return IterateSessionResponse(
            session_id=session_id,
            status="failed",
            converged_reason="critic_failed",
            iterations=[IterationView.from_row(r) for r in real_rows],
            final_version=None,
        )

    # Failed sentinel inside a real round (critic-failed mid-loop).
    if any(r.revise_mode == "failed" for r in real_rows):
        last = real_rows[-1] if real_rows else None
        return IterateSessionResponse(
            session_id=session_id,
            status="failed",
            converged_reason=last.converged_reason if last else "critic_failed",
            iterations=[IterationView.from_row(r) for r in real_rows],
            final_version=_resolve_final_version(real_rows),
        )

    last_real = real_rows[-1] if real_rows else None
    if last_real is not None and last_real.converged_reason is not None:
        return IterateSessionResponse(
            session_id=session_id,
            status="converged",
            converged_reason=last_real.converged_reason,
            iterations=[IterationView.from_row(r) for r in real_rows],
            final_version=_resolve_final_version(real_rows),
        )

    return IterateSessionResponse(
        session_id=session_id,
        status="running",
        converged_reason=None,
        iterations=[IterationView.from_row(r) for r in real_rows],
        final_version=_resolve_final_version(real_rows),
    )


def _resolve_final_version(rows: list[Iteration]) -> int | None:
    """The final version is the highest ``new_version`` across real rows.

    Baseline rows have ``new_version=None``; an empty / baseline-only
    session has no committed new version. We return ``None`` in that
    case so the UI can distinguish "nothing produced yet" from "we
    produced v2" (a real loop that converged at the baseline would
    return ``None`` and the UI shows "no rewrite needed").
    """
    versions = [r.new_version for r in rows if r.new_version is not None]
    return max(versions) if versions else None


# Public re-export so ``server/app.py`` can find the router via getattr,
# plus the response models so a downstream consumer (UI client codegen,
# tests in sibling worktrees) can import them by name without going
# through the routes package's ``__init__``.
__all__ = [
    "IterateSessionRequest",
    "IterateSessionResponse",
    "IterationView",
    "router",
]
