"""Detect same-class method-call orchestration (B1, wt/scanner-pipelines).

Pattern: a class method whose body sequences ``self.<method>(...)`` /
``await self.<method>(...)`` calls where each ``<method>`` is itself an
LLM-bearing method of the *same* class (its body contains a known
:class:`PromptSite`). This is the "multi-turn engine" shape — a single
class owns multiple LLM calls split across sub-methods, and a top-level
method composes them in order::

    class InteractionEngine:
        async def classify_intent(self, msg):
            return await openai.chat.completions.create(...)  # PromptSite #1

        async def generate_response(self, intent):
            return await openai.chat.completions.create(...)  # PromptSite #2

        async def validate(self, response):
            return await openai.chat.completions.create(...)  # PromptSite #3

        async def run_interaction(self, msg):
            intent = await self.classify_intent(msg)
            response = await self.generate_response(intent)
            await self.validate(response)

The existing intra-file detectors miss this:

- :class:`~aitap.scanner.dataflow.intra_file_chain.IntraFileChain`
  ignores ``self.method()`` calls by design — its ``_called_name``
  helper only matches module-level ``ast.Name`` callees.
- :class:`~aitap.scanner.dataflow.cross_file_orchestration.CrossFileOrchestration`
  resolves ``self.<attr>.<method>(...)`` where ``<attr>`` is a
  receiver assigned in ``__init__`` to a *different file's* class. The
  intra-class case has neither the extra attribute hop nor the
  cross-file class lookup, so the rule never fires.

Scope notes
-----------

This is L1: every step is purely syntactic.

We deliberately *don't* handle:

- Inheritance — ``self.method`` resolution is single-class only. A
  method defined on a base class won't link if the orchestrator lives
  on the subclass (or vice versa).
- ``async def`` semantics beyond ``await``-prefixing — we treat
  ``self.method(...)`` and ``await self.method(...)`` interchangeably.
- ``self.method`` reached via attribute chains
  (``self.helper.method()`` — that's cross_file_orchestration's case
  once the receiver is class-attribute-assigned).
- Conditional branches — every call we see in source order is treated
  as part of the chain. A method body with two parallel ``if`` arms
  each making three LLM calls produces six steps, not two chains; the
  rule lacks the symbolic execution that would untangle them.

Confidence: :attr:`Confidence.MEDIUM`. The signal is strong (method
calls on ``self`` resolved against locally-defined LLM-bearing methods)
but the chain semantics rely on source-order traversal, which is a
heuristic when control flow branches.
"""

from __future__ import annotations

import ast
from itertools import pairwise
from pathlib import Path

from aitap.scanner.models import Confidence, EdgeKind, PipelineEdge, PromptSite

from .base import index_sites_by_line


class IntraClassMethodChain:
    """Detector for sequential ``self.<method>(...)`` LLM orchestration.

    Pluggable in :func:`aitap.scanner.dataflow.default_detectors` —
    runs per-file alongside the other intra-file detectors. The
    orchestrator's per-file ≥2-site gate already filters away files
    that can't possibly chain anything, so we don't re-check it here.
    """

    name = "intra_class_method_chain"

    # Minimum number of distinct LLM-bearing ``self.<method>(...)`` calls
    # an orchestrator method must sequence before we emit edges. Three
    # is the floor at which the "multi-turn engine" pattern becomes
    # visually distinct from a two-step helper chain
    # (which :class:`IntraFileChain` + :class:`VariableTracker` already
    # cover via free-function callees).
    MIN_DISTINCT_STEPS = 3

    def detect(
        self,
        tree: ast.Module,
        sites_in_file: list[PromptSite],
        file_path: Path,
    ) -> list[PipelineEdge]:
        del file_path  # not needed — site ids already encode the location.
        if len(sites_in_file) < self.MIN_DISTINCT_STEPS:
            return []

        line_index = index_sites_by_line(sites_in_file)
        edges: list[PipelineEdge] = []

        for class_node in ast.walk(tree):
            if not isinstance(class_node, ast.ClassDef):
                continue
            method_to_site = _llm_bearing_methods(class_node, line_index)
            if len(method_to_site) < self.MIN_DISTINCT_STEPS:
                continue
            for method in _class_methods(class_node):
                if method.name in method_to_site:
                    # An LLM-bearing leaf method can't *also* be the
                    # orchestrator — we'd double-count its own site as
                    # a step in the chain it leads. Skip.
                    continue
                steps = _collect_self_method_steps(method, method_to_site)
                distinct_steps = _dedupe_keep_order(steps)
                if len(distinct_steps) < self.MIN_DISTINCT_STEPS:
                    continue
                edges.extend(
                    _emit_edges_between_steps(
                        distinct_steps,
                        method_to_site=method_to_site,
                        orchestrator_label=f"{class_node.name}.{method.name}",
                    )
                )
        return edges


def _class_methods(
    class_node: ast.ClassDef,
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Return direct method definitions (skipping nested classes / non-callable
    statements).

    Methods defined inside an inner class wouldn't be reachable via
    ``self.<name>(...)`` on the outer instance, so we stop at the first
    nesting level.
    """
    out: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for body_item in class_node.body:
        if isinstance(body_item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(body_item)
    return out


def _llm_bearing_methods(
    class_node: ast.ClassDef,
    line_index: dict[int, PromptSite],
) -> dict[str, str]:
    """Return ``{method_name: anchor_site_id}`` for class methods whose body
    contains at least one known :class:`PromptSite`.

    The "anchor" is the first PromptSite the method body encounters in
    AST-walk order — same convention the other detectors use so edges
    line up when multiple detectors emit the same link.
    """
    out: dict[str, str] = {}
    for method in _class_methods(class_node):
        anchor = _first_site_in_method(method, line_index)
        if anchor is not None:
            out[method.name] = anchor.id
    return out


def _first_site_in_method(
    method: ast.FunctionDef | ast.AsyncFunctionDef,
    line_index: dict[int, PromptSite],
) -> PromptSite | None:
    """Walk *method* in AST order; return the first known PromptSite Call."""
    for node in ast.walk(method):
        if not isinstance(node, ast.Call):
            continue
        lineno = getattr(node, "lineno", None)
        if lineno is None:
            continue
        site = line_index.get(lineno)
        if site is not None:
            return site
    return None


def _collect_self_method_steps(
    method: ast.FunctionDef | ast.AsyncFunctionDef,
    method_to_site: dict[str, str],
) -> list[str]:
    """Return method names called via ``self.<name>(...)`` inside *method*,
    in source order, restricted to LLM-bearing methods.

    We descend the whole AST (including ``async with``, ``for``, ``try``
    bodies) so a chain that spans control-flow blocks still surfaces.
    The caller dedupes adjacent repeats — same convention the cross-file
    orchestration rule uses.
    """
    steps: list[str] = []
    for node in ast.walk(method):
        if not isinstance(node, ast.Call):
            continue
        method_name = _self_dot_method_receiver(node)
        if method_name is None:
            continue
        if method_name not in method_to_site:
            continue
        steps.append(method_name)
    return steps


def _self_dot_method_receiver(call: ast.Call) -> str | None:
    """Match ``self.<method>(...)`` → ``"<method>"``.

    We intentionally do NOT match ``self.<attr>.<method>(...)`` —
    that's :class:`~aitap.scanner.dataflow.cross_file_orchestration.CrossFileOrchestration`'s
    pattern. The receiver here must be the bare ``self`` Name, one
    attribute access away from the call.
    """
    func = call.func
    if not isinstance(func, ast.Attribute):
        return None
    receiver = func.value
    if not isinstance(receiver, ast.Name):
        return None
    if receiver.id != "self":
        return None
    return func.attr


def _dedupe_keep_order(steps: list[str]) -> list[str]:
    """Return *steps* with duplicates removed, preserving first-seen order.

    Matches ``cross_file_orchestration._dedupe_keep_order``'s
    set-based semantics so ``["a", "b", "a", "c", "b"]`` collapses to
    ``["a", "b", "c"]`` — a re-entrant call ("classify, generate,
    classify again to refine, validate") is treated as one node per
    distinct method, chained in first-seen order.
    """
    seen: set[str] = set()
    out: list[str] = []
    for name in steps:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _emit_edges_between_steps(
    distinct_steps: list[str],
    *,
    method_to_site: dict[str, str],
    orchestrator_label: str,
) -> list[PipelineEdge]:
    """Chain edges between consecutive distinct steps.

    Each step resolves to its method's anchor PromptSite; we emit
    ``EdgeKind.FUNCTION`` edges with the orchestrator's qualified name
    on the ``via`` field so the UI can explain where the link came
    from (mirroring the cross-file rule's ``via`` shape).
    """
    edges: list[PipelineEdge] = []
    for src_name, tgt_name in pairwise(distinct_steps):
        src_id = method_to_site[src_name]
        tgt_id = method_to_site[tgt_name]
        if src_id == tgt_id:
            # Two distinct method names that happen to anchor on the
            # same site (rare: two methods sharing the same line, e.g.
            # one-liners stacked on the same physical line). Skip the
            # degenerate self-loop instead of emitting an invalid
            # ``source == target`` edge.
            continue
        edges.append(
            PipelineEdge(
                source=src_id,
                target=tgt_id,
                kind=EdgeKind.FUNCTION,
                via=orchestrator_label,
                confidence=Confidence.MEDIUM,
            )
        )
    return edges


__all__ = ["IntraClassMethodChain"]
