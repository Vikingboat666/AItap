"""LangGraph DAG detector (B2-LG, wt/scanner-langgraph).

Reads the explicit DAG declaration LangGraph projects expose:

    from langgraph.graph import StateGraph, START, END

    graph = StateGraph(MyState)
    graph.add_node("classify", classify_intent)
    graph.add_node("respond", generate_response)
    graph.add_edge("classify", "respond")
    graph.add_conditional_edges("respond", router, {"yes": "A", "no": "B"})

and emits a complete :class:`Pipeline` whose nodes mirror the declared
DAG one-for-one (including non-LLM helpers via
:attr:`PipelineNode.kind` ``= "non_llm"`` so the topology stays intact
even when intermediate steps don't call an LLM).

This is a :class:`ProjectPipelineDetector` — it returns complete
:class:`Pipeline` objects directly, bypassing the
:func:`build_pipelines_from_edges` union-find pass that the other
detectors flow through. Reason: the other detectors describe edges
over the :class:`PromptSite` id namespace; LangGraph DAGs can include
nodes that have no PromptSite (``add_node("parse", parse_json)`` where
``parse_json`` is a pure-Python helper), and a PromptSite-id-only
representation would silently drop those.

Scope (L1, purely syntactic — no symbolic execution):

- Recognised APIs: ``StateGraph(...)`` / ``MessageGraph(...)`` instance
  + ``add_node`` / ``add_edge`` / ``add_conditional_edges`` /
  ``set_entry_point`` / ``set_finish_point``.
- Callee resolution: ``Name(fn)`` and ``Lambda(...)``; ``Attribute``
  chains (``self.classify`` etc.) are tracked one level deep.
- Aliased imports (``from langgraph.graph import StateGraph as SG``)
  are followed via the same import-alias map :class:`CrossFileOrchestration`
  builds.

Deliberately out of scope (v2 candidates):

- ``partial(fn, ...)`` wrapped callees.
- Cross-file ``add_node(fn)`` where ``fn`` is imported from another
  module — we only resolve callees defined in the same file as the
  ``StateGraph`` instantiation. The cross_file_orchestration import
  map would be a natural starting point but the callee-to-PromptSite
  lookup needs project-wide ``def`` indexing, which is bigger than
  this PR.
- Subgraph composition (``graph.add_node("sub", subgraph.compile())``).
- Dynamic node-name strings (``add_node(NODE_NAME, fn)`` where
  ``NODE_NAME`` is a variable).
- ``MessageGraph`` — same API shape as ``StateGraph`` and the
  detector accepts both; LangGraph 0.2+ deprecated it but projects in
  the wild still use it.

Confidence: :attr:`Confidence.HIGH`. The DAG is declared explicitly —
no heuristic over source order. The downgrade we apply elsewhere for
branchy intra-class chains (B1) doesn't apply here because LangGraph's
edges *are* the branch declaration.
"""

from __future__ import annotations

import ast
import hashlib
from itertools import pairwise  # noqa: F401  (reserved for future use)
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

from .base import index_sites_by_line

if TYPE_CHECKING:
    pass


# Sentinel node names LangGraph uses for graph entry/exit. We strip
# them from the emitted DAG because they aren't LLM call sites.
_LANGGRAPH_SENTINELS: frozenset[str] = frozenset({"START", "END"})

# Canonical class names the detector recognises as graph-instantiation
# entry points. ``MessageGraph`` is the deprecated-but-still-in-the-wild
# variant of ``StateGraph`` with the same API shape.
_LANGGRAPH_GRAPH_CLASSES: frozenset[str] = frozenset({"StateGraph", "MessageGraph"})

# LangGraph methods we walk to extract DAG topology.
_METHOD_ADD_NODE = "add_node"
_METHOD_ADD_EDGE = "add_edge"
_METHOD_ADD_CONDITIONAL_EDGES = "add_conditional_edges"
_METHOD_SET_ENTRY_POINT = "set_entry_point"
_METHOD_SET_FINISH_POINT = "set_finish_point"


def _relative(file_path: Path, project_root: Path) -> str:
    try:
        return file_path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return file_path.as_posix()


class LangGraphDetector:
    """Detector for explicit LangGraph DAG declarations.

    Implements :class:`~aitap.scanner.dataflow.base.ProjectPipelineDetector`
    (returns full :class:`Pipeline` objects, not edges). Registered in
    :func:`~aitap.scanner.dataflow.default_project_pipeline_detectors`.
    """

    name = "langgraph"

    def detect_pipelines(
        self,
        files: list[Path],
        project_root: Path,
        sites: list[PromptSite],
    ) -> list[Pipeline]:
        """Walk each file, extract every StateGraph DAG, emit pipelines.

        One Pipeline per StateGraph instance. Multiple StateGraphs in
        the same file ⇒ one Pipeline each (independent topology).
        Files with no StateGraph instantiation contribute nothing.
        """
        # Per-file PromptSite index so we can resolve a callee's body
        # to its first PromptSite anchor (the same convention the
        # intra-class and intra-file detectors use).
        sites_by_file: dict[str, list[PromptSite]] = {}
        for site in sites:
            sites_by_file.setdefault(site.location.file, []).append(site)

        pipelines: list[Pipeline] = []
        for file_path in files:
            try:
                source = file_path.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(source, filename=str(file_path))
            except (OSError, SyntaxError):
                continue

            rel = _relative(file_path, project_root)
            file_imports = _collect_langgraph_aliases(tree)
            if not file_imports:
                # No StateGraph / MessageGraph import in this file — no
                # graph declaration possible. Cheap early-out.
                continue
            file_sites = sites_by_file.get(rel, [])
            line_index = index_sites_by_line(file_sites)
            fn_to_anchor = _build_function_anchor_map(tree, line_index)
            graph_vars = _collect_graph_instance_vars(tree, file_imports)
            if not graph_vars:
                continue

            for var_name in graph_vars:
                graph = _scan_one_graph(
                    tree,
                    var_name=var_name,
                    fn_to_anchor=fn_to_anchor,
                    file_relpath=rel,
                )
                if graph is None:
                    continue
                pipelines.append(graph)
        return pipelines


def _collect_langgraph_aliases(tree: ast.Module) -> dict[str, str]:
    """Return ``{local_name: canonical_class_name}`` for any
    ``from langgraph.graph import StateGraph[as X]`` / ``MessageGraph``.

    Empty dict means the file doesn't import langgraph at all; the
    detector treats that as "no DAG here, move on" — cheap rejection
    before we do any AST walking.
    """
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module is None or not node.module.startswith("langgraph"):
            continue
        for alias in node.names:
            if alias.name in _LANGGRAPH_GRAPH_CLASSES:
                local = alias.asname or alias.name
                out[local] = alias.name
    return out


def _build_function_anchor_map(
    tree: ast.Module,
    line_index: dict[int, PromptSite],
) -> dict[str, PromptSite | None]:
    """Return ``{function_name: first_PromptSite_or_None}`` for every
    top-level ``def`` / ``async def`` in the file.

    Used to resolve ``add_node("a", classify)`` — we look up
    ``classify`` here, walk its body, and return the first PromptSite
    Call it makes. A callee with no PromptSite in its body maps to
    ``None``, which the caller translates into ``PipelineNode.kind="non_llm"``.

    Nested functions are NOT indexed because LangGraph's ``add_node``
    refers to module-level names. Methods on classes aren't indexed
    either — that's a v2 (``self.classify`` resolution would need
    per-class scoping).
    """
    out: dict[str, PromptSite | None] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = _first_site_in_function(node, line_index)
    return out


def _first_site_in_function(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    line_index: dict[int, PromptSite],
) -> PromptSite | None:
    """Walk *fn*'s body (NOT into nested defs) for the first
    PromptSite Call. Mirrors the nested-def gate the B1 detector uses
    so a closure-style helper inside *fn* doesn't steal the anchor.
    """
    stop_at = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
    stack: list[ast.AST] = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Call):
            lineno = getattr(node, "lineno", None)
            if lineno is not None:
                site = line_index.get(lineno)
                if site is not None:
                    return site
        if isinstance(node, stop_at):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return None


def _collect_graph_instance_vars(
    tree: ast.Module,
    file_imports: dict[str, str],
) -> list[str]:
    """Return the variable names bound to ``StateGraph(...)`` / ``MessageGraph(...)``.

    Walks every assignment in the file (including ones inside function
    bodies — LangGraph projects commonly wrap the graph build in
    ``def build_graph(): ...``). Returns names in declaration order
    so multiple graphs in the same file produce deterministic
    Pipeline ordering.
    """
    out: list[str] = []
    seen: set[str] = set()
    for node in ast.walk(tree):
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target = node.target
            value = node.value
        if target is None or value is None:
            continue
        if not isinstance(target, ast.Name):
            continue
        if not isinstance(value, ast.Call):
            continue
        callee_name = _called_simple_name(value)
        if callee_name is None:
            continue
        if file_imports.get(callee_name) not in _LANGGRAPH_GRAPH_CLASSES:
            continue
        if target.id in seen:
            continue
        seen.add(target.id)
        out.append(target.id)
    return out


def _called_simple_name(call: ast.Call) -> str | None:
    """Return the bare name of *call*'s callee if it's a ``Name``,
    else ``None``. Filters out ``mod.SG(...)`` (we want the import
    alias map to be the source of truth)."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    return None


def _scan_one_graph(
    tree: ast.Module,
    *,
    var_name: str,
    fn_to_anchor: dict[str, PromptSite | None],
    file_relpath: str,
) -> Pipeline | None:
    """Walk *tree* for every ``<var_name>.<method>(...)`` call and
    assemble one :class:`Pipeline`.

    Returns ``None`` when the graph has no edges (a StateGraph that
    was instantiated but never wired — common in incomplete code). A
    Pipeline with zero edges would mislead the UI into rendering a
    DAG with isolated nodes.
    """
    nodes_in_order: list[tuple[str, ast.expr | None]] = []
    edges_raw: list[tuple[str, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        receiver_var, method_name = _split_method_call(node, var_name)
        if receiver_var is None:
            continue
        if method_name == _METHOD_ADD_NODE:
            spec = _parse_add_node(node)
            if spec is not None:
                node_name, callee_expr = spec
                if node_name not in {n for n, _ in nodes_in_order}:
                    nodes_in_order.append((node_name, callee_expr))
        elif method_name == _METHOD_ADD_EDGE:
            spec_edge = _parse_add_edge(node)
            if spec_edge is not None:
                edges_raw.append(spec_edge)
        elif method_name == _METHOD_ADD_CONDITIONAL_EDGES:
            edges_raw.extend(_parse_add_conditional_edges(node))
        elif method_name == _METHOD_SET_ENTRY_POINT:
            spec_legacy = _parse_set_entry_or_finish(node)
            if spec_legacy is not None:
                edges_raw.append(("START", spec_legacy))
        elif method_name == _METHOD_SET_FINISH_POINT:
            spec_legacy = _parse_set_entry_or_finish(node)
            if spec_legacy is not None:
                edges_raw.append((spec_legacy, "END"))

    if not nodes_in_order or not edges_raw:
        return None

    return _materialise_pipeline(
        var_name=var_name,
        file_relpath=file_relpath,
        nodes_in_order=nodes_in_order,
        edges_raw=edges_raw,
        fn_to_anchor=fn_to_anchor,
    )


def _split_method_call(
    call: ast.Call,
    receiver_var: str,
) -> tuple[str | None, str | None]:
    """If *call* is ``<receiver_var>.<method>(...)`` return
    ``(receiver_var, method_name)``; else ``(None, None)``.
    """
    func = call.func
    if not isinstance(func, ast.Attribute):
        return (None, None)
    value = func.value
    if not isinstance(value, ast.Name):
        return (None, None)
    if value.id != receiver_var:
        return (None, None)
    return (receiver_var, func.attr)


def _parse_add_node(call: ast.Call) -> tuple[str, ast.expr | None] | None:
    """``add_node(name, fn)`` → ``(name, fn_expr)``.

    Also accepts the single-arg form ``add_node(fn)`` where the node
    name defaults to ``fn.__name__`` — we synthesise the name from
    the callee's bare name. Anything we can't statically resolve (the
    node name is a variable, the callee is a complex expression) ⇒
    we return ``None`` and skip the node.
    """
    args = call.args
    if not args:
        return None
    if len(args) >= 2:
        name_expr = args[0]
        callee_expr: ast.expr | None = args[1]
        name = _str_literal(name_expr)
        if name is None:
            return None
        return (name, callee_expr)
    # Single-arg form: add_node(fn) — synthesise name from fn.
    callee = args[0]
    name = _callable_synthetic_name(callee)
    if name is None:
        return None
    return (name, callee)


def _parse_add_edge(call: ast.Call) -> tuple[str, str] | None:
    """``add_edge(src, tgt)`` → ``(src, tgt)``.

    ``src`` / ``tgt`` may be string literals or the LangGraph
    ``START`` / ``END`` sentinel ``Name`` references. Variable
    references ⇒ skip (dynamic, we can't see the value).
    """
    if len(call.args) < 2:
        return None
    src = _node_name_expr(call.args[0])
    tgt = _node_name_expr(call.args[1])
    if src is None or tgt is None:
        return None
    return (src, tgt)


def _parse_add_conditional_edges(call: ast.Call) -> list[tuple[str, str]]:
    """``add_conditional_edges(src, router, mapping)`` → one edge per
    mapping value. ``mapping`` must be a dict literal — variables or
    other dynamic expressions yield zero edges (we don't fabricate
    connections we can't see).
    """
    if len(call.args) < 1:
        return []
    src = _node_name_expr(call.args[0])
    if src is None:
        return []
    # Optional mapping is the third positional or a ``path_map=`` kwarg.
    mapping: ast.expr | None = None
    if len(call.args) >= 3:
        mapping = call.args[2]
    else:
        for kw in call.keywords:
            if kw.arg in ("path_map", "mapping"):
                mapping = kw.value
                break
    if mapping is None:
        return []
    if not isinstance(mapping, ast.Dict):
        return []
    out: list[tuple[str, str]] = []
    for value in mapping.values:
        if value is None:
            continue
        tgt = _node_name_expr(value)
        if tgt is None:
            continue
        out.append((src, tgt))
    return out


def _parse_set_entry_or_finish(call: ast.Call) -> str | None:
    """``set_entry_point("a")`` / ``set_finish_point("b")`` → the
    string literal name. Returns ``None`` for dynamic args.
    """
    if not call.args:
        return None
    return _node_name_expr(call.args[0])


def _node_name_expr(expr: ast.expr) -> str | None:
    """Resolve *expr* to a LangGraph node name.

    - ``Constant(str)`` → the string literal.
    - ``Name("START")`` / ``Name("END")`` → that sentinel keyword
      (we filter it out at emit time).
    - Anything else ⇒ ``None`` (dynamic, can't statically resolve).
    """
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    if isinstance(expr, ast.Name) and expr.id in _LANGGRAPH_SENTINELS:
        return expr.id
    return None


def _str_literal(expr: ast.expr) -> str | None:
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    return None


def _callable_synthetic_name(expr: ast.expr) -> str | None:
    """For the single-arg ``add_node(fn)`` form, derive a node name.

    Accepts a bare ``Name(id)``; rejects more complex expressions
    (``mod.fn``, ``partial(fn, ...)`` — those need v2).
    """
    if isinstance(expr, ast.Name):
        return expr.id
    return None


def _resolve_callee_to_anchor(
    callee: ast.expr | None,
    fn_to_anchor: dict[str, PromptSite | None],
) -> PromptSite | None:
    """Resolve *callee* to its first PromptSite (if any).

    Handles:

    - ``Name("classify")`` → ``fn_to_anchor["classify"]``.
    - ``Lambda(body=Call(...))`` → unwrap the lambda body; if it's a
      single call to a known function, recurse. Common LangGraph
      shape: ``add_node("x", lambda s: classify(s))``.
    - Anything else → ``None`` (we record the node but mark it
      ``kind="non_llm"`` because we couldn't tie it to an LLM call).
    """
    if callee is None:
        return None
    if isinstance(callee, ast.Name):
        return fn_to_anchor.get(callee.id)
    if isinstance(callee, ast.Lambda):
        # Walk the lambda body for a Call whose callee is a known fn.
        for node in ast.walk(callee.body):
            if isinstance(node, ast.Call):
                inner = _called_simple_name(node)
                if inner is not None and inner in fn_to_anchor:
                    return fn_to_anchor[inner]
        return None
    return None


def _materialise_pipeline(
    *,
    var_name: str,
    file_relpath: str,
    nodes_in_order: list[tuple[str, ast.expr | None]],
    edges_raw: list[tuple[str, str]],
    fn_to_anchor: dict[str, PromptSite | None],
) -> Pipeline:
    """Assemble a :class:`Pipeline` from the parsed nodes + edges.

    Filters out edges that touch the LangGraph sentinels ``START`` /
    ``END`` — they aren't LLM call sites and the UI would render them
    as confusing orphan nodes. Adds the surviving edges with
    ``EdgeKind.LANGGRAPH`` + ``Confidence.HIGH``. Generates a stable
    Pipeline id from the file + var name so re-scans produce the same
    id.
    """
    # Map each declared node name to a stable prompt_id + kind.
    node_specs: dict[str, tuple[str, str]] = {}  # name → (prompt_id, kind)
    nodes: list[PipelineNode] = []
    for node_name, callee_expr in nodes_in_order:
        if node_name in _LANGGRAPH_SENTINELS:
            continue
        anchor = _resolve_callee_to_anchor(callee_expr, fn_to_anchor)
        if anchor is not None:
            prompt_id = anchor.id
            kind = "llm"
            label = anchor.name
        else:
            # Synthesise a stable id so the node has *something* to
            # reference. Prefix prevents collision with real
            # PromptSite ids (those are SHA-256 hashes).
            prompt_id = f"langgraph:{file_relpath}:{var_name}:{node_name}"
            kind = "non_llm"
            label = node_name
        node_specs[node_name] = (prompt_id, kind)
        nodes.append(PipelineNode(prompt_id=prompt_id, label=label, kind=kind))  # type: ignore[arg-type]

    edges: list[PipelineEdge] = []
    via = f"{file_relpath}::StateGraph({var_name})"
    seen_edges: set[tuple[str, str]] = set()
    for src_name, tgt_name in edges_raw:
        if src_name in _LANGGRAPH_SENTINELS or tgt_name in _LANGGRAPH_SENTINELS:
            continue
        if src_name not in node_specs or tgt_name not in node_specs:
            continue
        src_id = node_specs[src_name][0]
        tgt_id = node_specs[tgt_name][0]
        if (src_id, tgt_id) in seen_edges:
            continue
        seen_edges.add((src_id, tgt_id))
        edges.append(
            PipelineEdge(
                source=src_id,
                target=tgt_id,
                kind=EdgeKind.LANGGRAPH,
                via=via,
                confidence=Confidence.HIGH,
            )
        )

    # Prune nodes that don't participate in any edge — they're either
    # sentinels (already filtered) or orphans the user hasn't wired
    # up yet. Either way they'd render as confusing isolated nodes.
    referenced: set[str] = set()
    for e in edges:
        referenced.add(e.source)
        referenced.add(e.target)
    nodes = [n for n in nodes if n.prompt_id in referenced]

    pipeline_id = _stable_pipeline_id(file_relpath, var_name)
    pipeline_name = f"langgraph:{var_name}"
    return Pipeline(
        id=pipeline_id,
        name=pipeline_name,
        nodes=nodes,
        edges=edges,
    )


def _stable_pipeline_id(file_relpath: str, var_name: str) -> str:
    """Hash of ``<file>:<var>`` so re-scans of the same project
    produce the same Pipeline id (the contract elsewhere expects
    Pipeline ids to be stable across runs)."""
    raw = f"langgraph:{file_relpath}:{var_name}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


__all__ = ["LangGraphDetector"]
