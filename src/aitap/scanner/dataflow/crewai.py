"""CrewAI multi-agent topology detector (B2-CrewAI, wt/scanner-crewai).

Reads the three primitives a CrewAI project exposes:

    researcher = Agent(role="Researcher", goal="…", backstory="…")
    write_task = Task(
        description="Write blog post",
        expected_output="markdown",
        agent=writer,
        context=[research_task],
    )
    crew = Crew(
        agents=[researcher, writer, …],
        tasks=[research_task, write_task, …],
        process=Process.sequential,  # or Process.hierarchical
    )

CrewAI has no ``add_edge``-equivalent. Dependencies live in
``Task(context=[other_task])`` (explicit, HIGH confidence) and in
``Crew(tasks=[a, b, c], process=Process.sequential)`` ordering
(implicit, MEDIUM confidence). ``Process.hierarchical`` doesn't have a
static shape — the manager agent routes work at runtime — so we emit
a LOW-confidence fan-out from the first task to all others.

Tasks emit as :attr:`PipelineNode.kind` ``= "declared_llm"``: real LLM
calls (CrewAI's runtime makes the agent execute the task description
as a prompt) whose prompt text lives inside the framework, not in
scannable user code. The playground runner rejects them the same way
it rejects ``non_llm`` nodes — CrewAI's runtime owns execution and
aitap can't currently drive it (queued as a follow-up worktree
``wt/scanner-crewai-runner``).

Scope (L1, syntactic):

- Recognised APIs: ``Agent`` (for label resolution), ``Task``
  (description / agent / context fields), ``Crew`` (tasks / process /
  manager_agent fields). ``Process.sequential`` and
  ``Process.hierarchical`` distinguish edge confidence.
- Aliased imports honoured (``from crewai import Crew as C``).
- Static ``context=[t1, t2]`` lists only; variable references skipped
  (we don't fabricate connections we can't see).

Deliberately out of scope (queued as v2):

- ``@CrewBase`` / ``@agent`` / ``@task`` decorator-driven YAML
  configurations — the entry-point is the decorator, not an ``Agent(...)``
  call literal. Needs separate handling.
- Tools wired via ``Agent(tools=[…])``.
- Cross-file Task references where the task is imported from another
  module.
- ``async_execution=True`` parallel branches.

Adjacent OSS context: ``agentic-radar`` (979★) does the same shape
(Agent/Task/Crew collection) but doesn't classify nodes by
LLM-bearing kind the way aitap now does. We borrow the per-file scan
strategy, then go further by emitting Pipeline objects with
``kind='declared_llm'`` so the UI can render CrewAI nodes solid green
— distinct from ``llm`` (solid blue, has PromptSite) and ``non_llm``
(dashed grey, doesn't cost tokens).
"""

from __future__ import annotations

import ast
import hashlib
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING

from aitap.scanner.models import (
    Confidence,
    EdgeKind,
    Pipeline,
    PipelineEdge,
    PipelineNode,
    PromptSite,
)

if TYPE_CHECKING:
    pass


_CREWAI_CLASSES: frozenset[str] = frozenset({"Agent", "Task", "Crew"})

_PROCESS_SEQUENTIAL = "sequential"
_PROCESS_HIERARCHICAL = "hierarchical"


def _relative(file_path: Path, project_root: Path) -> str:
    try:
        return file_path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return file_path.as_posix()


class CrewAIDetector:
    """Detector for CrewAI multi-agent topology declarations.

    Implements :class:`~aitap.scanner.dataflow.base.ProjectPipelineDetector`
    (returns full :class:`Pipeline` objects). Registered in
    :func:`~aitap.scanner.dataflow.default_project_pipeline_detectors`.
    """

    name = "crewai"

    def detect_pipelines(
        self,
        files: list[Path],
        project_root: Path,
        sites: list[PromptSite],
    ) -> list[Pipeline]:
        del sites  # CrewAI tasks have no backing PromptSites in user code.
        pipelines: list[Pipeline] = []
        for file_path in files:
            try:
                source = file_path.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(source, filename=str(file_path))
            except (OSError, SyntaxError):
                continue

            file_imports = _collect_crewai_aliases(tree)
            if not file_imports:
                continue

            rel = _relative(file_path, project_root)
            agents = _collect_agent_vars(tree, file_imports)
            tasks = _collect_task_vars(tree, file_imports)
            crews = _collect_crew_vars(tree, file_imports)
            for crew_var, crew_spec in crews:
                pipeline = _materialise_pipeline(
                    file_relpath=rel,
                    crew_var=crew_var,
                    crew_spec=crew_spec,
                    agents=agents,
                    tasks=tasks,
                )
                if pipeline is None:
                    continue
                pipelines.append(pipeline)
        return pipelines


# ---------------------------------------------------------------------------
# Import alias collection
# ---------------------------------------------------------------------------


def _collect_crewai_aliases(tree: ast.Module) -> dict[str, str]:
    """Return ``{local_name: canonical_class_name}`` for ``from crewai
    import Agent[, Task, Crew, Process[as P]]`` patterns.

    Empty dict ⇒ no CrewAI import in this file; the detector skips it.
    ``Process`` is tracked too so the ``Process.sequential`` /
    ``Process.hierarchical`` attribute reads resolve correctly.
    """
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module is None or not node.module.startswith("crewai"):
            continue
        for alias in node.names:
            if alias.name in _CREWAI_CLASSES or alias.name == "Process":
                local = alias.asname or alias.name
                out[local] = alias.name
    return out


# ---------------------------------------------------------------------------
# Agent / Task / Crew variable collection
# ---------------------------------------------------------------------------


def _agent_spec(role: str | None) -> dict[str, str | None]:
    return {"role": role}


def _collect_agent_vars(
    tree: ast.Module,
    file_imports: dict[str, str],
) -> dict[str, dict[str, str | None]]:
    """Return ``{var_name: {'role': <role-literal-or-None>}}`` for every
    ``<X> = Agent(role=…, …)`` assignment."""
    out: dict[str, dict[str, str | None]] = {}
    for node in ast.walk(tree):
        target = _assign_target(node)
        value = _assign_value(node)
        if target is None or value is None or not isinstance(value, ast.Call):
            continue
        callee_name = _called_simple_name(value)
        if callee_name is None or file_imports.get(callee_name) != "Agent":
            continue
        role = _kwarg_str_literal(value, "role")
        out[target] = _agent_spec(role)
    return out


def _task_spec(
    description: str | None,
    agent_var: str | None,
    context_vars: list[str] | None,
) -> dict[str, object]:
    return {
        "description": description,
        "agent_var": agent_var,
        "context_vars": context_vars,
    }


def _collect_task_vars(
    tree: ast.Module,
    file_imports: dict[str, str],
) -> dict[str, dict[str, object]]:
    """Return ``{var_name: {'description', 'agent_var', 'context_vars'}}``
    for every ``<T> = Task(...)`` assignment.

    ``context_vars`` is ``None`` when ``context=`` is dynamic (a variable,
    not a list literal); the caller treats ``None`` as "no static deps
    visible" and falls back to sequential ordering.
    """
    out: dict[str, dict[str, object]] = {}
    for node in ast.walk(tree):
        target = _assign_target(node)
        value = _assign_value(node)
        if target is None or value is None or not isinstance(value, ast.Call):
            continue
        callee_name = _called_simple_name(value)
        if callee_name is None or file_imports.get(callee_name) != "Task":
            continue
        description = _kwarg_str_literal(value, "description")
        agent_var = _kwarg_name_ref(value, "agent")
        context_expr = _kwarg_expr(value, "context")
        if context_expr is None:
            context_vars: list[str] | None = []
        elif isinstance(context_expr, ast.List):
            ctx: list[str] = []
            for elt in context_expr.elts:
                if isinstance(elt, ast.Name):
                    ctx.append(elt.id)
            context_vars = ctx
        else:
            # Variable reference (``context=upstream_list``) — dynamic.
            context_vars = None
        out[target] = _task_spec(description, agent_var, context_vars)
    return out


def _crew_spec(
    tasks: list[str] | None,
    process: str | None,
) -> dict[str, object]:
    return {"tasks": tasks, "process": process}


def _collect_crew_vars(
    tree: ast.Module,
    file_imports: dict[str, str],
) -> list[tuple[str, dict[str, object]]]:
    """Return ``[(var_name, {'tasks': […task_vars], 'process': str|None})]``
    for every ``<C> = Crew(...)`` assignment.

    Tasks list is ``None`` when ``tasks=`` is dynamic (variable not list
    literal) — we drop those crews (we can't build a topology without
    knowing which tasks are members).
    """
    out: list[tuple[str, dict[str, object]]] = []
    for node in ast.walk(tree):
        target = _assign_target(node)
        value = _assign_value(node)
        if target is None or value is None or not isinstance(value, ast.Call):
            continue
        callee_name = _called_simple_name(value)
        if callee_name is None or file_imports.get(callee_name) != "Crew":
            continue
        tasks_expr = _kwarg_expr(value, "tasks")
        if not isinstance(tasks_expr, ast.List):
            continue
        task_vars: list[str] = []
        for elt in tasks_expr.elts:
            if isinstance(elt, ast.Name):
                task_vars.append(elt.id)
        process = _kwarg_process_attr(value, file_imports)
        out.append((target, _crew_spec(task_vars, process)))
    return out


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _assign_target(node: ast.AST) -> str | None:
    """If *node* is a simple ``<Name> = ...`` or ``<Name>: T = ...``
    assignment, return the target name; else ``None``.
    """
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        tgt = node.targets[0]
        if isinstance(tgt, ast.Name):
            return tgt.id
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    return None


def _assign_value(node: ast.AST) -> ast.expr | None:
    if isinstance(node, ast.Assign):
        return node.value
    if isinstance(node, ast.AnnAssign):
        return node.value
    return None


def _called_simple_name(call_or_expr: ast.expr) -> str | None:
    if not isinstance(call_or_expr, ast.Call):
        return None
    func = call_or_expr.func
    if isinstance(func, ast.Name):
        return func.id
    return None


def _kwarg_expr(call: ast.Call, name: str) -> ast.expr | None:
    """Return the ``ast.expr`` value of keyword arg *name*, else ``None``."""
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _kwarg_str_literal(call: ast.Call, name: str) -> str | None:
    expr = _kwarg_expr(call, name)
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    return None


def _kwarg_name_ref(call: ast.Call, name: str) -> str | None:
    expr = _kwarg_expr(call, name)
    if isinstance(expr, ast.Name):
        return expr.id
    return None


def _kwarg_process_attr(
    call: ast.Call,
    file_imports: dict[str, str],
) -> str | None:
    """Match ``process=Process.sequential`` / ``Process.hierarchical``.

    The ``Process`` name might be aliased (``import Process as P``), so
    we consult the import map to confirm we're looking at the right
    base.
    """
    expr = _kwarg_expr(call, "process")
    if not isinstance(expr, ast.Attribute):
        return None
    if not isinstance(expr.value, ast.Name):
        return None
    base_canonical = file_imports.get(expr.value.id)
    if base_canonical != "Process":
        return None
    return expr.attr


# ---------------------------------------------------------------------------
# Pipeline materialisation
# ---------------------------------------------------------------------------


def _materialise_pipeline(
    *,
    file_relpath: str,
    crew_var: str,
    crew_spec: dict[str, object],
    agents: dict[str, dict[str, str | None]],
    tasks: dict[str, dict[str, object]],
) -> Pipeline | None:
    """Build the Pipeline for a single Crew(...) instance.

    Returns ``None`` when the crew has no tasks (an empty ``tasks=[]``
    list — common during scaffolding, would produce a confusing
    nodeless DAG).
    """
    task_list_obj = crew_spec.get("tasks")
    task_list: list[str] = task_list_obj if isinstance(task_list_obj, list) else []
    if not task_list:
        return None
    process = crew_spec.get("process") or _PROCESS_SEQUENTIAL

    # Build nodes (one per task in the crew's list, in declaration
    # order). Tasks the crew references but that we haven't captured
    # in ``tasks`` (e.g. they're imported from another module — out of
    # scope) get a synthetic label so the topology still renders.
    nodes: list[PipelineNode] = []
    seen_node_ids: set[str] = set()
    node_id_by_task_var: dict[str, str] = {}
    for task_var in task_list:
        task_spec = tasks.get(task_var, {})
        description_obj = task_spec.get("description")
        description: str | None = description_obj if isinstance(description_obj, str) else None
        agent_var = task_spec.get("agent_var")
        agent_role: str | None = None
        if isinstance(agent_var, str):
            agent_role = agents.get(agent_var, {}).get("role")
        # Synthetic id includes the task variable so re-scans stay stable
        # and ids never collide with PromptSite hashes.
        prompt_id = f"crewai:{file_relpath}:{crew_var}:{task_var}"
        if prompt_id in seen_node_ids:
            continue
        seen_node_ids.add(prompt_id)
        label = _build_label(description, agent_role, task_var)
        nodes.append(
            PipelineNode(
                prompt_id=prompt_id,
                label=label,
                kind="declared_llm",
            )
        )
        node_id_by_task_var[task_var] = prompt_id

    # Build edges.
    edges: list[PipelineEdge] = []
    via = f"{file_relpath}::Crew({crew_var})"
    seen_edges: set[tuple[str, str, Confidence]] = set()

    def _emit(src: str, tgt: str, confidence: Confidence) -> None:
        if src == tgt:
            return
        key = (src, tgt, confidence)
        if key in seen_edges:
            return
        seen_edges.add(key)
        edges.append(
            PipelineEdge(
                source=src,
                target=tgt,
                kind=EdgeKind.CREWAI,
                via=via,
                confidence=confidence,
            )
        )

    # 1. Explicit ``Task(context=[upstream])`` edges — HIGH confidence.
    for task_var in task_list:
        task_spec = tasks.get(task_var, {})
        ctx = task_spec.get("context_vars")
        if not isinstance(ctx, list):
            continue
        for upstream in ctx:
            if upstream not in node_id_by_task_var:
                continue
            _emit(
                node_id_by_task_var[upstream],
                node_id_by_task_var[task_var],
                Confidence.HIGH,
            )

    # 2. Process-driven edges.
    if process == _PROCESS_HIERARCHICAL:
        # Runtime-decided routing — emit a LOW-confidence fan-out from
        # the first task to all others so the UI surfaces the parallel
        # shape without claiming certainty about ordering.
        anchor = task_list[0]
        for downstream in task_list[1:]:
            if anchor in node_id_by_task_var and downstream in node_id_by_task_var:
                _emit(
                    node_id_by_task_var[anchor],
                    node_id_by_task_var[downstream],
                    Confidence.LOW,
                )
    else:
        # Sequential ordering — implicit chain at MEDIUM (context= edges
        # already at HIGH, so the dedup-by-(src,tgt,conf) above keeps
        # both signals when they overlap).
        for src_var, tgt_var in pairwise(task_list):
            if src_var in node_id_by_task_var and tgt_var in node_id_by_task_var:
                _emit(
                    node_id_by_task_var[src_var],
                    node_id_by_task_var[tgt_var],
                    Confidence.MEDIUM,
                )

    # Pipeline orphan-prune: keep nodes only if they appear in at
    # least one edge. (Single-task crews would produce a one-node /
    # zero-edge pipeline — return None for those, the DAG view would
    # show a confusing isolated node.)
    referenced: set[str] = set()
    for e in edges:
        referenced.add(e.source)
        referenced.add(e.target)
    nodes = [n for n in nodes if n.prompt_id in referenced]
    if not nodes or not edges:
        return None

    # Entry/exit points (lessons from B2-LG: the playground runner and
    # pipelines route both read these).
    node_ids = [n.prompt_id for n in nodes]
    incoming_ids = {e.target for e in edges}
    outgoing_ids = {e.source for e in edges}
    entry_points = sorted(nid for nid in node_ids if nid not in incoming_ids)
    exit_points = sorted(nid for nid in node_ids if nid not in outgoing_ids)

    return Pipeline(
        id=_stable_pipeline_id(file_relpath, crew_var),
        name=f"crewai:{crew_var}",
        nodes=nodes,
        edges=edges,
        entry_points=entry_points,
        exit_points=exit_points,
    )


def _build_label(
    description: str | None,
    agent_role: str | None,
    task_var: str,
) -> str:
    """Prefer the task's natural-language description (truncated) over
    the variable name; append the agent's role in parens when known so
    the DAG view shows ``"Research X" (Researcher)``-style labels.
    """
    base = description or task_var
    if len(base) > 40:
        base = base[:37] + "…"
    if agent_role:
        return f"{base} ({agent_role})"
    return base


def _stable_pipeline_id(file_relpath: str, crew_var: str) -> str:
    raw = f"crewai:{file_relpath}:{crew_var}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


__all__ = ["CrewAIDetector"]
