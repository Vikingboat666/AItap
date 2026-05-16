"""HTTP routes for prompt version history + rollback.

Endpoints (mounted under ``/api`` by :mod:`aitap.server.app`):

    GET    /api/history/{prompt_id}             -> HistoryResponse
    POST   /api/history/{prompt_id}/rollback    -> PromptVersionResponse

The handlers are thin adapters over :mod:`aitap.store.history` — all
real work (next-version allocation, diff/rollback semantics, score
aggregation) happens there so the CLI and HTTP API share one
implementation.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from aitap.server.routes import (
    HistoryEntry,
    HistoryResponse,
    PromptVersionResponse,
    RollbackRequest,
)
from aitap.server.routes._deps import get_db
from aitap.store import db as db_module
from aitap.store import history

router = APIRouter(tags=["history"])


@router.get("/history/{prompt_id}", response_model=HistoryResponse)
def get_history(
    prompt_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> HistoryResponse:
    """Return every recorded version of *prompt_id* plus average scores.

    We require the prompt to exist in the ``prompts`` table (404 if not)
    so the frontend can surface "this prompt was deleted from the source
    code" distinctly from "no versions yet" (which is an empty list).
    """
    _assert_prompt_exists(conn, prompt_id)
    rows = history.read_versions(conn, prompt_id)
    entries = [
        HistoryEntry(
            version=int(row["version"]),
            note=row["note"],
            created_at=row["created_at"],
            created_by=row["created_by"],
            parent_version=row["parent_version"],
            avg_score=history.avg_score_for_version(conn, prompt_id, int(row["version"])),
        )
        for row in rows
    ]
    return HistoryResponse(prompt_id=prompt_id, entries=entries)


@router.post(
    "/history/{prompt_id}/rollback",
    response_model=PromptVersionResponse,
)
def rollback(
    prompt_id: str,
    payload: RollbackRequest,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> PromptVersionResponse:
    """Create a new head version whose content matches ``target_version``.

    Rollback is implemented as a forward step (no destructive delete) so
    the audit trail stays intact — see :func:`aitap.store.history.perform_rollback`
    for the lineage semantics.
    """
    _assert_prompt_exists(conn, prompt_id)
    try:
        with db_module.transaction(conn):
            new_version = history.perform_rollback(conn, prompt_id, payload.target_version)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PromptVersionResponse(prompt_id=prompt_id, version=new_version)


def _assert_prompt_exists(conn: sqlite3.Connection, prompt_id: str) -> None:
    """Raise HTTP 404 when *prompt_id* is not in the ``prompts`` table."""
    cur = conn.execute("SELECT 1 FROM prompts WHERE id = ?", (prompt_id,))
    if cur.fetchone() is None:
        raise HTTPException(status_code=404, detail=f"prompt {prompt_id!r} not found")
