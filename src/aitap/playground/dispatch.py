"""Adapter wiring the playground runners into the ``POST /api/runs`` flow.

The runner modules (:mod:`aitap.playground.runner`,
:mod:`aitap.playground.pipeline_runner`) own the pure compute side of
"given a target + inputs + an LLM client, return outputs + cost". They
are deliberately ignorant of the FastAPI request lifecycle and SQLite
persistence — pulling those concerns into a dedicated adapter keeps the
runner module testable in isolation and lets the API route stay thin.

This adapter is what ``aitap.server.routes.runs._invoke_runner_safely``
probes via ``importlib.util.find_spec``. It:

1. Resolves the target (``payload.target_kind`` + ``payload.target_id``)
   against the SQLite store.
2. Loads dataset cases — inline on the payload first, falling back to a
   ``.aitap/datasets/<dataset_id>.cases.jsonl`` sidecar.
3. Builds an :class:`LLMClient` for the requested provider/model via the
   :mod:`aitap.deep.client` factory. Tests can swap this out with
   :func:`set_client_factory` so the full ``invoke_run`` path runs
   offline against :class:`aitap.deep.testing.MockLLMClient`.
4. Dispatches to :func:`run_prompt` or :func:`run_pipeline` and awaits
   the result via ``asyncio.run`` (the FastAPI handler that calls us is
   sync).
5. Persists the terminal status + cost back to ``runs``.

Output persistence:
    Per-case outputs are written to a JSONL sidecar at
    ``.aitap/runs/<run_id>/outputs.jsonl`` — one JSON object per case,
    aligned with the :class:`aitap.server.routes.RunOutput` contract so
    the reader (:func:`aitap.server.routes.runs._load_outputs`) can
    ``RunOutput.model_validate`` each line with zero conversion. Extra
    forward-looking fields (``cost_usd``, ``usage``, ``latency_ms``) are
    included alongside the contract fields; pydantic ignores them when
    constructing :class:`RunOutput` but they are available for the
    Wave 4 judge / critic to score and target weak cases.

    The sidecar is written atomically (``outputs.jsonl.tmp`` + atomic
    ``os.replace``) so a reader concurrent with a writer never sees a
    half-flushed file. The rolled-up run-level ``cost_usd`` and terminal
    status continue to land in the ``runs`` SQLite row exactly as
    before — the sidecar is additive context, not a replacement.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from aitap.deep import client as client_module
from aitap.deep.client import ChatResponse
from aitap.playground.pipeline_runner import (
    PipelineRunResult,
    run_pipeline,
)
from aitap.playground.runner import (
    PromptRunResult,
    run_prompt,
)
from aitap.scanner.models import Pipeline, PromptSite
from aitap.server.routes import DatasetCase, RunCreate, RunOutput
from aitap.store import db as store_db
from aitap.store import files as store_files
from aitap.store import runs as runs_dao

if TYPE_CHECKING:
    from aitap.config import Settings
    from aitap.deep.client import LLMClient

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Client factory indirection (so tests can inject MockLLMClient)
# ---------------------------------------------------------------------------

# Signature mirrors :func:`aitap.deep.client.get_client` so the default
# wiring is a straight pass-through and tests can substitute a callable
# that returns a MockLLMClient without monkey-patching the deep package.
ClientFactory = Callable[[str, str], "LLMClient"]


def _default_client_factory(provider: str, model: str) -> LLMClient:
    """Default factory: defer to :func:`aitap.deep.client.get_client`.

    We do not pass an API key — providers fall back to env vars (which is
    the documented contract for the deep package). Tests should override
    this whole factory via :func:`set_client_factory` rather than try to
    fake env vars.
    """
    return client_module.get_client(provider, model)


# Mutable on purpose: the tests' set_client_factory swap. Kept lower-case so
# the constant-redefinition rule doesn't flag us; the docstring on the setter
# is the source of truth for "treat this as private state, not a constant."
_client_factory: ClientFactory = _default_client_factory


def set_client_factory(factory: ClientFactory | None) -> None:
    """Install (or reset) the LLMClient factory used by :func:`invoke_run`.

    Passing ``None`` restores the production default. This is the
    designated test seam — pytest fixtures should ``yield`` then call
    ``set_client_factory(None)`` to avoid bleeding mocks across tests.
    """
    global _client_factory
    _client_factory = factory if factory is not None else _default_client_factory


# ---------------------------------------------------------------------------
# Public adapter entry point
# ---------------------------------------------------------------------------


def invoke_run(
    *,
    settings: Settings,
    run_id: str,
    payload: RunCreate,
) -> None:
    """Execute *payload* and persist the terminal run state + per-case outputs.

    Called from the FastAPI request handler in a synchronous context;
    we manage our own connection (rather than re-using the request's)
    because the handler closes its connection at request scope exit and
    the adapter's persistence outlives whatever scope the caller chose.

    The terminal status + rolled-up cost land in the ``runs`` SQLite row;
    per-case outputs (including per-case ``error`` for cases that fail
    in isolation) land in :func:`outputs_sidecar_path`. Writing the
    sidecar after a successful dispatch *and* on per-case failures means
    a Wave 4 judge can score every case the runner did manage to
    produce, even within an otherwise-degraded run.

    On run-level exception (target not found, malformed payload, etc.)
    we update the run to ``failed`` so the UI never shows a permanently-
    running spinner, then re-raise so FastAPI returns 500. We do not
    write a sidecar in that path because there are no per-case results
    to persist — the reader falls back to an empty list, which is the
    documented behaviour when ``outputs.jsonl`` is absent.
    """
    conn = store_db.connect(settings.db_path)
    try:
        store_db.init_db(conn)
        try:
            metrics = _dispatch(
                settings=settings,
                conn=conn,
                run_id=run_id,
                payload=payload,
            )
        except Exception:
            # Best-effort failure marker. We swallow any secondary error
            # from the status update so the original exception (which is
            # more diagnostic) is the one that surfaces.
            try:
                runs_dao.update_run_status(
                    conn, run_id, status="failed", cost_usd=0.0, finished=True
                )
            except Exception:
                _LOGGER.exception("failed to mark run %s as failed", run_id)
            raise
        runs_dao.update_run_status(
            conn,
            run_id,
            status="done",
            cost_usd=metrics.total_cost_usd,
            finished=True,
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _dispatch(
    *,
    settings: Settings,
    conn: sqlite3.Connection,
    run_id: str,
    payload: RunCreate,
) -> _RunMetrics:
    """Resolve the target + cases, build the client, and run the appropriate runner.

    Returns a small metrics shim with the aggregated cost so the caller
    can persist it without caring whether a prompt or pipeline ran.

    Per-case outputs are written to the JSONL sidecar from inside this
    function (after the runner returns and before we hand control back
    to :func:`invoke_run`). Writing here — rather than in ``invoke_run``
    — keeps the prompt-vs-pipeline shape branching local to where we
    actually have the typed result; the caller only needs the cost
    roll-up.
    """
    cases = _resolve_cases(settings, payload)
    client = _client_factory(payload.provider.value, payload.model)

    if payload.target_kind == "prompt":
        site = _load_prompt_site(conn, payload.target_id)
        prompt_result: PromptRunResult = asyncio.run(
            run_prompt(
                site=site,
                version=payload.target_version,
                dataset_cases=cases,
                client=client,
                parameters=payload.parameters,
            )
        )
        write_outputs_sidecar(
            settings=settings,
            run_id=run_id,
            outputs=prompt_result.outputs,
            responses=prompt_result.responses,
        )
        return _RunMetrics(total_cost_usd=prompt_result.total_cost_usd)

    if payload.target_kind == "pipeline":
        pipeline = _load_pipeline(conn, payload.target_id)
        site_index = _load_site_index_for_pipeline(conn, pipeline)
        # Honour the requested run mode (A·D4). ``None`` maps to
        # ``end_to_end`` so clients that never send a mode keep their
        # historical whole-DAG behaviour byte-for-byte. The route layer
        # (routes/runs.py) has already validated field/mode consistency
        # and 422'd inconsistent requests, so we forward the selectors as
        # given; the runner re-checks them and raises ValueError on any
        # residual inconsistency (which invoke_run turns into a failed run).
        mode = payload.pipeline_mode or "end_to_end"
        pipeline_result: PipelineRunResult = asyncio.run(
            run_pipeline(
                pipeline,
                mode,
                dataset_cases=cases,
                site_index=site_index,
                client=client,
                parameters=payload.parameters,
                version=payload.target_version,
                node_id=payload.pipeline_node_id,
                segment=payload.pipeline_segment,
            )
        )
        # Pipeline mode does not surface per-case ChatResponses through
        # PipelineRunResult — per-case cost/usage rolls up at the node-
        # walk level inside ``_run_single_case_segment``. We persist the
        # contract fields (text + intermediate + error) and leave the
        # forward-looking cost/usage fields null so the judge falls back
        # to the run-level cost on the runs table. Sink output is in
        # ``RunOutput.text``; per-node intermediates ride along in
        # ``RunOutput.intermediate`` — no separate intermediates.jsonl
        # is needed because the contract already carries that shape.
        write_outputs_sidecar(
            settings=settings,
            run_id=run_id,
            outputs=pipeline_result.outputs,
            responses=None,
        )
        return _RunMetrics(total_cost_usd=pipeline_result.metrics.total_cost_usd)

    raise ValueError(f"unknown target_kind: {payload.target_kind!r}")


class _RunMetrics:
    """Minimal cost-only shim shared between prompt + pipeline branches.

    We deliberately don't surface tokens here — the persistence layer
    only stores ``cost_usd`` and per-case usage lives on the per-case
    outputs (which the JSONL sidecar will carry in M4). Keeping this
    narrow means callers can't accidentally drift the contract.
    """

    __slots__ = ("total_cost_usd",)

    def __init__(self, *, total_cost_usd: float) -> None:
        self.total_cost_usd = total_cost_usd


def _resolve_cases(settings: Settings, payload: RunCreate) -> list[DatasetCase]:
    """Inline cases take precedence; otherwise read the JSONL sidecar.

    Returning an empty list is valid — :func:`run_prompt` simply makes
    zero LLM calls (cost stays at 0.0, status still flips to ``done``).
    This is intentional: the contract test seeds a payload with
    ``cases=[]`` to verify the persistence/state-machine path without
    needing a real provider.
    """
    if payload.cases:
        return list(payload.cases)
    if payload.dataset_id:
        path = store_files.dataset_path(settings.datasets_dir, payload.dataset_id)
        rows = store_files.read_cases(path)
        return [DatasetCase(inputs=row) for row in rows]
    return []


def _load_prompt_site(conn: sqlite3.Connection, prompt_id: str) -> PromptSite:
    """Read a PromptSite from the ``prompts`` table by id.

    The scanner stores the full pydantic dump in ``payload_json``; we
    round-trip via :meth:`PromptSite.model_validate_json` so any future
    field additions on the model are picked up automatically.
    """
    cur = conn.execute("SELECT payload_json FROM prompts WHERE id = ?", (prompt_id,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"prompt {prompt_id!r} not found in store")
    try:
        return PromptSite.model_validate_json(_row_payload(row))
    except Exception as exc:
        raise ValueError(f"prompt {prompt_id!r} has malformed payload_json") from exc


def _load_pipeline(conn: sqlite3.Connection, pipeline_id: str) -> Pipeline:
    """Read a Pipeline from the ``pipelines`` table by id."""
    cur = conn.execute("SELECT payload_json FROM pipelines WHERE id = ?", (pipeline_id,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"pipeline {pipeline_id!r} not found in store")
    try:
        return Pipeline.model_validate_json(_row_payload(row))
    except Exception as exc:
        raise ValueError(f"pipeline {pipeline_id!r} has malformed payload_json") from exc


def _load_site_index_for_pipeline(
    conn: sqlite3.Connection,
    pipeline: Pipeline,
) -> dict[str, PromptSite]:
    """Fetch every node's PromptSite up front so the runner can walk the DAG.

    Missing nodes (a stale pipeline payload referencing a prompt that's
    been re-scanned away) raise here rather than at the runner — the
    runner's KeyError surfaces deeper in the stack and is harder to
    diagnose than a clear "your pipeline references a missing prompt"
    error at adapter time.
    """
    site_index: dict[str, PromptSite] = {}
    for node in pipeline.nodes:
        if node.prompt_id in site_index:
            continue
        site_index[node.prompt_id] = _load_prompt_site(conn, node.prompt_id)
    return site_index


def _row_payload(row: sqlite3.Row) -> str:
    """Extract ``payload_json`` from a Row as a str (pyright-safe)."""
    raw = row["payload_json"]
    if isinstance(raw, str):
        return raw
    # SQLite *can* return bytes for TEXT columns under exotic configurations;
    # fall back to a json-safe decode so the model_validate_json call works.
    if isinstance(raw, bytes | bytearray):
        return raw.decode("utf-8")
    # Last resort: JSON-encode whatever object the row gave us so we don't
    # crash inside pydantic with an unintelligible TypeError.
    return json.dumps(raw)


__all__ = [
    "ClientFactory",
    "invoke_run",
    "outputs_sidecar_path",
    "set_client_factory",
    "write_outputs_sidecar",
]


# Keep a sidecar path helper exported so the reader side
# (:func:`aitap.server.routes.runs._load_outputs`) and the writer here
# share a single source of truth for the layout. Changing one without
# the other silently desyncs the API contract — the constant lives in
# this module rather than ``config.py`` because the layout is owned by
# the dispatch adapter, not the global Settings surface.
def outputs_sidecar_path(settings: Settings, run_id: str) -> Path:
    """Resolve the per-run outputs JSONL path.

    Layout: ``<runs_dir>/<run_id>/outputs.jsonl``. The reader
    (:func:`aitap.server.routes.runs._load_outputs`) treats a missing
    file as an empty outputs list (legitimate for runs still in
    ``running`` status); the writer always writes atomically via a
    ``.tmp`` neighbour + ``os.replace`` so the reader never sees a
    half-flushed file.
    """
    return settings.runs_dir / run_id / "outputs.jsonl"


def _run_output_to_record(
    output: RunOutput,
    response: ChatResponse | None,
) -> dict[str, object]:
    """Serialise one RunOutput + optional per-case response into a JSONL row.

    The fields that match :class:`RunOutput` are emitted exactly as
    declared by the API contract (so the reader's ``model_validate``
    call round-trips with zero conversion). Forward-looking fields the
    Wave 4 judge / critic will consume — per-case cost, token usage,
    latency — are added alongside; pydantic's default ``extra="ignore"``
    on ``_ApiModel`` drops them when constructing the response shape so
    the API contract stays stable while the on-disk format is richer.
    """
    record: dict[str, object] = {
        # Contract fields (round-trip into RunOutput via model_validate).
        "case_index": output.case_index,
        "text": output.text,
        "image_path": output.image_path,
        "error": output.error,
        "intermediate": output.intermediate,
        # Forward-looking judge/critic context (extras, ignored by RunOutput).
        "cost_usd": response.cost_usd if response is not None else None,
        "usage": (
            {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }
            if response is not None
            else None
        ),
        # Latency is not currently measured by the runner; reserved so
        # the on-disk shape is stable once the runner starts capturing
        # it. Judge code reading older sidecars should tolerate ``None``.
        "latency_ms": None,
    }
    return record


def write_outputs_sidecar(
    *,
    settings: Settings,
    run_id: str,
    outputs: list[RunOutput],
    responses: list[ChatResponse | None] | None,
) -> None:
    """Persist per-case ``outputs`` to ``outputs.jsonl`` atomically.

    Atomicity guarantees:
        We write to ``outputs.jsonl.tmp`` in the same directory and then
        :func:`os.replace` to the final name. ``os.replace`` is atomic
        on both POSIX and Windows for paths on the same volume, which
        is guaranteed here because both names share the run directory.
        A reader concurrent with the writer either sees no file (and
        returns an empty list) or sees the fully-written file — never
        a partial line.

    Parameters:
        outputs: The per-case RunOutput list from the runner. Index
            order is preserved verbatim so ``case_index`` lines up.
        responses: Per-case ChatResponse list from prompt mode (None
            for cases that errored). ``None`` for pipeline mode where
            per-case responses aren't surfaced through PipelineRunResult.

    Writing an empty outputs list still creates the file (zero lines).
    This is intentional: it lets callers distinguish "run completed
    with no cases" (empty file present) from "run never wrote outputs"
    (file absent — e.g., the dispatch raised before reaching here).
    """
    target = outputs_sidecar_path(settings, run_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")

    # Pair each output with its response (or None for pipeline / errored
    # cases). zip_longest semantics not needed — both lists are 1:1 with
    # cases in prompt mode; pipeline mode passes None so the pairing
    # degenerates to "every record has response=None."
    if responses is None:
        paired: list[tuple[RunOutput, ChatResponse | None]] = [(o, None) for o in outputs]
    else:
        # The runner contract guarantees ``len(responses) == len(outputs)``
        # in prompt mode; assert to surface a runner bug loudly rather
        # than silently truncate the sidecar.
        if len(responses) != len(outputs):
            raise RuntimeError(
                f"runner returned mismatched outputs ({len(outputs)}) "
                f"and responses ({len(responses)}) for run {run_id!r}"
            )
        paired = list(zip(outputs, responses, strict=True))

    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            for output, response in paired:
                record = _run_output_to_record(output, response)
                handle.write(json.dumps(record, ensure_ascii=False))
                handle.write("\n")
            # Force fsync-ish durability before the rename so a crash in
            # the window between write() and replace() can't leave a
            # zero-byte tmp file masquerading as a valid sidecar.
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except Exception:
        # Best-effort cleanup of the half-written tmp so a retried
        # invoke_run doesn't trip over stale partial state. We do NOT
        # touch the (possibly-pre-existing) target on failure — the
        # previous good file, if any, is left intact.
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            _LOGGER.exception("failed to clean up %s after sidecar write error", tmp)
        raise
