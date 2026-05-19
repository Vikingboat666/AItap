"""Unit tests for the Impact Analyzer (Wave 4, ``iterate/impact.py``).

The analyzer is a pure DAG walker over the scanner's ``Pipeline`` contract:

- ``analyze`` does a BFS from an iterated node and returns the downstream
  consumers ordered by ``(distance, node_id)``.
- ``assess_status`` compares pre/post weighted scores per node and emits a
  4-state classification (verified / regressed / improved / unverified).
- ``serialize_status_for_iterations`` produces the JSON shape persisted
  on the ``iterations.downstream_status`` column (Decision 5 of the Wave 4
  design doc).

Tests build ``Pipeline`` instances inline — no real files, no LLM.
"""

from __future__ import annotations

import pytest

from aitap.iterate.impact import (
    DownstreamNode,
    DownstreamStatus,
    analyze,
    assess_status,
    serialize_status_for_iterations,
)
from aitap.scanner.models import (
    EdgeKind,
    Pipeline,
    PipelineEdge,
    PipelineNode,
)

# --------------------------------------------------------------------------- #
# Pipeline helpers                                                            #
# --------------------------------------------------------------------------- #


def _pipeline(
    node_ids: list[str],
    edges: list[tuple[str, str, EdgeKind]],
    *,
    pipeline_id: str = "p",
    name: str = "p",
) -> Pipeline:
    """Build a ``Pipeline`` from node ids and (source, target, kind) tuples.

    Keeping this helper trivial means each test reads as ``nodes + edges``
    rather than five lines of pydantic boilerplate.
    """
    nodes = [PipelineNode(prompt_id=nid) for nid in node_ids]
    pipeline_edges = [PipelineEdge(source=src, target=tgt, kind=kind) for src, tgt, kind in edges]
    incoming: set[str] = {tgt for _, tgt, _ in edges}
    outgoing: set[str] = {src for src, _, _ in edges}
    entry_points = [nid for nid in node_ids if nid not in incoming]
    exit_points = [nid for nid in node_ids if nid not in outgoing]
    return Pipeline(
        id=pipeline_id,
        name=name,
        nodes=nodes,
        edges=pipeline_edges,
        entry_points=entry_points,
        exit_points=exit_points,
    )


# --------------------------------------------------------------------------- #
# analyze                                                                     #
# --------------------------------------------------------------------------- #


def test_analyze_linear_chain_from_root_returns_full_downstream() -> None:
    pipeline = _pipeline(
        ["a", "b", "c"],
        [("a", "b", EdgeKind.VARIABLE), ("b", "c", EdgeKind.VARIABLE)],
    )
    result = analyze(pipeline, iterated_node_id="a")
    assert [n.node_id for n in result] == ["b", "c"]
    assert [n.distance for n in result] == [1, 2]
    # iterated node itself never appears in its own downstream list
    assert all(n.node_id != "a" for n in result)


def test_analyze_linear_chain_from_sink_returns_empty() -> None:
    pipeline = _pipeline(
        ["a", "b", "c"],
        [("a", "b", EdgeKind.VARIABLE), ("b", "c", EdgeKind.VARIABLE)],
    )
    assert analyze(pipeline, iterated_node_id="c") == []


def test_analyze_linear_chain_from_middle_skips_iterated_node() -> None:
    pipeline = _pipeline(
        ["a", "b", "c"],
        [("a", "b", EdgeKind.VARIABLE), ("b", "c", EdgeKind.VARIABLE)],
    )
    result = analyze(pipeline, iterated_node_id="b")
    assert [n.node_id for n in result] == ["c"]
    assert result[0].distance == 1


def test_analyze_fanout_returns_both_branches_at_distance_one() -> None:
    pipeline = _pipeline(
        ["a", "b", "c"],
        [("a", "b", EdgeKind.VARIABLE), ("a", "c", EdgeKind.VARIABLE)],
    )
    result = analyze(pipeline, iterated_node_id="a")
    # ordering: distance asc, then node_id asc
    assert [n.node_id for n in result] == ["b", "c"]
    assert [n.distance for n in result] == [1, 1]


def test_analyze_fanin_does_not_duplicate_shared_descendant() -> None:
    # a -> c, b -> c — from 'a', c shows up exactly once
    pipeline = _pipeline(
        ["a", "b", "c"],
        [("a", "c", EdgeKind.VARIABLE), ("b", "c", EdgeKind.VARIABLE)],
    )
    result = analyze(pipeline, iterated_node_id="a")
    assert [n.node_id for n in result] == ["c"]
    assert result[0].distance == 1


def test_analyze_diamond_yields_unique_descendants_with_shortest_distance() -> None:
    #     a
    #    / \
    #   b   c
    #    \ /
    #     d
    pipeline = _pipeline(
        ["a", "b", "c", "d"],
        [
            ("a", "b", EdgeKind.VARIABLE),
            ("a", "c", EdgeKind.VARIABLE),
            ("b", "d", EdgeKind.VARIABLE),
            ("c", "d", EdgeKind.VARIABLE),
        ],
    )
    result = analyze(pipeline, iterated_node_id="a")
    assert [n.node_id for n in result] == ["b", "c", "d"]
    assert [n.distance for n in result] == [1, 1, 2]


def test_analyze_unknown_iterated_node_raises_value_error() -> None:
    pipeline = _pipeline(["a", "b"], [("a", "b", EdgeKind.VARIABLE)])
    with pytest.raises(ValueError, match="not in pipeline"):
        analyze(pipeline, iterated_node_id="ghost")


def test_analyze_empty_pipeline_raises_when_iterated_id_unknown() -> None:
    pipeline = _pipeline([], [])
    with pytest.raises(ValueError, match="not in pipeline"):
        analyze(pipeline, iterated_node_id="anything")


def test_analyze_single_node_pipeline_returns_empty() -> None:
    pipeline = _pipeline(["only"], [])
    assert analyze(pipeline, iterated_node_id="only") == []


def test_analyze_records_edge_kinds_along_shortest_path() -> None:
    # a --variable--> b --langchain_pipe--> c
    pipeline = _pipeline(
        ["a", "b", "c"],
        [
            ("a", "b", EdgeKind.VARIABLE),
            ("b", "c", EdgeKind.LANGCHAIN_PIPE),
        ],
    )
    result = analyze(pipeline, iterated_node_id="a")
    by_id = {n.node_id: n for n in result}
    assert set(by_id["b"].edge_kinds) == {EdgeKind.VARIABLE.value}
    # c's shortest path traverses both kinds — both should be present, deduped
    assert set(by_id["c"].edge_kinds) == {
        EdgeKind.VARIABLE.value,
        EdgeKind.LANGCHAIN_PIPE.value,
    }


def test_analyze_default_status_is_unverified() -> None:
    pipeline = _pipeline(
        ["a", "b"],
        [("a", "b", EdgeKind.VARIABLE)],
    )
    result = analyze(pipeline, iterated_node_id="a")
    assert result[0].status is DownstreamStatus.UNVERIFIED


def test_analyze_detects_cycle_and_raises() -> None:
    # Pipelines should be DAGs but if a buggy scanner ever produces a
    # cycle the analyzer must refuse rather than loop forever. We bypass
    # the helper because it relies on incoming/outgoing partitioning.
    nodes = [PipelineNode(prompt_id=n) for n in ("a", "b", "c")]
    edges = [
        PipelineEdge(source="a", target="b", kind=EdgeKind.VARIABLE),
        PipelineEdge(source="b", target="c", kind=EdgeKind.VARIABLE),
        PipelineEdge(source="c", target="a", kind=EdgeKind.VARIABLE),
    ]
    pipeline = Pipeline(
        id="cyc",
        name="cyc",
        nodes=nodes,
        edges=edges,
        entry_points=[],
        exit_points=[],
    )
    with pytest.raises(ValueError, match="cycle"):
        analyze(pipeline, iterated_node_id="a")


def test_analyze_merges_edge_kinds_from_all_equal_distance_shortest_paths() -> None:
    # Diamond with two distinct edge kinds on the equal-length branches:
    #
    #         a
    #        / \
    #  (var)/   \(langchain_pipe)
    #      v     v
    #      b     c
    #       \   /
    #   (var)\ /(var)
    #         v
    #         d
    #
    # Both A->B->D and A->C->D are shortest paths to D (distance 2).
    # Reviewer blocker B2: the analyzer must union edge kinds across
    # *all* equal-distance shortest paths, not pick whichever sibling
    # the BFS happened to drain first. Without the fix D.edge_kinds is
    # silently truncated to whichever branch won the race.
    pipeline = _pipeline(
        ["a", "b", "c", "d"],
        [
            ("a", "b", EdgeKind.VARIABLE),
            ("a", "c", EdgeKind.LANGCHAIN_PIPE),
            ("b", "d", EdgeKind.VARIABLE),
            ("c", "d", EdgeKind.VARIABLE),
        ],
    )
    result = analyze(pipeline, iterated_node_id="a")
    by_id = {n.node_id: n for n in result}
    assert set(by_id["d"].edge_kinds) == {
        EdgeKind.VARIABLE.value,
        EdgeKind.LANGCHAIN_PIPE.value,
    }


def test_analyze_handles_long_linear_chain_without_recursion() -> None:
    # Reviewer blocker B3: cycle detection used recursive DFS, which
    # raises ``RecursionError`` on chains longer than Python's default
    # ~1000-frame limit. A 2000-node linear chain is a perfectly valid
    # DAG and must not crash a defensive safety net. Iterative DFS
    # (explicit stack) keeps the same three-colour algorithm without
    # touching the call stack.
    n = 2000
    node_ids = [f"a_{i}" for i in range(n)]
    edges = [(node_ids[i], node_ids[i + 1], EdgeKind.VARIABLE) for i in range(n - 1)]
    pipeline = _pipeline(node_ids, edges)
    result = analyze(pipeline, iterated_node_id=node_ids[0])
    # Every node except the iterated source is downstream of a_0.
    assert len(result) == n - 1
    # Spot-check the BFS distance is the chain offset, not an off-by-one.
    assert result[0].node_id == node_ids[1]
    assert result[0].distance == 1
    assert result[-1].node_id == node_ids[-1]
    assert result[-1].distance == n - 1


def test_analyze_ignores_edges_to_nodes_outside_pipeline() -> None:
    # Defensive: a stray edge pointing at a non-existent node id must
    # not crash the walker. Treating the edge as inert is the safest
    # interpretation — the dangling reference is a scanner bug we
    # surface via warning channels, not by exploding here.
    nodes = [PipelineNode(prompt_id="a"), PipelineNode(prompt_id="b")]
    edges = [
        PipelineEdge(source="a", target="b", kind=EdgeKind.VARIABLE),
        PipelineEdge(source="b", target="ghost", kind=EdgeKind.VARIABLE),
    ]
    pipeline = Pipeline(
        id="p",
        name="p",
        nodes=nodes,
        edges=edges,
        entry_points=["a"],
        exit_points=[],
    )
    result = analyze(pipeline, iterated_node_id="a")
    assert [n.node_id for n in result] == ["b"]


# --------------------------------------------------------------------------- #
# assess_status                                                               #
# --------------------------------------------------------------------------- #


def test_assess_status_verified_within_epsilon() -> None:
    status = assess_status(
        pre_scores={"b": 0.80},
        post_scores={"b": 0.82},
        epsilon=0.02,
    )
    assert status == {"b": DownstreamStatus.VERIFIED}


def test_assess_status_verified_at_exact_epsilon_boundary() -> None:
    # Reviewer blocker B1: the docstring says ``|diff| <= epsilon`` lands
    # in VERIFIED, but the previous test was a lie — ``0.82 - 0.80``
    # rounds to ``0.01999...`` under IEEE 754 and never trips the
    # boundary. Build the diff from powers-of-two fractions that *are*
    # bit-exact (0.25 and 0.5 are representable without rounding), so
    # the ``diff == epsilon`` equality holds at the bit level and the
    # branch we actually want to pin gets exercised.
    diff = 0.5 - 0.25
    assert diff == 0.25  # sanity: bit-exact, not float noise
    status = assess_status(
        pre_scores={"b": 0.25},
        post_scores={"b": 0.5},
        epsilon=0.25,
    )
    assert status == {"b": DownstreamStatus.VERIFIED}


def test_assess_status_improved_just_above_epsilon() -> None:
    # One nudge past the boundary — diff is strictly greater than
    # epsilon, so the status flips to IMPROVED. Pairs with the boundary
    # test above to pin both sides of the inequality (``>`` vs ``==``).
    status = assess_status(
        pre_scores={"b": 0.25},
        post_scores={"b": 0.5 + 1e-9},
        epsilon=0.25,
    )
    assert status == {"b": DownstreamStatus.IMPROVED}


def test_assess_status_regressed_when_post_drops_more_than_epsilon() -> None:
    status = assess_status(
        pre_scores={"b": 0.80},
        post_scores={"b": 0.60},
        epsilon=0.02,
    )
    assert status == {"b": DownstreamStatus.REGRESSED}


def test_assess_status_improved_when_post_rises_more_than_epsilon() -> None:
    status = assess_status(
        pre_scores={"b": 0.80},
        post_scores={"b": 0.85},
        epsilon=0.02,
    )
    assert status == {"b": DownstreamStatus.IMPROVED}


def test_assess_status_missing_post_is_unverified() -> None:
    # If we have a pre score but no post (re-run not done yet for this node)
    # it stays "unverified" — that's exactly what the UI badge counts.
    status = assess_status(
        pre_scores={"b": 0.80, "c": 0.50},
        post_scores={"b": 0.81},
        epsilon=0.02,
    )
    assert status == {
        "b": DownstreamStatus.VERIFIED,
        "c": DownstreamStatus.UNVERIFIED,
    }


def test_assess_status_post_only_node_is_skipped() -> None:
    # post without a pre baseline doesn't yield a meaningful delta —
    # we don't invent an entry. assess_status keys on pre_scores.
    status = assess_status(
        pre_scores={"b": 0.80},
        post_scores={"b": 0.81, "stranger": 0.99},
        epsilon=0.02,
    )
    assert "stranger" not in status
    assert status["b"] is DownstreamStatus.VERIFIED


def test_assess_status_rejects_negative_epsilon() -> None:
    with pytest.raises(ValueError, match="epsilon"):
        assess_status(
            pre_scores={"b": 0.5},
            post_scores={"b": 0.5},
            epsilon=-0.01,
        )


# --------------------------------------------------------------------------- #
# serialize_status_for_iterations                                             #
# --------------------------------------------------------------------------- #


def test_serialize_status_round_trip_to_json_friendly_dict() -> None:
    nodes = [
        DownstreamNode(
            node_id="b",
            distance=1,
            edge_kinds=[EdgeKind.VARIABLE.value],
            status=DownstreamStatus.VERIFIED,
        ),
        DownstreamNode(
            node_id="c",
            distance=2,
            edge_kinds=[EdgeKind.VARIABLE.value, EdgeKind.LANGCHAIN_PIPE.value],
            status=DownstreamStatus.REGRESSED,
        ),
    ]
    serialized = serialize_status_for_iterations(nodes)
    # Pure str -> str mapping suitable for ``json.dumps``; matches the
    # shape Decision 5 specifies for ``iterations.downstream_status``.
    assert serialized == {"b": "verified", "c": "regressed"}
    assert all(isinstance(v, str) for v in serialized.values())


def test_serialize_status_empty_list_returns_empty_dict() -> None:
    assert serialize_status_for_iterations([]) == {}


def test_serialize_status_preserves_unverified_default() -> None:
    nodes = [DownstreamNode(node_id="b", distance=1, edge_kinds=[])]
    assert serialize_status_for_iterations(nodes) == {"b": "unverified"}
