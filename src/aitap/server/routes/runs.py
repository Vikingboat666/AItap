"""HTTP routes for ``/api/runs`` and ``/api/runs/{id}/{feedback,iterate}``.

This module is the *write side* of the playground surface: every endpoint
ends up touching the ``runs``, ``scores``, ``feedback``, or
``prompt_versions`` tables.

It deliberately does **not** execute prompts itself. The single source of
truth for "run a prompt against a dataset" is :mod:`aitap.playground.runner`
(owned by the ``wt/runner`` worktree). When that module isn't merged yet,
:func:`_invoke_runner_safely` records the run in the ``running`` status and
returns immediately — the contract still gets exercised end-to-end and
``wt/runner`` can later attach.

All endpoints depend on a :class:`aitap.config.Settings` instance and a
SQLite connection factory. Both are injected via FastAPI's ``Depends`` so
tests can swap them with a tmp_path-rooted Settings without monkeypatching.
"""

from __future__ import annotations

from typing import Literal, cast

from fastapi import APIRouter, HTTPException, status

from aitap.config import Settings
from aitap.iterate import iterate_one_round
from aitap.server.deps import SettingsDep, get_conn
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
from aitap.store import runs as runs_dao

router = APIRouter(prefix="/api/runs", tags=["runs"])

# Narrow strings the API layer surfaces, kept in sync with the contract.
_RUN_STATUS_VALUES: frozenset[str] = frozenset({"running", "done", "failed"})
_TARGET_KIND_VALUES: frozenset[str] = frozenset({"prompt", "pipeline"})


@router.post("", response_model=RunResponse, status_code=status.HTTP_202_ACCEPTED)
def create_run(
    payload: RunCreate,
    settings: SettingsDep,
) -> RunResponse:
    """Queue a new run.

    Wave 3 contract:
    - Validate the payload (pydantic handles shape).
    - Insert a ``runs`` row in the *running* state.
    - Attempt to hand off to :mod:`aitap.playground.runner` via a lazy
      import; if the runner module isn't available yet, leave the row in
      *running* and let ``wt/runner`` mark it *done* once it lands.
    """
    run_id = runs_dao.new_run_id(payload.target_id, payload.target_version)
    parameters_json = runs_dao.serialize_parameters(payload.parameters)

    with get_conn(settings) as conn:
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
    return RunResponse(run_id=run_id, status=final_status)


@router.get("", response_model=RunListResponse)
def list_runs_endpoint(
    settings: SettingsDep,
    target_id: str | None = None,
    limit: int = 50,
) -> RunListResponse:
    """List runs, optionally filtered by ``target_id``.

    ``limit`` is clamped to [1, 200] so a malicious query can't drain the
    table. The frontend's default page size is 50.
    """
    capped = max(1, min(int(limit), 200))
    with get_conn(settings) as conn:
        rows = runs_dao.list_runs(conn, target_id=target_id, limit=capped)
    return RunListResponse(
        runs=[
            RunResponse(run_id=str(row["id"]), status=_coerce_status(row["status"])) for row in rows
        ]
    )


@router.get("/{run_id}", response_model=RunDetailResponse)
def get_run(
    run_id: str,
    settings: SettingsDep,
) -> RunDetailResponse:
    """Fetch one run + a placeholder outputs list.

    Wave 3 doesn't persist outputs to the DB (no ``outputs`` table); we
    return an empty list so the response shape matches the contract while
    ``wt/runner`` decides where outputs land (likely a JSONL inside
    ``.aitap/runs/<id>/``).
    """
    with get_conn(settings) as conn:
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
    "/{run_id}/feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_feedback(
    run_id: str,
    payload: FeedbackCreate,
    settings: SettingsDep,
) -> FeedbackResponse:
    """Attach a feedback record to a run case."""
    with get_conn(settings) as conn:
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
    "/{run_id}/iterate",
    response_model=IterateResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_iterate(
    run_id: str,
    payload: IterateRequest,
    settings: SettingsDep,
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
        with get_conn(settings) as conn:
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
    """Hand off to :mod:`aitap.playground.runner` if it's importable.

    Returns the run status to surface in the response:
    - ``"done"`` when the runner module ran the prompt synchronously.
    - ``"running"`` when the runner module isn't available or chose to
      run async — the run row stays in ``running`` until something
      else (typically the runner itself) updates it.

    The intentional lack of LLM execution here keeps this worktree free
    of provider deps. We do **not** swallow exceptions raised by an
    available runner — those should propagate so callers see real failures.
    """
    # Lazy module probe so installing aitap without the playground
    # worktree's files still lets this package import. ``find_spec`` is
    # preferred over try/except ImportError because it lets a real bug
    # inside an existing module surface loudly rather than being silently
    # misread as "not implemented yet."
    import importlib
    from importlib.util import find_spec

    if find_spec("aitap.playground.runner") is None:
        return "running"

    playground_runner = importlib.import_module("aitap.playground.runner")
    invoke = getattr(playground_runner, "invoke_run", None)
    if invoke is None:
        return "running"

    # invoke_run is expected to handle its own persistence (status, cost,
    # outputs). If the contract evolves the route layer can be updated
    # independently — we don't try to second-guess what the runner does.
    try:
        invoke(settings=settings, run_id=run_id, payload=payload)
    except Exception:
        # Mark the run failed so the UI doesn't show a perpetually-running
        # request when the runner blows up. Re-raise so FastAPI returns 500.
        with get_conn(settings) as conn:
            runs_dao.update_run_status(conn, run_id, status="failed", finished=True)
        raise
    return "done"


def _load_outputs() -> list[RunOutput]:
    """Placeholder — Wave 3 doesn't persist per-case outputs to SQLite.

    Once ``wt/runner`` lands and we decide where outputs live (JSONL
    sidecars under ``.aitap/runs/<id>/`` is the current direction), this
    helper will read them. For now we return an empty list so the response
    shape stays contract-correct.
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
