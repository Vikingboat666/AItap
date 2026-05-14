"""Detect same-file function-call chains.

Pattern: ``def f(x): ... call_a(...) ...`` ``def g(x): ... call_b(...) ...``
called as ``g(f(x))`` or as ``a = f(x); g(a)`` (the latter overlaps with
the variable_tracker; we let both fire and the orchestrator dedupes).

This catches the common refactor where a developer split prompt logic
into helper functions but the data flow is still obvious from a single
file:

    def summarise(text: str) -> str:
        return openai.ChatCompletion.create(... messages=[{"content": text}]).choices[0].message.content

    def critique(summary: str) -> str:
        return anthropic.messages.create(... messages=[{"content": summary}]).content[0].text

    final = critique(summarise(raw_text))

Edges produced are :attr:`EdgeKind.FUNCTION` with HIGH confidence when
the inner call clearly returns the value of an LLM call (the function
body's last statement is ``return <Call>`` matching a known site).
"""

from __future__ import annotations

import ast
from pathlib import Path

from aitap.scanner.models import Confidence, EdgeKind, PipelineEdge, PromptSite

from .base import call_at_or_within, index_sites_by_line


class IntraFileChain:
    name = "intra_file_chain"

    def detect(
        self,
        tree: ast.Module,
        sites_in_file: list[PromptSite],
        file_path: Path,
    ) -> list[PipelineEdge]:
        if len(sites_in_file) < 2:
            return []

        line_index = index_sites_by_line(sites_in_file)

        # Map function-name -> site_id when the function's "primary return"
        # is a known PromptSite call.
        fn_to_site: dict[str, str] = {}
        for fn in _iter_functions(tree):
            site = _function_returns_prompt(fn, line_index)
            if site is not None:
                fn_to_site[fn.name] = site.id

        if len(fn_to_site) < 2:
            return []

        edges: list[PipelineEdge] = []
        edges.extend(_nested_call_edges(tree, fn_to_site))
        edges.extend(_variable_mediated_edges(tree, fn_to_site))
        return edges


def _nested_call_edges(tree: ast.Module, fn_to_site: dict[str, str]) -> list[PipelineEdge]:
    """Edges from ``g(f(x))`` patterns where both f and g are wrapper functions."""
    edges: list[PipelineEdge] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        outer_name = _called_name(node)
        if outer_name is None or outer_name not in fn_to_site:
            continue
        for arg in node.args:
            if not isinstance(arg, ast.Call):
                continue
            inner_name = _called_name(arg)
            if inner_name is None or inner_name not in fn_to_site or inner_name == outer_name:
                continue
            edges.append(
                PipelineEdge(
                    source=fn_to_site[inner_name],
                    target=fn_to_site[outer_name],
                    kind=EdgeKind.FUNCTION,
                    via=f"{inner_name}() → {outer_name}()",
                    confidence=Confidence.HIGH,
                )
            )
    return edges


def _variable_mediated_edges(tree: ast.Module, fn_to_site: dict[str, str]) -> list[PipelineEdge]:
    """Edges from ``a = f(...); g(a, ...)`` where f and g are wrapper functions.

    The most common shape in real RAG / agent code: each pipeline stage is a
    helper function and the orchestrator wires them with named intermediates.
    Variable scope is per-function (or module) just like in variable_tracker —
    we don't cross function bodies.
    """
    edges: list[PipelineEdge] = []
    scopes: list[ast.AST] = [tree]
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            scopes.append(node)

    for scope in scopes:
        body: list[ast.stmt] = getattr(scope, "body", [])
        if len(body) < 2:
            continue
        var_to_site: dict[str, str] = {}
        for stmt in body:
            # Track assignments whose RHS is (somewhere) a wrapper-fn call.
            for tgt_name, site_id in _wrapper_assignment_bindings(stmt, fn_to_site):
                var_to_site[tgt_name] = site_id
            # Find Calls in this stmt whose args reference a tracked var
            # AND the call itself is a wrapper-fn call → emit edge.
            for use in _wrapper_uses(stmt, fn_to_site, var_to_site):
                edges.append(use)
    return edges


def _wrapper_assignment_bindings(
    stmt: ast.stmt,
    fn_to_site: dict[str, str],
) -> list[tuple[str, str]]:
    """Yield (target_name, site_id) when *stmt* assigns the result of a
    wrapper-fn call to a variable. Walks the RHS so post-processing chains
    (``a = wrapper(x).strip()``) still bind correctly."""
    out: list[tuple[str, str]] = []
    value: ast.expr | None = None
    targets: list[ast.expr] = []

    if isinstance(stmt, ast.Assign):
        value = stmt.value
        targets = list(stmt.targets)
    elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
        value = stmt.value
        targets = [stmt.target]
    if value is None:
        return out

    site_id: str | None = None
    for node in ast.walk(value):
        if isinstance(node, ast.Call):
            name = _called_name(node)
            if name and name in fn_to_site:
                site_id = fn_to_site[name]
                break
    if site_id is None:
        return out
    for tgt in targets:
        if isinstance(tgt, ast.Name):
            out.append((tgt.id, site_id))
    return out


def _wrapper_uses(
    stmt: ast.stmt,
    fn_to_site: dict[str, str],
    var_to_site: dict[str, str],
) -> list[PipelineEdge]:
    """Find Calls in *stmt* that are wrapper-fn calls AND consume a tracked var."""
    edges: list[PipelineEdge] = []
    seen: set[tuple[str, str, str]] = set()
    for node in ast.walk(stmt):
        if not isinstance(node, ast.Call):
            continue
        name = _called_name(node)
        if name is None or name not in fn_to_site:
            continue
        target_site_id = fn_to_site[name]
        # Find tracked var references in this call's args (deeply nested).
        for arg in [*node.args, *(kw.value for kw in node.keywords)]:
            for inner in ast.walk(arg):
                if isinstance(inner, ast.Name) and inner.id in var_to_site:
                    source_id = var_to_site[inner.id]
                    if source_id == target_site_id:
                        continue
                    key = (source_id, target_site_id, inner.id)
                    if key in seen:
                        continue
                    seen.add(key)
                    edges.append(
                        PipelineEdge(
                            source=source_id,
                            target=target_site_id,
                            kind=EdgeKind.FUNCTION,
                            via=inner.id,
                            confidence=Confidence.HIGH,
                        )
                    )
    return edges


def _iter_functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    out: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            out.append(node)
    return out


def _function_returns_prompt(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    line_index: dict[int, PromptSite],
) -> PromptSite | None:
    """Identify functions whose last/primary return value comes from an LLM call.

    We look at every ``return`` statement in the function body — if any
    one returns a Call matching a known site, we treat the function as a
    wrapper around that call. Functions with multiple distinct LLM-call
    returns are still mapped to the first one we see (deterministic order).
    """
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Call):
            site = call_at_or_within(node.value, line_index)
            if site is not None:
                return site
        # Also catch `return <call>.choices[0].message.content` style — walk
        # into Attribute/Subscript chains to find the underlying Call.
        if isinstance(node, ast.Return) and node.value is not None:
            for inner in ast.walk(node.value):
                if isinstance(inner, ast.Call):
                    site = call_at_or_within(inner, line_index)
                    if site is not None:
                        return site
    return None


def _called_name(call: ast.Call) -> str | None:
    """Extract the simple name of the callee, if it's a Name (not an Attribute).

    We deliberately ignore method calls (``self.summarise(text)``) here —
    cross-class state-flow detection is v0.2 scope.
    """
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None
