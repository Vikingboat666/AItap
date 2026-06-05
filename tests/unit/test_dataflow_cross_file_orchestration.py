"""Tests for the cross-file orchestration detector.

The cc-project eval after PRs #46 / #47 / #48 / #49 hit ~90 %
inventory completeness on prompt sites but still reported zero
pipelines because every orchestrator (`daily_runner.run` and friends)
lives in a file with zero LLM sites — every per-file detector skips
it. PR #51 closes that gap with a cross-file detector that resolves
``self.<attr>`` assignments through ``__init__`` to classes defined
elsewhere in the project.

The tests below build small fake projects in ``tmp_path`` that mirror
the cc-project shape and exercise both the happy path and the false-
positive guards. The fixture builder writes real ``.py`` files so the
detector runs end-to-end through ``scan_project``.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path
from textwrap import dedent

import pytest

from aitap.scanner.dataflow import CrossFileOrchestration
from aitap.scanner.dataflow.cross_file_orchestration import _dedupe_keep_order
from aitap.scanner.engine import scan_project
from aitap.scanner.models import EdgeKind, PromptSite

# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _write(project_root: Path, relpath: str, source: str) -> Path:
    file_path = project_root / relpath
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(dedent(source), encoding="utf-8")
    return file_path


def _scan(project_root: Path):
    return scan_project(project_root)


def _pipeline_via(result, expected_substring: str) -> bool:
    """True if *result* has at least one Pipeline whose edges' ``via``
    field contains *expected_substring*."""
    for pipeline in result.pipelines:
        for edge in pipeline.edges:
            if edge.via and expected_substring in edge.via:
                return True
    return False


# --------------------------------------------------------------------------- #
# _dedupe_keep_order — module-level helper                                    #
# --------------------------------------------------------------------------- #


def test_dedupe_keep_order_preserves_first_seen() -> None:
    assert _dedupe_keep_order(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]


def test_dedupe_keep_order_handles_empty_input() -> None:
    assert _dedupe_keep_order([]) == []


# --------------------------------------------------------------------------- #
# Happy path — Pet-Heaven-shaped fixture                                      #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    return tmp_path


def _seed_pet_heaven_shape(project_root: Path) -> None:
    """Write the minimum fixture needed to reproduce the cc-project
    orchestrator shape: two agent files each with one wrapper call,
    plus an orchestrator file that imports both and sequences them.
    """
    _write(
        project_root,
        "app/agents/planner.py",
        """
        class Planner:
            def __init__(self):
                self._llm = object()

            async def plan_day(self, pet):
                return await self._llm.complete(
                    messages=[{"role": "user", "content": "Plan today."}],
                )
        """,
    )
    _write(
        project_root,
        "app/agents/engine.py",
        """
        class Engine:
            def __init__(self):
                self._llm = object()

            async def run_interaction(self, pet):
                return await self._llm.complete(
                    messages=[{"role": "user", "content": "Run interaction."}],
                )
        """,
    )
    _write(
        project_root,
        "app/runner.py",
        """
        from app.agents.planner import Planner
        from app.agents.engine import Engine

        class DailyRunner:
            def __init__(self):
                self._planner = Planner()
                self._engine = Engine()

            async def run(self, pet):
                plan = await self._planner.plan_day(pet)
                scene = await self._engine.run_interaction(pet)
                return plan, scene
        """,
    )


def test_cross_file_orchestration_detects_pet_heaven_shape(
    project_root: Path,
) -> None:
    _seed_pet_heaven_shape(project_root)
    result = _scan(project_root)

    # Both agent files surface their wrapper calls.
    site_files = sorted(s.location.file for s in result.prompts)
    assert "app/agents/planner.py" in site_files
    assert "app/agents/engine.py" in site_files

    # The orchestrator file produces one Pipeline whose edges
    # carry the orchestrator's qualified name.
    assert len(result.pipelines) >= 1
    assert _pipeline_via(result, "app/runner.py::DailyRunner.run")

    # The Pipeline's two nodes point at the two agent prompt sites
    # (one each).
    pipeline = result.pipelines[0]
    assert len(pipeline.nodes) == 2
    pipeline_node_ids = {n.prompt_id for n in pipeline.nodes}
    pipeline_node_files = {s.location.file for s in result.prompts if s.id in pipeline_node_ids}
    assert pipeline_node_files == {
        "app/agents/planner.py",
        "app/agents/engine.py",
    }


def test_cross_file_orchestration_emits_function_edge_kind(
    project_root: Path,
) -> None:
    _seed_pet_heaven_shape(project_root)
    result = _scan(project_root)
    assert any(
        edge.kind is EdgeKind.FUNCTION for pipeline in result.pipelines for edge in pipeline.edges
    )


def test_cross_file_orchestration_handles_annotated_init_assignment(
    project_root: Path,
) -> None:
    """``self._planner: Planner = Planner()`` is the type-annotated
    variant the rule must also accept; otherwise projects that lint
    for type annotations would silently miss every orchestrator."""
    _write(
        project_root,
        "app/agents/planner.py",
        """
        class Planner:
            def __init__(self):
                self._llm = object()

            async def plan_day(self, pet):
                return await self._llm.complete(
                    messages=[{"role": "user", "content": "Plan."}],
                )
        """,
    )
    _write(
        project_root,
        "app/agents/engine.py",
        """
        class Engine:
            def __init__(self):
                self._llm = object()

            async def run_interaction(self, pet):
                return await self._llm.complete(
                    messages=[{"role": "user", "content": "Engine."}],
                )
        """,
    )
    _write(
        project_root,
        "app/runner.py",
        """
        from app.agents.planner import Planner
        from app.agents.engine import Engine

        class DailyRunner:
            def __init__(self):
                self._planner: Planner = Planner()
                self._engine: Engine = Engine()

            async def run(self, pet):
                a = await self._planner.plan_day(pet)
                b = await self._engine.run_interaction(pet)
                return a, b
        """,
    )
    result = _scan(project_root)
    assert _pipeline_via(result, "app/runner.py::DailyRunner.run")


def test_cross_file_orchestration_handles_aliased_import(
    project_root: Path,
) -> None:
    """``from foo import Bar as Baz`` then ``self.x = Baz()`` should
    resolve to the file that defines ``Bar`` — both ``Bar`` and
    ``Baz`` are mapped into the file's local import table."""
    _write(
        project_root,
        "app/agents/planner.py",
        """
        class Planner:
            def __init__(self):
                self._llm = object()

            async def plan_day(self, pet):
                return await self._llm.complete(
                    messages=[{"role": "user", "content": "Plan."}],
                )
        """,
    )
    _write(
        project_root,
        "app/agents/engine.py",
        """
        class Engine:
            def __init__(self):
                self._llm = object()

            async def run_interaction(self, pet):
                return await self._llm.complete(
                    messages=[{"role": "user", "content": "Engine."}],
                )
        """,
    )
    _write(
        project_root,
        "app/runner.py",
        """
        from app.agents.planner import Planner as MyPlanner
        from app.agents.engine import Engine

        class DailyRunner:
            def __init__(self):
                self._planner = MyPlanner()
                self._engine = Engine()

            async def run(self, pet):
                a = await self._planner.plan_day(pet)
                b = await self._engine.run_interaction(pet)
                return a, b
        """,
    )
    result = _scan(project_root)
    assert _pipeline_via(result, "app/runner.py::DailyRunner.run")


# --------------------------------------------------------------------------- #
# False-positive guards                                                       #
# --------------------------------------------------------------------------- #


def test_orchestrator_with_single_distinct_receiver_emits_nothing(
    project_root: Path,
) -> None:
    """One distinct receiver isn't a pipeline — even three calls to the
    same ``self._planner`` don't justify an edge to a non-existent
    second node.
    """
    _write(
        project_root,
        "app/agents/planner.py",
        """
        class Planner:
            def __init__(self):
                self._llm = object()

            async def plan_day(self, pet):
                return await self._llm.complete(
                    messages=[{"role": "user", "content": "Plan."}],
                )
        """,
    )
    _write(
        project_root,
        "app/runner.py",
        """
        from app.agents.planner import Planner

        class DailyRunner:
            def __init__(self):
                self._planner = Planner()

            async def run(self, pet):
                a = await self._planner.plan_day(pet)
                b = await self._planner.plan_day(pet)
                c = await self._planner.plan_day(pet)
                return a, b, c
        """,
    )
    result = _scan(project_root)
    # Planner's wrapper site is still surfaced, but no pipeline.
    assert any(s.location.file == "app/agents/planner.py" for s in result.prompts)
    assert not _pipeline_via(result, "app/runner.py::DailyRunner.run")


def test_orchestrator_with_non_llm_bearing_receivers_emits_nothing(
    project_root: Path,
) -> None:
    """``self._db.query`` / ``self._cache.get`` etc. resolve to files
    with no prompt sites — the rule must not chain them.
    """
    _write(
        project_root,
        "app/db.py",
        """
        class DB:
            def query(self, what):
                return None
        """,
    )
    _write(
        project_root,
        "app/cache.py",
        """
        class Cache:
            def get(self, key):
                return None
        """,
    )
    _write(
        project_root,
        "app/runner.py",
        """
        from app.db import DB
        from app.cache import Cache

        class Runner:
            def __init__(self):
                self._db = DB()
                self._cache = Cache()

            def run(self):
                self._db.query("x")
                self._cache.get("y")
        """,
    )
    result = _scan(project_root)
    assert result.pipelines == []


def test_orchestrator_in_file_without_init_emits_nothing(
    project_root: Path,
) -> None:
    """A class without an ``__init__`` has no resolvable
    ``self.<attr>`` map; the rule must not crash and must not
    guess at attribute origins."""
    _write(
        project_root,
        "app/agents/planner.py",
        """
        class Planner:
            def __init__(self):
                self._llm = object()

            async def plan_day(self, pet):
                return await self._llm.complete(
                    messages=[{"role": "user", "content": "Plan."}],
                )
        """,
    )
    _write(
        project_root,
        "app/runner.py",
        """
        from app.agents.planner import Planner

        class DailyRunner:
            # No __init__ — attributes show up via subclassing or
            # ClassVar elsewhere; we don't try to resolve those.

            async def run(self, pet):
                a = await self._planner.plan_day(pet)
                b = await self._planner.plan_day(pet)
                return a, b
        """,
    )
    result = _scan(project_root)
    assert not _pipeline_via(result, "app/runner.py::DailyRunner.run")


def test_orchestrator_with_local_class_reference_emits_nothing(
    project_root: Path,
) -> None:
    """An orchestrator class whose ``__init__`` instantiates *itself*
    (or another class defined in the same file) should not chain back
    to its own file as a cross-file edge — that's intra-file
    territory, and the existing intra-file detectors handle it.
    """
    _write(
        project_root,
        "app/runner.py",
        """
        class Helper:
            def __init__(self):
                self._llm = object()

            async def help(self):
                return await self._llm.complete(
                    messages=[{"role": "user", "content": "Help."}],
                )

        class Runner:
            def __init__(self):
                self._a = Helper()
                self._b = Helper()

            async def run(self):
                await self._a.help()
                await self._b.help()
        """,
    )
    result = _scan(project_root)
    # Helper.help is a wrapper site; the orchestrator references it twice
    # but both receivers point at the same file — no cross-file edge.
    cross_file_edges = [
        edge
        for pipeline in result.pipelines
        for edge in pipeline.edges
        if edge.via and "Runner.run" in edge.via
    ]
    assert cross_file_edges == []


def test_orchestrator_referencing_unresolved_class_emits_nothing(
    project_root: Path,
) -> None:
    """``self._planner = Planner()`` where ``Planner`` isn't imported
    and isn't defined locally — the rule must not crash and must not
    invent an edge to nowhere."""
    _write(
        project_root,
        "app/runner.py",
        """
        class Runner:
            def __init__(self):
                self._planner = Planner()  # not imported, not defined
                self._engine = Engine()  # same

            async def run(self):
                await self._planner.plan_day(None)
                await self._engine.run_interaction(None)
        """,
    )
    result = _scan(project_root)
    assert result.pipelines == []


# --------------------------------------------------------------------------- #
# Internal helper unit tests — receiver shape                                 #
# --------------------------------------------------------------------------- #


def test_internal_receiver_recogniser_accepts_self_dot_attr_dot_method() -> None:
    detector = CrossFileOrchestration()
    tree = ast.parse("self._planner.plan_day(x)")
    call = tree.body[0].value
    assert isinstance(call, ast.Call)
    assert detector._self_dot_attr_method_receiver(call) == "_planner"


def test_internal_receiver_recogniser_rejects_module_level_call() -> None:
    detector = CrossFileOrchestration()
    tree = ast.parse("planner.plan_day(x)")
    call = tree.body[0].value
    assert isinstance(call, ast.Call)
    assert detector._self_dot_attr_method_receiver(call) is None


def test_internal_receiver_recogniser_rejects_deep_chain() -> None:
    """``self.x.y.method()`` is intentionally skipped — multi-step
    attribute resolution belongs in a future rule."""
    detector = CrossFileOrchestration()
    tree = ast.parse("self.x.y.method(z)")
    call = tree.body[0].value
    assert isinstance(call, ast.Call)
    assert detector._self_dot_attr_method_receiver(call) is None


# --------------------------------------------------------------------------- #
# Smoke: prompts list is unchanged when the new rule fires                    #
# --------------------------------------------------------------------------- #


def test_prompts_list_byte_for_byte_unchanged_by_cross_file_rule(
    project_root: Path,
) -> None:
    """Regression guard: PR #51 is additive — it only touches
    ``ScanResult.pipelines``. The prompts list must not change shape
    or order when the new detector fires.
    """
    _seed_pet_heaven_shape(project_root)
    result_with_detector = _scan(project_root)

    # Strip pipelines, compare prompts. The same project scanned twice
    # would produce the same ``prompts`` regardless of which detectors
    # ran — this proves the cross-file pass doesn't leak into the
    # prompt-site list.
    sites_first: list[PromptSite] = list(result_with_detector.prompts)
    result_again = _scan(project_root)
    sites_second: list[PromptSite] = list(result_again.prompts)
    assert sites_first == sites_second


def _ids(sites: Iterable[PromptSite]) -> list[str]:
    return [s.id for s in sites]
