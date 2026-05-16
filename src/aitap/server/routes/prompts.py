"""HTTP routes for prompt list/detail and new-version recording.

Endpoints (all rooted under ``/api`` once mounted in :mod:`aitap.server.app`):

    GET    /api/prompts                       -> PromptListResponse
    GET    /api/prompts/{prompt_id}           -> PromptDetailResponse
    POST   /api/prompts/{prompt_id}/versions  -> PromptVersionResponse

All response shapes come from :mod:`aitap.server.routes` (the OpenAPI
contract). The router never hand-builds a dict — every endpoint returns
a contract model so the generated TypeScript types stay in sync.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from aitap.scanner.models import PromptSite
from aitap.server.routes import (
    PromptDetailResponse,
    PromptListResponse,
    PromptSummary,
    PromptVersionCreate,
    PromptVersionInfo,
    PromptVersionResponse,
)
from aitap.server.routes._deps import get_db
from aitap.store import db as db_module
from aitap.store import history

router = APIRouter(tags=["prompts"])


@router.get("/prompts", response_model=PromptListResponse)
def list_prompts(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> PromptListResponse:
    """Return every prompt detected by the most recent scan.

    ``latest_version`` is the highest ``prompt_versions.version`` for
    the prompt, or 0 when no version has been recorded yet — the UI
    treats 0 as "discovered but never edited" and offers a "record v1"
    affordance.
    """
    rows = db_module.read_prompts(conn)
    summaries: list[PromptSummary] = []
    for row in rows:
        site = PromptSite.model_validate_json(row["payload_json"])
        latest = latest_version_for(conn, site.id)
        summaries.append(summary_from_site(site, latest))
    return PromptListResponse(prompts=summaries)


@router.get("/prompts/{prompt_id}", response_model=PromptDetailResponse)
def get_prompt(
    prompt_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> PromptDetailResponse:
    """Return one prompt's payload plus its complete version history.

    Returns 404 when the id is unknown — the frontend uses that to
    redirect away from stale bookmarks rather than rendering a blank
    detail page.
    """
    site = _load_site(conn, prompt_id)
    version_rows = history.read_versions(conn, prompt_id)
    versions = [
        PromptVersionInfo(
            version=int(row["version"]),
            note=row["note"],
            created_at=row["created_at"],
            created_by=row["created_by"],
            parent_version=row["parent_version"],
        )
        for row in version_rows
    ]
    return PromptDetailResponse(site=site, versions=versions)


@router.post(
    "/prompts/{prompt_id}/versions",
    response_model=PromptVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_prompt_version(
    prompt_id: str,
    payload: PromptVersionCreate,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> PromptVersionResponse:
    """Record a new version of *prompt_id*.

    The body carries the edited messages + parameters; we don't try to
    diff against the previous head here (that's a UI concern). We do
    validate that the prompt exists so callers can't seed orphan version
    rows by typo'ing an id.
    """
    _load_site(conn, prompt_id)  # 404s if missing
    with db_module.transaction(conn):
        new_version = history.record_version(
            conn,
            prompt_id,
            messages=payload.messages,
            parameters=payload.parameters,
            note=payload.note,
            created_by="human",
            parent_version=payload.parent_version,
        )
    return PromptVersionResponse(prompt_id=prompt_id, version=new_version)


# ---------------------------------------------------------------------------
# Helpers shared with the pipeline detail endpoint via the site_index.
# ---------------------------------------------------------------------------


def latest_version_for(conn: sqlite3.Connection, prompt_id: str) -> int:
    """Return the highest recorded version, or 0 when none exist.

    The contract types ``PromptSummary.latest_version`` as ``int``
    (non-optional), so 0 is the explicit "no versions yet" sentinel.
    """
    cur = conn.execute(
        "SELECT MAX(version) AS v FROM prompt_versions WHERE prompt_id = ?",
        (prompt_id,),
    )
    row = cur.fetchone()
    if row is None or row["v"] is None:
        return 0
    return int(row["v"])


def _load_site(conn: sqlite3.Connection, prompt_id: str) -> PromptSite:
    """Fetch a :class:`PromptSite` by id, raising 404 if absent."""
    cur = conn.execute("SELECT payload_json FROM prompts WHERE id = ?", (prompt_id,))
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"prompt {prompt_id!r} not found")
    return PromptSite.model_validate_json(row["payload_json"])


def summary_from_site(site: PromptSite, latest_version: int) -> PromptSummary:
    """Map a :class:`PromptSite` row to a contract summary."""
    return PromptSummary(
        id=site.id,
        name=site.name,
        provider=site.provider,
        file=site.location.file,
        line_start=site.location.line_start,
        purpose=site.purpose,
        confidence=site.confidence,
        latest_version=latest_version,
    )
