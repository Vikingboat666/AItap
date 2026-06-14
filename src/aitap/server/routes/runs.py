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

import json
import logging
import sqlite3
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, status

from aitap.config import Settings
from aitap.iterate import iterate_one_round
from aitap.playground.dispatch import outputs_sidecar_path
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

_LOGGER = logging.getLogger(__name__)

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

    Wave 5 addition (A·D1/A·D3): pipeline runs carry an explicit
    ``pipeline_mode`` plus mode-specific selectors. We validate their
    consistency here — *before* writing the runs row — so a malformed
    request 422s cleanly without leaving an orphan ``running`` row behind.
    """
    _validate_pipeline_mode(payload)

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
        profile_id=payload.profile_id,
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
    settings: Annotated[Settings, Depends(get_settings)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> RunDetailResponse:
    """Fetch one run + per-case outputs from the JSONL sidecar.

    Per-case outputs live in
    ``<runs_dir>/<run_id>/outputs.jsonl`` (written by
    :func:`aitap.playground.dispatch._write_outputs_sidecar`). Runs still
    in the ``running`` status — or runs that failed at the run level
    before any case completed — have no sidecar file; :func:`_load_outputs`
    returns an empty list in that case so the contract shape is preserved.
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
        outputs=_load_outputs(settings, run_id),
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


def _validate_pipeline_mode(payload: RunCreate) -> None:
    """Enforce ``pipeline_mode`` / selector consistency for pipeline runs.

    Mapping (see wave-5-design.md A·D1 / A·D3):

    - ``target_kind != "pipeline"``: prompt runs never carry pipeline
      selectors, so we no-op. A stray ``pipeline_mode`` on a prompt payload
      is ignored, not an error — the field is simply out of scope there.
    - ``pipeline_mode in (None, "end_to_end")``: today's behaviour. We do
      **not** consistency-check ``pipeline_node_id`` / ``pipeline_segment``
      here; the runner ignores them and a lenient backend keeps the
      additive contract change non-breaking for clients that send stray
      defaults.
    - ``pipeline_mode == "node"``: requires a **non-empty**
      ``pipeline_node_id`` (a blank string is treated as missing); must not
      also carry ``pipeline_segment`` (ambiguous — which one wins?).
    - ``pipeline_mode == "segment"``: requires a **non-empty**
      ``pipeline_segment`` (an empty list is the "zero-node segment
      silently succeeds" footgun A·D3 explicitly blocks); must not also
      carry ``pipeline_node_id``.

    Design note on the conflict rules: rather than silently pick a winner
    when both a node id and a segment are present, we reject the request.
    An ambiguous selector almost always signals a client bug (e.g. stale
    UI state not cleared on a mode switch); a 422 surfaces it immediately
    instead of running something the caller didn't intend. The two
    permissive modes (``None``/``end_to_end``) stay lenient on purpose so
    the additive change can't break an existing client.

    Raises:
        HTTPException: 422 with a human-readable ``detail`` naming the
            offending field on any inconsistency.
    """
    if payload.target_kind != "pipeline":
        return

    mode = payload.pipeline_mode
    if mode is None or mode == "end_to_end":
        return

    if mode == "node":
        if not payload.pipeline_node_id:
            # ``not`` (rather than ``is None``) so a blank ``""`` is treated
            # as missing too — symmetric with the segment branch's empty-list
            # check below. Otherwise an empty string slips past here and only
            # fails deep in the runner as a 500 ("node not found") instead of
            # a clean 422.
            _raise_422("pipeline_mode='node' requires a non-empty pipeline_node_id")
        if payload.pipeline_segment is not None:
            _raise_422(
                "pipeline_mode='node' must not carry pipeline_segment; send only pipeline_node_id"
            )
        return

    if mode == "segment":
        if not payload.pipeline_segment:
            _raise_422("pipeline_mode='segment' requires a non-empty pipeline_segment")
        if payload.pipeline_node_id is not None:
            _raise_422(
                "pipeline_mode='segment' must not carry pipeline_node_id; "
                "send only pipeline_segment"
            )
        return


def _raise_422(detail: str) -> None:
    """Raise an HTTP 422 with *detail* (factored out for a single call site shape).

    The status code is the literal ``422`` rather than
    ``status.HTTP_422_UNPROCESSABLE_ENTITY``: newer Starlette deprecated that
    constant in favour of ``HTTP_422_UNPROCESSABLE_CONTENT``, and the two
    spellings drift across the versions pinned in different worktrees. The
    integer is unambiguous, stable, and warning-free.
    """
    raise HTTPException(status_code=422, detail=detail)


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


def _load_outputs(settings: Settings, run_id: str) -> list[RunOutput]:
    """Read per-case outputs from the JSONL sidecar.

    Layout: ``<runs_dir>/<run_id>/outputs.jsonl`` — one JSON record per
    case, written by :func:`aitap.playground.dispatch._write_outputs_sidecar`.

    Missing file → empty list. This is the legitimate "run is still
    ``running``" and "run failed at the run level before any case
    completed" path; the API contract (and the existing integration
    tests) tolerate an empty outputs list for those states.

    Malformed lines are skipped with a warning rather than crashing the
    detail endpoint. The sidecar is forward-compatible — extra fields
    that the API contract doesn't know about are silently ignored by
    pydantic — but a *broken* line (truncated JSON, wrong type at
    ``case_index``) should not 500 the whole UI; we log it and move on
    so the rest of the run's outputs are still surfaced.
    """
    path = outputs_sidecar_path(settings, run_id)
    if not path.exists():
        return []
    outputs: list[RunOutput] = []
    try:
        # Open in text mode with explicit utf-8; the writer matches.
        with path.open("r", encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                stripped = raw_line.strip()
                if not stripped:
                    # Tolerate trailing newlines / blank separators.
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError:
                    _LOGGER.warning("skipping malformed JSON in %s at line %d", path, line_number)
                    continue
                try:
                    outputs.append(RunOutput.model_validate(record))
                except Exception:  # pydantic ValidationError, but also generic guard
                    _LOGGER.warning(
                        "skipping unparseable RunOutput in %s at line %d", path, line_number
                    )
                    continue
    except OSError:
        _LOGGER.exception("failed to read outputs sidecar at %s", path)
        return []
    return outputs


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
