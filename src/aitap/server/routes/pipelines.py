"""HTTP routes for pipeline list/detail.

Endpoints (mounted under ``/api`` by :mod:`aitap.server.app`):

    GET    /api/pipelines                 -> PipelineListResponse
    GET    /api/pipelines/{pipeline_id}   -> PipelineDetailResponse

The detail endpoint also returns a ``site_index`` mapping prompt ids to
:class:`PromptSummary` records so the frontend can render the DAG nodes
without a separate round-trip per node.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from aitap.scanner.models import Pipeline, PromptSite
from aitap.server.routes import (
    PipelineDetailResponse,
    PipelineListResponse,
    PipelineSummary,
    PromptSummary,
)
from aitap.server.routes._deps import get_db
from aitap.server.routes.prompts import latest_version_for, summary_from_site
from aitap.store import db as db_module

router = APIRouter(tags=["pipelines"])


@router.get("/pipelines", response_model=PipelineListResponse)
def list_pipelines(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> PipelineListResponse:
    """Return one summary per detected pipeline.

    Summaries derive node/edge/entry/exit counts from the stored payload
    rather than running a fresh DAG analysis — the scanner already
    populated ``entry_points``/``exit_points`` and storing pre-computed
    counts in the schema would be redundant denormalisation.
    """
    rows = db_module.read_pipelines(conn)
    summaries: list[PipelineSummary] = []
    for row in rows:
        pipeline = Pipeline.model_validate_json(row["payload_json"])
        summaries.append(
            PipelineSummary(
                id=pipeline.id,
                name=pipeline.name,
                node_count=len(pipeline.nodes),
                edge_count=len(pipeline.edges),
                entry_count=len(pipeline.entry_points),
                exit_count=len(pipeline.exit_points),
            )
        )
    return PipelineListResponse(pipelines=summaries)


@router.get("/pipelines/{pipeline_id}", response_model=PipelineDetailResponse)
def get_pipeline(
    pipeline_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> PipelineDetailResponse:
    """Return a pipeline plus a prompt-id -> summary index for its nodes.

    The ``site_index`` lets the UI render every node label/file/line
    without a follow-up ``GET /api/prompts/{id}`` per node. We tolerate
    nodes that reference prompts no longer in the DB (e.g., a stale
    pipeline payload after a re-scan removed a call site) by simply
    omitting them from the index — the frontend renders an unknown node
    placeholder.
    """
    cur = conn.execute("SELECT payload_json FROM pipelines WHERE id = ?", (pipeline_id,))
    row = cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"pipeline {pipeline_id!r} not found")
    pipeline = Pipeline.model_validate_json(row["payload_json"])

    site_index: dict[str, PromptSummary] = {}
    for node in pipeline.nodes:
        cur = conn.execute("SELECT payload_json FROM prompts WHERE id = ?", (node.prompt_id,))
        prow = cur.fetchone()
        if prow is None:
            continue
        site = PromptSite.model_validate_json(prow["payload_json"])
        site_index[node.prompt_id] = summary_from_site(site, latest_version_for(conn, site.id))

    return PipelineDetailResponse(pipeline=pipeline, site_index=site_index)
