"""Cross-file orchestration detector.

Sees a function whose body sequences ``self.<attr>.<method>(...)`` calls
where ``self.<attr>`` resolves through ``__init__`` to a class defined
elsewhere in the project, and the file holding that class contains at
least one prompt site. The existing intra-file detectors require ≥ 2
prompt sites in the same file before they look at it — that assumption
holds for LangChain-pipe / LlamaIndex-engine projects but misses the
common production layout where an orchestrator file (no LLM call of its
own) sequences single-LLM-call agents living in separate modules.

The Pet Heaven (cc-project) eval is the motivating example::

    # backend/app/simulation/daily_runner.py
    class DailyRunner:
        def __init__(self):
            self._planner = Planner()           # planner.py: 1 wrapper site
            self._engine = InteractionEngine()  # interaction_engine.py: 1 wrapper site
            self._reflector = ReflectionEngine()# reflection_engine.py: 1 wrapper site
            self._digest_gen = DigestGenerator()# digest_generator.py: 1 wrapper site
            ...

        async def run(self, sim_date, session_factory):
            plan = await self._planner.plan_day(pet, ...)
            scene = await self._engine.run_interaction(pair, ...)
            await self._reflector.reflect(pet, ...)
            await self._digest_gen.generate(pet, ...)

The orchestrator file itself holds zero LLM sites — every per-file
detector skips it. This rule looks at the project as a whole, resolves
the ``self._planner`` / ``self._engine`` / etc. attributes back to their
defining files via ``__init__`` + module-level imports, and emits
:class:`PipelineEdge` instances chaining the sites in those defining
files in the order they appear inside the orchestrator's body.

Honest scope notes
------------------

This is L1: every step is purely syntactic — no symbolic execution, no
runtime trace, no cross-file dataflow beyond the single
``self.<attr> = <Class>()`` indirection through ``__init__``.

We deliberately *don't* handle:

- ``self.x = factory_function()`` — we only see ``<Class>()`` literal.
- Aliased imports (``from foo import Bar as Baz``) — they work in
  principle (the alias becomes the attribute target) but the alias is
  what the receiver name resolves to; the rule's accuracy degrades
  when the same class is aliased in multiple files. Documented in the
  follow-up section of the design doc.
- Class-name collisions across modules. ``class_to_file`` is built
  first-seen-wins; a future ``wt/scanner-class-resolution`` worktree
  can do proper module-path resolution.
- Receivers that aren't ``self.<attr>`` (module-level ``llm.invoke``,
  free variables, dynamically-stored attributes). Those need either
  the wrapper-call rule (which already catches them at the call site
  if they qualify) or local-scope binding tracking.

Confidence is :attr:`Confidence.MEDIUM` because the class-attribute
indirection is a heuristic — a maintainer should glance at the
orchestrator before treating the inferred pipeline as ground truth.
"""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

from aitap.scanner.models import (
    Confidence,
    EdgeKind,
    PipelineEdge,
    PromptSite,
)

from .base import dedupe_keep_order as _dedupe_keep_order


@dataclass(frozen=True)
class _CrossFileContext:
    """Pre-computed project-wide views the per-file pass needs."""

    # Global ``ClassName -> file_relpath`` (first definition wins).
    class_to_file: dict[str, str]
    # ``file_relpath -> True`` iff the file contains at least one prompt site.
    llm_bearing_files: frozenset[str]
    # ``file_relpath -> [PromptSite]`` so we can pick an anchor site per file
    # when emitting an edge.
    sites_by_file: dict[str, list[PromptSite]]


class CrossFileOrchestration:
    """Detect orchestration functions whose steps span multiple files.

    See module docstring for the motivating shape; this class is
    intentionally light — every interesting decision lives in a small
    method and is unit-testable.
    """

    name = "cross_file_orchestration"

    # Minimum number of distinct cross-file LLM-bearing receivers in an
    # orchestrator's body. Two is the floor at which we'd actually emit
    # an edge; below that there's nothing to chain.
    MIN_DISTINCT_RECEIVERS = 2

    def detect(
        self,
        files: Iterable[Path],
        project_root: Path,
        sites: list[PromptSite],
    ) -> list[PipelineEdge]:
        """Return the cross-file edges this detector finds."""
        files_list = list(files)
        context = self._build_context(files_list, project_root, sites)
        all_edges: list[PipelineEdge] = []

        for file_path in files_list:
            try:
                source = file_path.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(source, filename=str(file_path))
            except (OSError, SyntaxError):
                continue

            rel = _relative(file_path, project_root)
            file_imports = self._collect_local_class_imports(tree)
            all_edges.extend(
                self._scan_file_for_orchestration_edges(
                    tree,
                    file_relpath=rel,
                    file_imports=file_imports,
                    context=context,
                )
            )

        return all_edges

    # ------------------------------------------------------------------
    # Context building
    # ------------------------------------------------------------------

    def _build_context(
        self,
        files: list[Path],
        project_root: Path,
        sites: list[PromptSite],
    ) -> _CrossFileContext:
        class_to_file: dict[str, str] = {}
        for file_path in files:
            try:
                tree = ast.parse(file_path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, SyntaxError):
                continue
            rel = _relative(file_path, project_root)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name not in class_to_file:
                    class_to_file[node.name] = rel

        sites_by_file: dict[str, list[PromptSite]] = defaultdict(list)
        for site in sites:
            sites_by_file[site.location.file].append(site)
        # Keep deterministic order per file so the "first site" anchor we
        # emit edges between stays stable across scans.
        for file_sites in sites_by_file.values():
            file_sites.sort(key=lambda s: (s.location.line_start, s.location.col_start or 0))

        llm_bearing_files = frozenset(sites_by_file.keys())
        return _CrossFileContext(
            class_to_file=class_to_file,
            llm_bearing_files=llm_bearing_files,
            sites_by_file=dict(sites_by_file),
        )

    # ------------------------------------------------------------------
    # Per-file detection
    # ------------------------------------------------------------------

    def _scan_file_for_orchestration_edges(
        self,
        tree: ast.Module,
        *,
        file_relpath: str,
        file_imports: dict[str, str],
        context: _CrossFileContext,
    ) -> list[PipelineEdge]:
        """Walk classes in *tree*, treat each method as a possible
        orchestrator, and emit edges between LLM-bearing receivers it
        sequences in source order.
        """
        edges: list[PipelineEdge] = []
        for class_node in ast.walk(tree):
            if not isinstance(class_node, ast.ClassDef):
                continue

            attr_target_files = self._extract_self_attr_target_files(
                class_node,
                file_imports=file_imports,
                context=context,
            )
            if not attr_target_files:
                continue

            for method in class_node.body:
                if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                steps = self._collect_orchestration_steps(method, attr_target_files)
                # Distinct receivers in source order. We chain edges
                # between consecutive distinct receivers — a method that
                # calls ``planner`` twice in a row then ``engine`` once
                # produces a single planner → engine edge, not two.
                distinct_receivers = _dedupe_keep_order(steps)
                if len(distinct_receivers) < self.MIN_DISTINCT_RECEIVERS:
                    continue
                edges.extend(
                    self._emit_edges_between_receiver_files(
                        distinct_receivers,
                        context=context,
                        orchestrator_file=file_relpath,
                        orchestrator_label=f"{class_node.name}.{method.name}",
                    )
                )
        return edges

    # ------------------------------------------------------------------
    # ``__init__`` attribute resolution
    # ------------------------------------------------------------------

    def _extract_self_attr_target_files(
        self,
        class_node: ast.ClassDef,
        *,
        file_imports: dict[str, str],
        context: _CrossFileContext,
    ) -> dict[str, str]:
        """For *class_node*, return ``{attr: target_file}``.

        ``target_file`` is the relative path of the file defining the
        class assigned to ``self.<attr>``. Returns empty dict when the
        class has no resolvable assignments.
        """
        out: dict[str, str] = {}
        init_method = self._find_init(class_node)
        if init_method is None:
            return out
        for stmt in init_method.body:
            self._record_attr_target(stmt, file_imports, context, out)
        return out

    def _find_init(self, class_node: ast.ClassDef) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        for body_item in class_node.body:
            if (
                isinstance(body_item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and body_item.name == "__init__"
            ):
                return body_item
        return None

    def _record_attr_target(
        self,
        stmt: ast.stmt,
        file_imports: dict[str, str],
        context: _CrossFileContext,
        out: dict[str, str],
    ) -> None:
        """Update *out* from a single statement in ``__init__``.

        Recognises plain ``self.x = ClassName()`` and annotated
        ``self.x: ClassName = ClassName()``. Returns silently otherwise.
        """
        if isinstance(stmt, ast.Assign):
            targets = stmt.targets
            value = stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            targets = [stmt.target]
            value = stmt.value
        else:
            return

        class_name = self._class_name_from_constructor_call(value)
        if class_name is None:
            return

        target_file = self._resolve_class_name_to_file(class_name, file_imports, context)
        if target_file is None:
            return

        for target in targets:
            attr_name = self._self_attr_name(target)
            if attr_name is not None:
                out[attr_name] = target_file

    def _class_name_from_constructor_call(self, node: ast.AST) -> str | None:
        """``ClassName()`` or ``module.ClassName()`` → ``"ClassName"``."""
        if not isinstance(node, ast.Call):
            return None
        func = node.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return None

    def _self_attr_name(self, target: ast.expr) -> str | None:
        """``self.x = …`` → ``"x"``; anything else → None."""
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        ):
            return target.attr
        return None

    def _resolve_class_name_to_file(
        self,
        class_name: str,
        file_imports: dict[str, str],
        context: _CrossFileContext,
    ) -> str | None:
        """Look up which project file defines *class_name*.

        Two paths:

        1. The class is imported in this file via ``from <module> import
           <Class>``. We honour the alias if there's one
           (``... as Alias``), so ``Alias`` in the call site resolves
           to the original ``Class`` name and then to the file. The
           ``file_imports`` map records both directions.

        2. The class is defined locally. We don't emit a cross-file
           edge to *the same file* — that's intra-file dataflow's job.
        """
        canonical = file_imports.get(class_name, class_name)
        target_file = context.class_to_file.get(canonical)
        return target_file

    # ------------------------------------------------------------------
    # Imports
    # ------------------------------------------------------------------

    def _collect_local_class_imports(self, tree: ast.Module) -> dict[str, str]:
        """Return ``{local_name: original_class_name}`` for every
        ``from <module> import <Class> [as <Alias>]`` in the file.

        ``import foo`` and ``import foo as bar`` aren't recorded — we
        only care about names that could be the RHS of ``self.x =
        ClassName()``.
        """
        out: dict[str, str] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                out[local] = alias.name
        return out

    # ------------------------------------------------------------------
    # Step collection inside a method body
    # ------------------------------------------------------------------

    def _collect_orchestration_steps(
        self,
        method: ast.FunctionDef | ast.AsyncFunctionDef,
        attr_target_files: dict[str, str],
    ) -> list[str]:
        """Return target files in source order for every
        ``self.<attr>.<method>(...)`` call whose ``<attr>`` is in
        *attr_target_files*. The caller dedupes.
        """
        steps: list[str] = []
        for node in ast.walk(method):
            if not isinstance(node, ast.Call):
                continue
            attr_name = self._self_dot_attr_method_receiver(node)
            if attr_name is None:
                continue
            target = attr_target_files.get(attr_name)
            if target is None:
                continue
            steps.append(target)
        return steps

    def _self_dot_attr_method_receiver(self, call: ast.Call) -> str | None:
        """Match ``self.<attr>.<method>(...)`` → ``"<attr>"``.

        Two-level chain is the canonical agent-call shape; we don't
        match deeper chains like ``self.x.y.method()`` because they
        require multi-step attribute resolution that's a separate
        feature.
        """
        func = call.func
        if not isinstance(func, ast.Attribute):
            return None
        receiver = func.value
        if not isinstance(receiver, ast.Attribute):
            return None
        if not isinstance(receiver.value, ast.Name):
            return None
        if receiver.value.id != "self":
            return None
        return receiver.attr

    # ------------------------------------------------------------------
    # Edge emission
    # ------------------------------------------------------------------

    def _emit_edges_between_receiver_files(
        self,
        distinct_receiver_files: list[str],
        *,
        context: _CrossFileContext,
        orchestrator_file: str,
        orchestrator_label: str,
    ) -> list[PipelineEdge]:
        """Chain edges between consecutive receiver files.

        Each receiver file becomes a node in the pipeline; we pick the
        first prompt site in each file as the anchor for that node. The
        ``via`` field carries the orchestrator's qualified name
        (``"app/runner.py::DailyRunner.run"``) so a downstream UI can
        explain where the edge came from.
        """
        edges: list[PipelineEdge] = []
        anchors: list[PromptSite] = []
        for rel in distinct_receiver_files:
            file_sites = context.sites_by_file.get(rel, [])
            if not file_sites:
                continue
            anchors.append(file_sites[0])

        if len(anchors) < self.MIN_DISTINCT_RECEIVERS:
            return edges

        via = f"{orchestrator_file}::{orchestrator_label}"
        for src, tgt in pairwise(anchors):
            edges.append(
                PipelineEdge(
                    source=src.id,
                    target=tgt.id,
                    kind=EdgeKind.FUNCTION,
                    via=via,
                    confidence=Confidence.MEDIUM,
                )
            )
        return edges


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ``_dedupe_keep_order`` moved to :mod:`aitap.scanner.dataflow.base`
# (B1, wt/scanner-pipelines) and is now shared with
# :class:`~aitap.scanner.dataflow.intra_class_method_chain.IntraClassMethodChain`
# so the two detectors can't drift on collapse semantics. The import
# above re-exports it under the legacy ``_dedupe_keep_order`` name so
# the existing tests in
# ``tests/unit/test_dataflow_cross_file_orchestration.py`` keep
# importing it from this module unchanged.


def _relative(file_path: Path, project_root: Path) -> str:
    try:
        return file_path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return file_path.as_posix()
