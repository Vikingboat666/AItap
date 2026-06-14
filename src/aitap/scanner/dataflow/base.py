"""Shared types and helpers for the dataflow detectors.

Every concrete detector implements :class:`DataflowDetector` — a Protocol
rather than an ABC so detectors can be plain modules or callables, not
forced into a class hierarchy.

The orchestrator in :mod:`aitap.scanner.dataflow` calls every registered
detector against each file's AST + the PromptSites we already extracted,
then dedupes the resulting edges and builds Pipeline objects.
"""

from __future__ import annotations

import ast
import hashlib
import re
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from aitap.scanner.models import (
    Confidence,
    Pipeline,
    PipelineEdge,
    PipelineNode,
    PromptSite,
)

if TYPE_CHECKING:
    pass


class DataflowDetector(Protocol):
    """A detector that proposes :class:`PipelineEdge`\\s for one file.

    Detectors should be cheap to construct and stateless; the orchestrator
    creates fresh instances each scan.
    """

    name: str

    def detect(
        self,
        tree: ast.Module,
        sites_in_file: list[PromptSite],
        file_path: Path,
    ) -> list[PipelineEdge]: ...


# --------------------------------------------------------------------------- #
# Site lookup helpers — detectors share the same "is this AST node a known    #
# prompt site?" question, so we centralise the answer.                        #
# --------------------------------------------------------------------------- #


def index_sites_by_line(sites: list[PromptSite]) -> dict[int, PromptSite]:
    """Return {line_start: site} for the calls in this file.

    Two sites on the exact same line are rare (we already deduplicate by
    column in the scanner); on conflict we keep the first seen, which
    matches the engine's traversal order.
    """
    out: dict[int, PromptSite] = {}
    for site in sites:
        out.setdefault(site.location.line_start, site)
    return out


def call_at_or_within(node: ast.AST, line_index: dict[int, PromptSite]) -> PromptSite | None:
    """Return the PromptSite covered by *node*, if any.

    Calls span multiple lines (multi-arg, multi-line strings); line_start is
    where the call expression begins. We match on the AST node's lineno.
    """
    if not isinstance(node, ast.Call):
        return None
    lineno = getattr(node, "lineno", None)
    if lineno is None:
        return None
    return line_index.get(lineno)


# --------------------------------------------------------------------------- #
# Edge dedup + pipeline construction                                          #
# --------------------------------------------------------------------------- #


def dedupe_edges(edges: list[PipelineEdge]) -> list[PipelineEdge]:
    """Collapse duplicate edges, preferring the highest-confidence variant.

    Two edges are duplicates when (source, target, kind) match. When kinds
    differ we keep both — a variable flow AND a langchain pipe edge between
    the same two sites are independent signals worth surfacing.
    """
    by_key: dict[tuple[str, str, str], PipelineEdge] = {}
    rank = {Confidence.HIGH: 3, Confidence.MEDIUM: 2, Confidence.LOW: 1}
    for edge in edges:
        key = (edge.source, edge.target, edge.kind.value)
        existing = by_key.get(key)
        if existing is None or rank[edge.confidence] > rank[existing.confidence]:
            by_key[key] = edge
    return list(by_key.values())


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    return _SLUG_RE.sub("_", text.lower()).strip("_") or "pipeline"


def build_pipelines_from_edges(
    edges: list[PipelineEdge],
    sites: list[PromptSite],
) -> list[Pipeline]:
    """Group *edges* into weakly-connected components → :class:`Pipeline`\\s.

    A weakly-connected component (WCC) on a directed graph is the set of
    nodes you can reach by ignoring edge direction. Every chain of LLM
    calls connected by data flow becomes one Pipeline. Isolated PromptSites
    (no edges) stay out of the pipeline list — they're already in
    ``ScanResult.prompts``.
    """
    if not edges:
        return []

    # Union-find over prompt ids.
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent.get(x, x), parent.get(x, x))
            x = parent.get(x, x)
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    site_ids = {s.id for s in sites}
    for edge in edges:
        if edge.source in site_ids and edge.target in site_ids:
            parent.setdefault(edge.source, edge.source)
            parent.setdefault(edge.target, edge.target)
            union(edge.source, edge.target)

    # Bucket edges by component root.
    components: dict[str, list[PipelineEdge]] = defaultdict(list)
    for edge in edges:
        if edge.source in parent and edge.target in parent:
            components[find(edge.source)].append(edge)

    by_id = {s.id: s for s in sites}
    pipelines: list[Pipeline] = []
    for root_id, comp_edges in components.items():
        node_ids = sorted({e.source for e in comp_edges} | {e.target for e in comp_edges})
        nodes = [
            PipelineNode(
                prompt_id=nid,
                label=by_id[nid].name if nid in by_id else None,
            )
            for nid in node_ids
        ]

        incoming = {e.target for e in comp_edges}
        outgoing = {e.source for e in comp_edges}
        entries = sorted(n for n in node_ids if n not in incoming)
        exits = sorted(n for n in node_ids if n not in outgoing)

        # Stable id from the sorted node ids — re-running the scan against
        # unchanged code produces the same Pipeline.id.
        digest = hashlib.sha1("|".join(node_ids).encode("utf-8")).hexdigest()[:12]

        # Naming heuristic: borrow the prettier of the entry sites' names.
        # When the component has multiple entry sites we just sort by name
        # for determinism.
        anchor = by_id.get(entries[0]) if entries else by_id.get(node_ids[0])
        base_name = anchor.name if anchor else f"pipeline_{root_id[:6]}"
        name = _slug(f"{base_name}_pipeline")

        pipelines.append(
            Pipeline(
                id=digest,
                name=name,
                nodes=nodes,
                edges=comp_edges,
                entry_points=entries,
                exit_points=exits,
            )
        )

    pipelines.sort(key=lambda p: p.name)
    return pipelines
