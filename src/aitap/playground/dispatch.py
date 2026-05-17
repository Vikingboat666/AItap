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
    Per-case outputs are *not* written to SQLite (no ``outputs`` table in
    the Wave 3 schema). The current direction is a JSONL sidecar under
    ``.aitap/runs/<run_id>/outputs.jsonl`` and that's TODO for M4 — when
    we know what schema the UI wants. For now the adapter only needs to
    move the run from ``running`` to ``done``/``failed`` and stamp the
    rolled-up cost so the API contract holds end-to-end.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from aitap.deep import client as client_module
from aitap.playground.pipeline_runner import (
    PipelineRunResult,
    run_pipeline,
)
from aitap.playground.runner import (
    PromptRunResult,
    run_prompt,
)
from aitap.scanner.models import Pipeline, PromptSite
from aitap.server.routes import DatasetCase, RunCreate
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
    """Execute *payload* and persist the terminal run state.

    Called from the FastAPI request handler in a synchronous context;
    we manage our own connection (rather than re-using the request's)
    because the handler closes its connection at request scope exit and
    the adapter's persistence outlives whatever scope the caller chose.

    On exception we update the run to ``failed`` (so the UI never shows
    a permanently-running spinner) and re-raise so FastAPI returns 500
    to the caller.
    """
    conn = store_db.connect(settings.db_path)
    try:
        store_db.init_db(conn)
        try:
            metrics = _dispatch(settings=settings, conn=conn, payload=payload)
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
    payload: RunCreate,
) -> _RunMetrics:
    """Resolve the target + cases, build the client, and run the appropriate runner.

    Returns a small metrics shim with the aggregated cost so the caller
    can persist it without caring whether a prompt or pipeline ran.
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
        return _RunMetrics(total_cost_usd=prompt_result.total_cost_usd)

    if payload.target_kind == "pipeline":
        pipeline = _load_pipeline(conn, payload.target_id)
        site_index = _load_site_index_for_pipeline(conn, pipeline)
        pipeline_result: PipelineRunResult = asyncio.run(
            run_pipeline(
                pipeline,
                "end_to_end",
                dataset_cases=cases,
                site_index=site_index,
                client=client,
                parameters=payload.parameters,
                version=payload.target_version,
            )
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


__all__ = ["ClientFactory", "invoke_run", "set_client_factory"]


# Keep a sidecar path helper exported so future M4 work writing outputs.jsonl
# under ``.aitap/runs/<id>/`` has a single source of truth for the layout.
def outputs_sidecar_path(settings: Settings, run_id: str) -> Path:
    """Resolve the per-run outputs JSONL path.

    TODO(M4): write outputs here from ``invoke_run`` and teach
    ``aitap.server.routes.runs._load_outputs`` to read it back.
    """
    return settings.runs_dir / run_id / "outputs.jsonl"
