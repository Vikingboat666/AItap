"""Tests for the intra-class method-chain detector (B1, wt/scanner-pipelines).

The cc-project (Pet Heaven) eval after the cross_file_orchestration
rule (PR #51) landed still showed zero pipelines for
``interaction_engine`` — a class that owns three or more LLM-bearing
sub-methods (``classify_intent`` / ``generate_response`` / ``validate``
…) sequenced by a top-level ``run_interaction`` method on the same
class.

PR #51's rule resolves ``self.<attr>.<method>(...)`` where ``<attr>``
points to a *different file's* class via ``__init__``; it can't see the
intra-class case because the receiver is the bare ``self`` (no
attribute hop, no cross-file resolution).
:class:`~aitap.scanner.dataflow.intra_file_chain.IntraFileChain` can't
see it either — its ``_called_name`` helper only matches bare-Name
callees, dropping ``self.method`` calls by design.

These tests stand the gap up against the interaction_engine shape and
the false-positive guards we care about: a two-step helper chain (must
*not* fire — below the ``MIN_DISTINCT_STEPS = 3`` threshold), an
orchestrator that calls only non-LLM helpers (must not fire), and an
orchestrator method that ``is`` an LLM-bearing leaf (must not
double-count its own site as a step).
"""

from __future__ import annotations

import ast
from pathlib import Path
from textwrap import dedent

import pytest

from aitap.scanner.dataflow import IntraClassMethodChain
from aitap.scanner.dataflow.intra_class_method_chain import _dedupe_keep_order
from aitap.scanner.engine import scan_project
from aitap.scanner.models import Confidence, EdgeKind


def _write(project_root: Path, relpath: str, source: str) -> Path:
    file_path = project_root / relpath
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(dedent(source), encoding="utf-8")
    return file_path


def _scan(project_root: Path):
    return scan_project(project_root)


def _pipeline_via(result, expected_substring: str) -> bool:
    for pipeline in result.pipelines:
        for edge in pipeline.edges:
            if edge.via and expected_substring in edge.via:
                return True
    return False


# --------------------------------------------------------------------------- #
# Module-level helper unit tests                                              #
# --------------------------------------------------------------------------- #


def test_dedupe_keep_order_preserves_first_seen() -> None:
    assert _dedupe_keep_order(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]


def test_dedupe_keep_order_collapses_adjacent_repeats() -> None:
    # The orchestrator-walks-AST pattern can produce adjacent
    # duplicates when an enclosing block adds the call once and the
    # inner ``ast.walk`` adds it again. The helper must keep the
    # de-duped, ordered shape so edge chaining works the same way the
    # cross-file rule produces it.
    assert _dedupe_keep_order(["a", "a", "a", "b"]) == ["a", "b"]


def test_dedupe_keep_order_handles_empty_input() -> None:
    assert _dedupe_keep_order([]) == []


# --------------------------------------------------------------------------- #
# Happy path — interaction-engine shape                                       #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    return tmp_path


def _seed_interaction_engine_shape(project_root: Path) -> None:
    """Three LLM-bearing methods composed by a fourth on the same class.

    The literal ``llm.complete`` body is enough to surface a PromptSite
    at the call line — same fixture shape the cross-file orchestration
    tests use.
    """
    _write(
        project_root,
        "app/interaction_engine.py",
        """
        class InteractionEngine:
            def __init__(self):
                self._llm = object()

            async def classify_intent(self, msg):
                return await self._llm.complete(
                    messages=[{"role": "user", "content": "Classify."}],
                )

            async def generate_response(self, intent):
                return await self._llm.complete(
                    messages=[{"role": "user", "content": "Respond."}],
                )

            async def validate(self, response):
                return await self._llm.complete(
                    messages=[{"role": "user", "content": "Validate."}],
                )

            async def run_interaction(self, msg):
                intent = await self.classify_intent(msg)
                response = await self.generate_response(intent)
                return await self.validate(response)
        """,
    )


def test_intra_class_method_chain_detects_interaction_engine_shape(
    project_root: Path,
) -> None:
    _seed_interaction_engine_shape(project_root)
    result = _scan(project_root)

    # Three PromptSites, one per leaf method.
    assert len(result.prompts) == 3
    assert all(s.location.file == "app/interaction_engine.py" for s in result.prompts)

    # Exactly one Pipeline whose edges carry the orchestrator's
    # qualified name.
    assert len(result.pipelines) == 1
    assert _pipeline_via(result, "InteractionEngine.run_interaction")

    pipeline = result.pipelines[0]
    assert len(pipeline.nodes) == 3
    assert {edge.kind for edge in pipeline.edges} == {EdgeKind.FUNCTION}
    # MEDIUM confidence — heuristic, mirrors cross_file_orchestration's
    # claim about syntactic-only resolution.
    assert all(edge.confidence is Confidence.MEDIUM for edge in pipeline.edges)


# --------------------------------------------------------------------------- #
# False-positive guards                                                       #
# --------------------------------------------------------------------------- #


def test_intra_class_method_chain_skips_two_step_helper_chains(
    project_root: Path,
) -> None:
    """A two-step ``self.method()`` chain must NOT fire — that's below
    the ``MIN_DISTINCT_STEPS = 3`` threshold and would overlap with the
    free-function helper chains the existing detectors handle.
    """
    _write(
        project_root,
        "app/two_step.py",
        """
        class TwoStep:
            def __init__(self):
                self._llm = object()

            async def step_a(self):
                return await self._llm.complete(
                    messages=[{"role": "user", "content": "A."}],
                )

            async def step_b(self):
                return await self._llm.complete(
                    messages=[{"role": "user", "content": "B."}],
                )

            async def orchestrate(self):
                a = await self.step_a()
                return await self.step_b()
        """,
    )
    result = _scan(project_root)
    # Sites still surface; no Pipeline from this detector.
    assert len(result.prompts) == 2
    assert not _pipeline_via(result, "TwoStep.orchestrate")


def test_intra_class_method_chain_skips_non_llm_method_orchestrator(
    project_root: Path,
) -> None:
    """When ``self.<method>`` resolves to non-LLM helpers the
    orchestrator must NOT emit edges — only LLM-bearing methods count.
    """
    _write(
        project_root,
        "app/mixed.py",
        """
        class MixedHelpers:
            def __init__(self):
                self._llm = object()

            async def llm_call_one(self):
                return await self._llm.complete(
                    messages=[{"role": "user", "content": "One."}],
                )

            async def llm_call_two(self):
                return await self._llm.complete(
                    messages=[{"role": "user", "content": "Two."}],
                )

            async def llm_call_three(self):
                return await self._llm.complete(
                    messages=[{"role": "user", "content": "Three."}],
                )

            def helper_a(self):  # no LLM call
                return 1

            def helper_b(self):  # no LLM call
                return 2

            def helper_c(self):  # no LLM call
                return 3

            def orchestrate_only_helpers(self):
                # Three self.<method>() calls, but none are LLM-bearing.
                self.helper_a()
                self.helper_b()
                self.helper_c()
        """,
    )
    result = _scan(project_root)
    # The non-LLM orchestrator does not produce a Pipeline.
    assert not _pipeline_via(result, "MixedHelpers.orchestrate_only_helpers")


def test_intra_class_method_chain_skips_orchestrator_that_is_a_leaf(
    project_root: Path,
) -> None:
    """An LLM-bearing method that also calls ``self.<other_llm_method>``
    can't double-count its own site as a step. The detector skips
    methods whose body has its own PromptSite (they're leaves, not
    orchestrators).
    """
    _write(
        project_root,
        "app/leaf_orchestrator.py",
        """
        class LeafOrchestrator:
            def __init__(self):
                self._llm = object()

            async def leaf_a(self):
                return await self._llm.complete(
                    messages=[{"role": "user", "content": "A."}],
                )

            async def leaf_b(self):
                return await self._llm.complete(
                    messages=[{"role": "user", "content": "B."}],
                )

            async def leaf_c(self):
                return await self._llm.complete(
                    messages=[{"role": "user", "content": "C."}],
                )

            # This method has its own LLM site AND calls three others —
            # it's a leaf, not an orchestrator. The detector must skip it.
            async def leaf_plus_orchestration(self):
                own = await self._llm.complete(
                    messages=[{"role": "user", "content": "Own."}],
                )
                await self.leaf_a()
                await self.leaf_b()
                await self.leaf_c()
                return own
        """,
    )
    result = _scan(project_root)
    # The leaf-plus-orchestration method does NOT produce a Pipeline.
    assert not _pipeline_via(result, "LeafOrchestrator.leaf_plus_orchestration")


def test_intra_class_method_chain_ignores_cross_attribute_calls(
    project_root: Path,
) -> None:
    """``self.<attr>.<method>(...)`` is cross_file_orchestration's
    territory; this detector must not fire on that shape and double
    up edges the other rule already emits.
    """
    # We use a class whose ``__init__`` assigns a self-attr to another
    # class in the same file — cross_file_orchestration won't fire
    # (same-file ⇒ no cross-file edge), so any Pipeline that surfaces
    # here is proof IntraClassMethodChain over-matched.
    _write(
        project_root,
        "app/cross_attr.py",
        """
        class Leaf:
            def __init__(self):
                self._llm = object()

            async def a(self):
                return await self._llm.complete(
                    messages=[{"role": "user", "content": "A."}],
                )

            async def b(self):
                return await self._llm.complete(
                    messages=[{"role": "user", "content": "B."}],
                )

            async def c(self):
                return await self._llm.complete(
                    messages=[{"role": "user", "content": "C."}],
                )

        class Orchestrator:
            def __init__(self):
                self._leaf = Leaf()

            async def run(self):
                # self.<attr>.<method>() — not self.<method>().
                await self._leaf.a()
                await self._leaf.b()
                await self._leaf.c()
        """,
    )
    result = _scan(project_root)
    # No IntraClassMethodChain Pipeline (orchestrator's calls are all
    # via ``self._leaf.*``, not ``self.*``).
    assert not _pipeline_via(result, "Orchestrator.run")


# --------------------------------------------------------------------------- #
# Detector-level unit test (no engine round-trip)                             #
# --------------------------------------------------------------------------- #


def test_detector_returns_empty_when_fewer_than_three_sites() -> None:
    """The per-file ≥3 site gate in ``detect`` is a fast-path that the
    orchestrator's own ≥2 gate doesn't cover."""
    detector = IntraClassMethodChain()
    tree = ast.parse("class X: pass")
    assert detector.detect(tree, [], Path("x.py")) == []
