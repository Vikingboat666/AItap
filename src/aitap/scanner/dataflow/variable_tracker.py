"""Direct variable-flow detection.

Recognises the canonical ``x = call_a(...); call_b(x)`` pattern where both
calls are at known PromptSite locations. The simplest dataflow shape and
also the most common in hand-written agent loops or content pipelines.

We deliberately stay scope-aware (function bodies, ``if/else`` arms, etc.)
but lexical-only: no inter-procedural analysis, no aliasing through
attribute writes (``self.x = ...``), no tracking through container
unpacking. Anything fancier is in scope for the v0.2 L2 enricher.

Edges produced:

- :attr:`EdgeKind.VARIABLE` — confidence HIGH when we see direct usage
  in the immediately following statements; downgraded to MEDIUM when the
  use site is in a deeper nested block (e.g. inside a loop), since we
  can't be sure execution actually reaches it.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

from aitap.scanner.models import Confidence, EdgeKind, PipelineEdge, PromptSite

from .base import call_at_or_within, index_sites_by_line


class VariableTracker:
    """Detector that links sites via simple variable assignment + use."""

    name = "variable_tracker"

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
        for scope in _iter_scopes(tree):
            edges.extend(_edges_in_scope(scope, line_index))
        return edges


def _iter_scopes(tree: ast.Module) -> list[ast.AST]:
    """Yield each scope whose statement list we'll walk independently.

    Functions, async functions, methods, class bodies, and the module
    itself. We don't cross scope boundaries (a variable in one function
    can't flow to a sibling function via name alone).
    """
    scopes: list[ast.AST] = [tree]
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            scopes.append(node)
    return scopes


def _edges_in_scope(
    scope: ast.AST,
    line_index: dict[int, PromptSite],
) -> list[PipelineEdge]:
    """Find variable-flow edges within a single scope's body.

    We make two passes over the scope's statements:

    1. Build ``var_to_source[name] = site_id`` whenever ``name = <call>``
       and the call is a known PromptSite. Reassignment overwrites — the
       most recent definition is the one that flows.
    2. Walk every Call inside the scope; for each argument that's a Name
       referencing a tracked variable AND the call itself is a known site,
       emit an edge.
    """
    edges: list[PipelineEdge] = []
    body: list[ast.stmt] = getattr(scope, "body", [])
    if not body:
        return edges

    # var_name -> (source_site_id, statement_index, depth)
    # depth = 0 at the scope's top level; >0 inside nested if/for/while/with bodies.
    var_to_source: dict[str, tuple[str, int, int]] = {}

    for stmt_idx, stmt in enumerate(body):
        # Collect any new assignments contributed by this statement.
        for tracked in _track_assignments(stmt, line_index):
            var_to_source[tracked.name] = (tracked.source_id, stmt_idx, 0)

        # Collect uses of tracked variables in this statement.
        for use in _track_uses(stmt, line_index, var_to_source, base_depth=0):
            edges.append(use)

    return edges


class _Tracked:
    __slots__ = ("name", "source_id")

    def __init__(self, name: str, source_id: str) -> None:
        self.name = name
        self.source_id = source_id


def _track_assignments(
    stmt: ast.stmt,
    line_index: dict[int, PromptSite],
) -> list[_Tracked]:
    """Return any variable→site bindings introduced by *stmt*.

    Handles ``x = call(...)`` and ``x: T = call(...)`` (AnnAssign), including
    the ubiquitous post-processing chain that real OpenAI/Anthropic code
    uses::

        outline = client.chat.completions.create(...).choices[0].message.content or ""

    The Assign.value here is a ``BoolOp(Or, [Attribute(Subscript(...)), Constant])``,
    not a bare Call — but the value the variable carries IS the LLM output,
    so we walk the whole RHS expression looking for a known PromptSite call.

    Ignores augmented assignments (``x += ...``) and unpacking (``a, b = ...``)
    since those don't produce a clean single-variable handle.
    """
    out: list[_Tracked] = []

    if isinstance(stmt, ast.Assign):
        site = _find_prompt_call_in_expr(stmt.value, line_index)
        if site is None:
            return out
        for tgt in stmt.targets:
            if isinstance(tgt, ast.Name):
                out.append(_Tracked(tgt.id, site.id))
        return out

    if isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
        site = _find_prompt_call_in_expr(stmt.value, line_index)
        if site is None:
            return out
        if isinstance(stmt.target, ast.Name):
            out.append(_Tracked(stmt.target.id, site.id))
    return out


def _find_prompt_call_in_expr(
    expr: ast.expr,
    line_index: dict[int, PromptSite],
) -> PromptSite | None:
    """Return the first PromptSite call reachable inside *expr*, or None.

    Matches our intent: an assignment whose RHS *contains* an LLM call binds
    the variable to that call's output (the surrounding ``.choices[0]...`` /
    ``or ""`` is just unwrapping).
    """
    for node in ast.walk(expr):
        if isinstance(node, ast.Call):
            site = call_at_or_within(node, line_index)
            if site is not None:
                return site
    return None


def _track_uses(
    stmt: ast.stmt,
    line_index: dict[int, PromptSite],
    var_to_source: dict[str, tuple[str, int, int]],
    *,
    base_depth: int,
) -> list[PipelineEdge]:
    """Walk *stmt* looking for Calls whose arguments use tracked variables.

    Confidence drops to MEDIUM when the use is inside a nested branching
    construct (we can't statically guarantee the execution path reaches
    the use after the assignment).
    """
    edges: list[PipelineEdge] = []
    nested_depth = base_depth + (
        1
        if isinstance(
            stmt, ast.If | ast.For | ast.AsyncFor | ast.While | ast.With | ast.AsyncWith | ast.Try
        )
        else 0
    )

    tracked_names = set(var_to_source.keys())
    for node in ast.walk(stmt):
        if not isinstance(node, ast.Call):
            continue
        target_site = call_at_or_within(node, line_index)
        if target_site is None:
            continue
        for arg in _vars_referenced_in_call_args(node, tracked_names):
            entry = var_to_source.get(arg)
            if entry is None:
                continue
            source_id, _, _ = entry
            if source_id == target_site.id:
                # Self-edge — same call site referenced via a variable. Skip.
                continue
            confidence = Confidence.HIGH if nested_depth == 0 else Confidence.MEDIUM
            edges.append(
                PipelineEdge(
                    source=source_id,
                    target=target_site.id,
                    kind=EdgeKind.VARIABLE,
                    via=arg,
                    confidence=confidence,
                )
            )

    # Dedupe edges produced by the same statement (same arg used multiple times).
    seen: set[tuple[str, str, str | None]] = set()
    unique: list[PipelineEdge] = []
    for edge in edges:
        key = (edge.source, edge.target, edge.via)
        if key in seen:
            continue
        seen.add(key)
        unique.append(edge)
    return unique


def _vars_referenced_in_call_args(call: ast.Call, tracked_names: set[str]) -> list[str]:
    """Tracked-variable Names referenced *anywhere* inside the call's args.

    Walks args + keyword.value subtrees so we catch deeply nested references
    like ``messages=[{"role": "user", "content": outline}]`` that real-world
    AI code uses. We deliberately skip the call.func subtree — a method name
    coincidentally matching a tracked variable shouldn't fabricate an edge.
    Nested Calls inside args are walked too, which means an outer call gets
    credit for a variable a nested call uses; the orchestrator's dedup keeps
    the result tight when intra_file_chain emits the same edge from a
    different angle.
    """
    used: set[str] = set()
    for arg in call.args:
        for node in ast.walk(arg):
            if isinstance(node, ast.Name) and node.id in tracked_names:
                used.add(node.id)
    for kw in call.keywords:
        for node in ast.walk(kw.value):
            if isinstance(node, ast.Name) and node.id in tracked_names:
                used.add(node.id)
    # Stable order so edge.via is deterministic across runs.
    return sorted(used)


_ = defaultdict  # silence unused-import warning
