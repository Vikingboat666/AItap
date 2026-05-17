"""HTTP routes for ``/api/runs`` and ``/api/runs/{id}/{feedback,iterate}``.

This module is the *write side* of the playground surface: every endpoint
ends up touching the ``runs``, ``scores``, ``feedback``, or
``prompt_versions`` tables.

It deliberately does **not** execute prompts itself. The single source of
truth for "run a prompt against a dataset" is :mod:`aitap.playground.dispatch`
(an adapter owned by this worktree that wires the ``wt/runner`` runner module
into the API request lifecycle). When that adapter is absent
:func:`_invoke_runner_safely` records the run in the ``running`` status and
returns immediately so the contract still exercises end-to-end.

All endpoints depend on a :class:`aitap.config.Settings` instance and an
``sqlite3.Connection`` injected via FastAPI's ``Depends``. Tests can swap
the Settings via ``app.dependency_overrides[get_settings]`` so the suite
runs against a tmp_path-rooted project without env-var monkeypatching.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, status

from aitap.config import Settings
from aitap.iterate import iterate_one_round
from aitap.server.routes import (
    FeedbackCreate,
    FeedbackResponse,
    IterateRequest,
    IterateResponse,
    RunCreate,
    RunDetailResponse,
    RunListResponse,
    RunOutput,
    RunResponse,
)
from aitap.server.routes._deps import get_db, get_settings
from aitap.store import db as store_db
from aitap.store import runs as runs_dao

router = APIRouter(tags=["runs"])

# Narrow strings the API layer surfaces, kept in sync with the contract.
_RUN_STATUS_VALUES: frozenset[str] = frozenset({"running", "done", "failed"})
_TARGET_KIND_VALUES: frozenset[str] = frozenset({"prompt", "pipeline"})


@router.post("/runs", response_model=RunResponse, status_code=status.HTTP_202_ACCEPTED)
def create_run(
    payload: RunCreate,
    settings: Annotated[Settings, Depends(get_settings)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> RunResponse:
    """Queue a new run.

    Wave 3 contract:
    - Validate the payload (pydantic handles shape).
    - Insert a ``runs`` row in the *running* state.
    - Attempt to hand off to :mod:`aitap.playground.dispatch` via a lazy
      import. The adapter is responsible for marking the run *done* /
      *failed* and stamping the final cost. If the module is unavailable
      we leave the row in *running* so a later worktree merge can attach.
    """
    run_id = runs_dao.new_run_id(payload.target_id, payload.target_version)
    parameters_json = runs_dao.serialize_parameters(payload.parameters)

    runs_dao.insert_run(
        conn,
        run_id=run_id,
        target_kind=payload.target_kind,
        target_id=payload.target_id,
        target_version=payload.target_version,
        dataset_id=payload.dataset_id,
        provider=payload.provider.value,
        model=payload.model,
        parameters_json=parameters_json,
    )

    final_status = _invoke_runner_safely(settings, run_id, payload)
    # ``_invoke_runner_safely`` opens its own connection (the adapter runs
    # outside the request handler's session because it may close/reopen
    # for status persistence). Re-read the row through *our* connection so
    # the response reflects any status mutation the adapter performed.
    row = runs_dao.read_run(conn, run_id)
    surfaced = _coerce_status(row["status"]) if row is not None else final_status
    return RunResponse(run_id=run_id, status=surfaced)


@router.get("/runs", response_model=RunListResponse)
def list_runs_endpoint(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    target_id: str | None = None,
    limit: int = 50,
) -> RunListResponse:
    """List runs, optionally filtered by ``target_id``.

    ``limit`` is clamped to [1, 200] so a malicious query can't drain the
    table. The frontend's default page size is 50.
    """
    capped = max(1, min(int(limit), 200))
    rows = runs_dao.list_runs(conn, target_id=target_id, limit=capped)
    return RunListResponse(
        runs=[
            RunResponse(run_id=str(row["id"]), status=_coerce_status(row["status"])) for row in rows
        ]
    )


@router.get("/runs/{run_id}", response_model=RunDetailResponse)
def get_run(
    run_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> RunDetailResponse:
    """Fetch one run + a placeholder outputs list.

    Wave 3 doesn't persist per-case outputs to the DB (no ``outputs``
    table); we return an empty list so the response shape matches the
    contract. The :mod:`aitap.playground.dispatch` adapter has a TODO to
    write outputs as a JSONL sidecar under ``.aitap/runs/<id>/`` in M4 —
    once that lands :func:`_load_outputs` will read from there.
    """
    row = runs_dao.read_run(conn, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    return RunDetailResponse(
        run_id=str(row["id"]),
        target_kind=_coerce_target_kind(row["target_kind"]),
        target_id=str(row["target_id"]),
        target_version=int(row["target_version"]),
        status=_coerce_status(row["status"]),
        outputs=_load_outputs(),
        cost_usd=float(row["cost_usd"] or 0.0),
        started_at=runs_dao.parse_started_at(row),
        finished_at=runs_dao.parse_finished_at(row),
    )


@router.post(
    "/runs/{run_id}/feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_feedback(
    run_id: str,
    payload: FeedbackCreate,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> FeedbackResponse:
    """Attach a feedback record to a run case."""
    run_row = runs_dao.read_run(conn, run_id)
    if run_row is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    feedback_id = runs_dao.insert_feedback(
        conn,
        run_id=run_id,
        case_index=payload.case_index,
        rating=payload.rating,
        ideal_answer=payload.ideal_answer,
        critique=payload.critique,
    )
    return FeedbackResponse(feedback_id=feedback_id)


@router.post(
    "/runs/{run_id}/iterate",
    response_model=IterateResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_iterate(
    run_id: str,
    payload: IterateRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> IterateResponse:
    """Fire one round of self-iteration based on collected feedback.

    Wave 3 implementation is a stub: it writes a new ``prompt_versions``
    row attributed to ``created_by='iteration'`` so the rest of the
    pipeline (history, diff, rollback) sees a real, queryable record.
    The LLM-driven rewrite lands in M4 and will honour the
    ``judge_model``/``convergence_threshold``/``include_downstream`` knobs
    on ``payload`` — captured here so the API surface stays stable.
    """
    _ = payload  # accepted for the contract; M4 will dispatch on it
    try:
        outcome = iterate_one_round(conn, run_id=run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return IterateResponse(
        new_version=outcome.new_version,
        score_before=outcome.score_before,
        score_after=outcome.score_after,
        converged=outcome.converged,
        downstream_impact=outcome.downstream_impact,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _invoke_runner_safely(
    settings: Settings,
    run_id: str,
    payload: RunCreate,
) -> Literal["running", "done", "failed"]:
    """Hand off to :mod:`aitap.playground.dispatch` if it's importable.

    Returns the run status to surface in the response:
    - ``"done"`` when the adapter ran the prompt synchronously and
      persisted a terminal status.
    - ``"running"`` when the adapter module isn't available (e.g.,
      a downstream consumer installs aitap without playground deps).

    The intentional lack of LLM execution here keeps this module thin —
    the adapter owns provider construction, dataset loading, and
    persistence. We do **not** swallow exceptions raised by the adapter;
    they propagate so callers see real failures.
    """
    # Lazy module probe so installing aitap without the dispatch module
    # still lets this package import. ``find_spec`` is preferred over
    # try/except ImportError because it lets a real bug inside an
    # existing module surface loudly rather than being silently misread
    # as "not implemented yet."
    import importlib
    from importlib.util import find_spec

    if find_spec("aitap.playground.dispatch") is None:
        return "running"

    dispatch_module = importlib.import_module("aitap.playground.dispatch")
    invoke = getattr(dispatch_module, "invoke_run", None)
    if invoke is None:
        return "running"

    # invoke_run is expected to handle its own persistence (status, cost,
    # outputs). If the contract evolves the route layer can be updated
    # independently — we don't try to second-guess what the adapter does.
    try:
        invoke(settings=settings, run_id=run_id, payload=payload)
    except Exception:
        # Mark the run failed so the UI doesn't show a perpetually-running
        # request when the adapter blows up. Open a fresh connection
        # because the request-scoped one may be in an unknown state after
        # the adapter's own DB activity. Re-raise so FastAPI returns 500.
        failover_conn = store_db.connect(settings.db_path)
        try:
            store_db.init_db(failover_conn)
            runs_dao.update_run_status(failover_conn, run_id, status="failed", finished=True)
        finally:
            failover_conn.close()
        raise
    return "done"


def _load_outputs() -> list[RunOutput]:
    """Placeholder — Wave 3 doesn't persist per-case outputs to SQLite.

    The dispatch adapter currently writes outputs to a JSONL sidecar
    under ``.aitap/runs/<id>/outputs.jsonl`` (see
    :mod:`aitap.playground.dispatch`). M4 will teach this helper to read
    that file; for now the response keeps outputs empty so the shape
    stays contract-correct.
    """
    return []


def _coerce_status(value: object) -> Literal["running", "done", "failed"]:
    """Narrow a sqlite text column to the contract's status literal.

    pyright sees ``sqlite3.Row.__getitem__`` returning ``Any``; we accept
    that at the boundary and constrain to the known values here. Unknown
    values surface as ``"failed"`` rather than crash — better to mark a
    row as failed than to 500 the whole list endpoint.
    """
    text = str(value)
    if text in _RUN_STATUS_VALUES:
        return cast(Literal["running", "done", "failed"], text)
    return "failed"


def _coerce_target_kind(value: object) -> Literal["prompt", "pipeline"]:
    text = str(value)
    if text in _TARGET_KIND_VALUES:
        return cast(Literal["prompt", "pipeline"], text)
    # Pre-existing rows with an unexpected kind shouldn't be possible (the
    # DDL has no CHECK but the API layer is the only writer); default to
    # "prompt" to keep the response valid.
    return "prompt"


__all__ = ["router"]
