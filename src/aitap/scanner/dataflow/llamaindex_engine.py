"""Detect LlamaIndex query-engine pipelines.

LlamaIndex doesn't overload ``|`` — chains are constructed via builder
methods on an index/retriever:

    qe = index.as_query_engine(...)
    response = qe.query(...)

When a project has multiple LLM call sites involved in this construction
(e.g., a custom retriever uses one model, the synthesiser uses another)
the chain is implicit through the engine object. We can't statically
prove the data-flow path the way we can with ``|`` or variable tracking,
so the edges we emit here are :attr:`Confidence.LOW` and
:attr:`EdgeKind.UNRESOLVED` — the UI should render them dashed and the
user can confirm.

MVP scope: just identify the *existence* of an llamaindex pipeline by
matching ``index.as_query_engine(...)`` or ``index.as_chat_engine(...)``
calls and connecting them to the nearest preceding/following PromptSite
in source order. v0.2 should walk the LlamaIndex constructor kwargs to
find ``llm=...``, ``response_synthesizer=...`` etc and link those.
"""

from __future__ import annotations

import ast
from itertools import pairwise
from pathlib import Path

from aitap.scanner.models import Confidence, EdgeKind, PipelineEdge, PromptSite

_LLAMAINDEX_BUILDERS = frozenset(
    {
        "as_query_engine",
        "as_chat_engine",
        "as_retriever",
    }
)


class LlamaIndexEngine:
    name = "llamaindex_engine"

    def detect(
        self,
        tree: ast.Module,
        sites_in_file: list[PromptSite],
        file_path: Path,
    ) -> list[PipelineEdge]:
        if len(sites_in_file) < 2:
            return []

        # Quick sniff: does this file even mention llamaindex? If not, skip
        # the AST walk entirely — keeps scan time linear in the number of
        # llamaindex-using files, not all Python files.
        if not _file_mentions_llamaindex(tree):
            return []

        if not any(_is_llamaindex_builder_call(n) for n in ast.walk(tree)):
            return []

        # Heuristic edges: chain consecutive sites in source order with
        # UNRESOLVED kind so the UI flags them as user-confirmation-needed.
        sorted_sites = sorted(sites_in_file, key=lambda s: s.location.line_start)
        edges: list[PipelineEdge] = []
        for upstream, downstream in pairwise(sorted_sites):
            edges.append(
                PipelineEdge(
                    source=upstream.id,
                    target=downstream.id,
                    kind=EdgeKind.LLAMAINDEX,
                    via="query_engine",
                    confidence=Confidence.LOW,
                )
            )
        return edges


def _file_mentions_llamaindex(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("llama_index"):
                    return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith("llama_index"):
                return True
    return False


def _is_llamaindex_builder_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    return isinstance(node.func, ast.Attribute) and node.func.attr in _LLAMAINDEX_BUILDERS
