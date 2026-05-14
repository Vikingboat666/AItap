"""Detect LangChain ``a | b | c | ...`` pipeline expressions.

LangChain Expression Language (LCEL) overloads ``|`` to compose Runnables:

    chain = prompt | model | parser | next_prompt | next_model

The AST shape is left-associative ``BinOp(BitOr)`` chains. We walk these
and emit edges between adjacent operands that we can identify as known
LLM call sites.

Identification heuristics for an operand:

- ``ast.Call`` whose ``lineno`` matches a known PromptSite — high confidence.
- ``ast.Name`` referencing a variable previously bound to a PromptSite —
  medium confidence (we'd need the variable_tracker's state to be sure).
- Anything else — skipped (we don't fabricate phantom nodes).

This keeps the detector dependency-free of the variable_tracker; if a
chain mixes inline calls and variables (``prompt_var | model | parser``),
the variable side gets dropped and the inline side still produces edges.
We accept that gap since real LCEL chains are usually all-inline or
ground-truth assigned-then-piped, and the variable tracker covers the
latter case via its own edges.
"""

from __future__ import annotations

import ast
from itertools import pairwise
from pathlib import Path

from aitap.scanner.models import Confidence, EdgeKind, PipelineEdge, PromptSite

from .base import call_at_or_within, index_sites_by_line


class LangChainPipe:
    name = "langchain_pipe"

    def detect(
        self,
        tree: ast.Module,
        sites_in_file: list[PromptSite],
        file_path: Path,
    ) -> list[PipelineEdge]:
        if len(sites_in_file) < 2:
            return []
        line_index = index_sites_by_line(sites_in_file)

        edges: list[PipelineEdge] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.BitOr):
                continue
            chain = _flatten_pipe_chain(node)
            for upstream, downstream in pairwise(chain):
                up_site = call_at_or_within(upstream, line_index)
                down_site = call_at_or_within(downstream, line_index)
                if up_site is None or down_site is None:
                    continue
                if up_site.id == down_site.id:
                    continue
                edges.append(
                    PipelineEdge(
                        source=up_site.id,
                        target=down_site.id,
                        kind=EdgeKind.LANGCHAIN_PIPE,
                        via="|",
                        confidence=Confidence.HIGH,
                    )
                )
        return edges


def _flatten_pipe_chain(node: ast.BinOp) -> list[ast.AST]:
    """Flatten a left-associative ``BitOr`` chain into operand order.

    AST shape for ``a | b | c | d``::

        BinOp(BinOp(BinOp(a, |, b), |, c), |, d)

    We unfold the left spine until we hit a non-BinOp, then collect
    everything in source order.
    """
    operands: list[ast.AST] = [node.right]
    cur: ast.AST = node.left
    while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.BitOr):
        operands.append(cur.right)
        cur = cur.left
    operands.append(cur)
    return list(reversed(operands))
