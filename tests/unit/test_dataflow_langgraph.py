"""LangGraph DAG detector tests (B2-LG, wt/scanner-langgraph).

LangGraph projects declare their agent DAG explicitly::

    graph = StateGraph(MyState)
    graph.add_node("classify", classify_intent)
    graph.add_node("respond", generate_response)
    graph.add_edge("classify", "respond")

This detector reads that declaration and emits a Pipeline whose nodes
mirror the declared DAG one-for-one. It's a *project-level* detector
(:class:`ProjectPipelineDetector`) — it returns complete
:class:`Pipeline` objects directly, not edges over the PromptSite id
namespace, because some nodes (``add_node("parse", parse_json)``) have
no LLM call and therefore no PromptSite.

Adjacent OSS for context:

- LangGraph's own ``graph.get_graph()`` viz is a *runtime* feature — it
  needs the StateGraph to be instantiated and the nodes registered.
- ``agentic-radar`` (979★) does a similar static scan but only for
  framework-aware projects; like us it walks ``add_node`` / ``add_edge``
  by AST.

We diverge from agentic-radar in two ways: (a) we resolve callee
references back to PromptSites so the LangGraph node lights up the
same LLM site the user already sees in their inventory; (b) we
preserve non-LLM nodes via ``PipelineNode.kind="non_llm"`` so the DAG
topology stays intact even when intermediate steps don't call an LLM.

Test plan (per design doc, B2-LG):

1. linear chain ``START → A → B → C → END`` with three LLM-bearing
   nodes — 3 LLM nodes, 2 edges, ``EdgeKind.LANGGRAPH``,
   ``Confidence.HIGH``;
2. mid-chain non-LLM node ``A → B(parse) → C`` — option 3 (virtual
   node): B kept with ``kind="non_llm"``, A→B and B→C both emitted;
3. conditional edges expand into one edge per mapping entry;
4. ``set_entry_point`` legacy API still recognised;
5. lambda node body lets us find the wrapped LLM call;
6. dynamic conditional mapping (not a dict literal) ⇒ those edges
   are skipped — we don't fabricate connections we can't see;
7. aliased import (``from langgraph.graph import StateGraph as SG``)
   still recognised.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from aitap.scanner.dataflow import LangGraphDetector
from aitap.scanner.engine import scan_project
from aitap.scanner.models import Confidence, EdgeKind, Pipeline

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


def _langgraph_pipelines(result) -> list[Pipeline]:
    """Filter to pipelines whose edges came from the LangGraph rule.

    The result may also contain Pipelines from other detectors; this
    helper keeps the assertions focused on the LangGraph contribution.
    """
    out: list[Pipeline] = []
    for p in result.pipelines:
        if any(e.kind is EdgeKind.LANGGRAPH for e in p.edges):
            out.append(p)
    return out


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    return tmp_path


# --------------------------------------------------------------------------- #
# 1. Linear chain                                                             #
# --------------------------------------------------------------------------- #


def test_langgraph_linear_chain_emits_high_confidence_edges(
    project_root: Path,
) -> None:
    """``START → A → B → C → END`` with three LLM-bearing nodes
    produces three LLM nodes + two LANGGRAPH edges at HIGH confidence.
    """
    _write(
        project_root,
        "app/agent.py",
        """
        from langgraph.graph import StateGraph, START, END

        async def classify(state):
            return await openai.complete(
                messages=[{"role": "user", "content": "Classify."}],
            )

        async def respond(state):
            return await openai.complete(
                messages=[{"role": "user", "content": "Respond."}],
            )

        async def validate(state):
            return await openai.complete(
                messages=[{"role": "user", "content": "Validate."}],
            )

        def build_graph():
            g = StateGraph(dict)
            g.add_node("classify", classify)
            g.add_node("respond", respond)
            g.add_node("validate", validate)
            g.add_edge(START, "classify")
            g.add_edge("classify", "respond")
            g.add_edge("respond", "validate")
            g.add_edge("validate", END)
            return g.compile()
        """,
    )
    result = _scan(project_root)
    lg_pipelines = _langgraph_pipelines(result)
    assert len(lg_pipelines) == 1
    pipeline = lg_pipelines[0]

    # Three LLM nodes (START/END are LangGraph sentinels, not real
    # nodes in our DAG).
    llm_nodes = [n for n in pipeline.nodes if n.kind == "llm"]
    assert len(llm_nodes) == 3

    # Two edges: classify→respond, respond→validate. The
    # START→classify / validate→END sentinel-touching edges are
    # filtered out (START/END aren't LLM call sites).
    assert len(pipeline.edges) == 2
    assert all(edge.kind is EdgeKind.LANGGRAPH for edge in pipeline.edges)
    assert all(edge.confidence is Confidence.HIGH for edge in pipeline.edges)


# --------------------------------------------------------------------------- #
# 2. Mid-chain non-LLM node (option 3 — virtual node)                         #
# --------------------------------------------------------------------------- #


def test_langgraph_keeps_non_llm_node_with_kind_marker(
    project_root: Path,
) -> None:
    """``A → B(parse) → C`` where B is a pure-Python helper (no LLM):
    B stays in the DAG with ``kind="non_llm"``; both A→B and B→C
    edges emit so the topology reads correctly.
    """
    _write(
        project_root,
        "app/agent_with_helper.py",
        """
        from langgraph.graph import StateGraph

        async def classify(state):
            return await openai.complete(
                messages=[{"role": "user", "content": "Classify."}],
            )

        def parse_json(state):
            # Pure Python — no LLM call.
            return {"parsed": True}

        async def respond(state):
            return await openai.complete(
                messages=[{"role": "user", "content": "Respond."}],
            )

        def build_graph():
            g = StateGraph(dict)
            g.add_node("classify", classify)
            g.add_node("parse", parse_json)
            g.add_node("respond", respond)
            g.add_edge("classify", "parse")
            g.add_edge("parse", "respond")
            return g.compile()
        """,
    )
    result = _scan(project_root)
    lg_pipelines = _langgraph_pipelines(result)
    assert len(lg_pipelines) == 1
    pipeline = lg_pipelines[0]

    # Three nodes total: 2 LLM + 1 non_llm.
    kinds = sorted(n.kind for n in pipeline.nodes)
    assert kinds == ["llm", "llm", "non_llm"]

    # Two edges still emitted — the topology is preserved.
    assert len(pipeline.edges) == 2


# --------------------------------------------------------------------------- #
# 3. Conditional edges                                                        #
# --------------------------------------------------------------------------- #


def test_langgraph_conditional_edges_expand_into_one_edge_per_mapping(
    project_root: Path,
) -> None:
    """``add_conditional_edges("router", route_fn, {"yes": "A", "no": "B"})``
    emits one LANGGRAPH edge per mapping value (router→A, router→B).
    """
    _write(
        project_root,
        "app/agent_conditional.py",
        """
        from langgraph.graph import StateGraph

        async def router(state):
            return await openai.complete(
                messages=[{"role": "user", "content": "Route."}],
            )

        async def step_a(state):
            return await openai.complete(
                messages=[{"role": "user", "content": "A."}],
            )

        async def step_b(state):
            return await openai.complete(
                messages=[{"role": "user", "content": "B."}],
            )

        def route_choice(state):
            return "yes" if state.get("flag") else "no"

        def build_graph():
            g = StateGraph(dict)
            g.add_node("router", router)
            g.add_node("step_a", step_a)
            g.add_node("step_b", step_b)
            g.add_conditional_edges(
                "router",
                route_choice,
                {"yes": "step_a", "no": "step_b"},
            )
            return g.compile()
        """,
    )
    result = _scan(project_root)
    lg_pipelines = _langgraph_pipelines(result)
    assert len(lg_pipelines) == 1
    pipeline = lg_pipelines[0]

    assert len(pipeline.edges) == 2
    edge_pairs = sorted((e.source, e.target) for e in pipeline.edges)
    # Both edges originate at router; targets are step_a and step_b.
    sources = {pair[0] for pair in edge_pairs}
    assert len(sources) == 1  # all from router


# --------------------------------------------------------------------------- #
# 4. set_entry_point legacy API                                               #
# --------------------------------------------------------------------------- #


def test_langgraph_set_entry_point_legacy_api_recognised(
    project_root: Path,
) -> None:
    """``set_entry_point("a")`` + ``set_finish_point("b")`` are the
    pre-START/END API. They're equivalent to ``add_edge(START, "a")``
    / ``add_edge("b", END)`` — the detector treats them identically.
    """
    _write(
        project_root,
        "app/agent_legacy.py",
        """
        from langgraph.graph import StateGraph

        async def step_a(state):
            return await openai.complete(
                messages=[{"role": "user", "content": "A."}],
            )

        async def step_b(state):
            return await openai.complete(
                messages=[{"role": "user", "content": "B."}],
            )

        def build_graph():
            g = StateGraph(dict)
            g.add_node("a", step_a)
            g.add_node("b", step_b)
            g.set_entry_point("a")
            g.add_edge("a", "b")
            g.set_finish_point("b")
            return g.compile()
        """,
    )
    result = _scan(project_root)
    lg_pipelines = _langgraph_pipelines(result)
    assert len(lg_pipelines) == 1
    pipeline = lg_pipelines[0]
    # The a→b edge survives the legacy API (sentinel-touching edges
    # are dropped, same as in the linear-chain test).
    assert len(pipeline.edges) == 1


# --------------------------------------------------------------------------- #
# 5. Lambda node                                                              #
# --------------------------------------------------------------------------- #


def test_langgraph_lambda_node_resolves_through_lambda_body(
    project_root: Path,
) -> None:
    """``add_node("x", lambda s: classify(s))`` — the lambda wraps a
    call to a known LLM-bearing function. The detector unwraps the
    lambda and finds the PromptSite anchor.
    """
    _write(
        project_root,
        "app/agent_lambda.py",
        """
        from langgraph.graph import StateGraph

        async def classify(state):
            return await openai.complete(
                messages=[{"role": "user", "content": "Classify."}],
            )

        async def respond(state):
            return await openai.complete(
                messages=[{"role": "user", "content": "Respond."}],
            )

        async def validate(state):
            return await openai.complete(
                messages=[{"role": "user", "content": "Validate."}],
            )

        def build_graph():
            g = StateGraph(dict)
            g.add_node("classify", lambda s: classify(s))
            g.add_node("respond", respond)
            g.add_node("validate", validate)
            g.add_edge("classify", "respond")
            g.add_edge("respond", "validate")
            return g.compile()
        """,
    )
    result = _scan(project_root)
    lg_pipelines = _langgraph_pipelines(result)
    assert len(lg_pipelines) == 1
    pipeline = lg_pipelines[0]
    # All three nodes resolve to LLM (lambda body unwraps to
    # classify()'s site).
    llm_nodes = [n for n in pipeline.nodes if n.kind == "llm"]
    assert len(llm_nodes) == 3


# --------------------------------------------------------------------------- #
# 6. Dynamic conditional mapping — skipped                                    #
# --------------------------------------------------------------------------- #


def test_langgraph_dynamic_conditional_mapping_skips_those_edges(
    project_root: Path,
) -> None:
    """When ``add_conditional_edges`` is passed a variable (not a dict
    literal) as the mapping, we can't statically see which targets
    are reachable. We skip those edges — don't fabricate connections.
    The straight ``add_edge`` calls in the same graph still emit.
    """
    _write(
        project_root,
        "app/agent_dynamic.py",
        """
        from langgraph.graph import StateGraph

        async def router(state):
            return await openai.complete(
                messages=[{"role": "user", "content": "Route."}],
            )

        async def step_a(state):
            return await openai.complete(
                messages=[{"role": "user", "content": "A."}],
            )

        async def step_b(state):
            return await openai.complete(
                messages=[{"role": "user", "content": "B."}],
            )

        def route_choice(state):
            return state["next"]

        MAPPING = {"yes": "step_a", "no": "step_b"}  # dynamic — not inline dict literal

        def build_graph():
            g = StateGraph(dict)
            g.add_node("router", router)
            g.add_node("step_a", step_a)
            g.add_node("step_b", step_b)
            g.add_edge("step_a", "step_b")  # this static edge should survive
            g.add_conditional_edges("router", route_choice, MAPPING)  # this is skipped
            return g.compile()
        """,
    )
    result = _scan(project_root)
    lg_pipelines = _langgraph_pipelines(result)
    assert len(lg_pipelines) == 1
    pipeline = lg_pipelines[0]
    # Only the static add_edge("step_a", "step_b") survives — the
    # dynamic conditional doesn't emit phantom edges.
    assert len(pipeline.edges) == 1
    edge = pipeline.edges[0]
    assert edge.kind is EdgeKind.LANGGRAPH


# --------------------------------------------------------------------------- #
# 7. Aliased import                                                           #
# --------------------------------------------------------------------------- #


def test_langgraph_aliased_import_still_recognised(
    project_root: Path,
) -> None:
    """``from langgraph.graph import StateGraph as SG`` — the alias
    binds SG in the file's namespace, and the detector follows the
    import map so ``SG(...)`` still resolves to StateGraph.
    """
    _write(
        project_root,
        "app/agent_aliased.py",
        """
        from langgraph.graph import StateGraph as SG

        async def step_a(state):
            return await openai.complete(
                messages=[{"role": "user", "content": "A."}],
            )

        async def step_b(state):
            return await openai.complete(
                messages=[{"role": "user", "content": "B."}],
            )

        async def step_c(state):
            return await openai.complete(
                messages=[{"role": "user", "content": "C."}],
            )

        def build_graph():
            g = SG(dict)
            g.add_node("a", step_a)
            g.add_node("b", step_b)
            g.add_node("c", step_c)
            g.add_edge("a", "b")
            g.add_edge("b", "c")
            return g.compile()
        """,
    )
    result = _scan(project_root)
    lg_pipelines = _langgraph_pipelines(result)
    assert len(lg_pipelines) == 1


# --------------------------------------------------------------------------- #
# Detector-level smoke test                                                   #
# --------------------------------------------------------------------------- #


def test_detector_returns_empty_list_when_no_state_graph_in_project(
    project_root: Path,
) -> None:
    """Project with LLM calls but no StateGraph → detector returns []."""
    _write(
        project_root,
        "app/plain.py",
        """
        async def step(state):
            return await openai.complete(
                messages=[{"role": "user", "content": "Hello."}],
            )
        """,
    )
    detector = LangGraphDetector()
    result = _scan(project_root)
    files = [project_root / "app/plain.py"]
    pipelines = detector.detect_pipelines(files, project_root, result.prompts)
    assert pipelines == []
