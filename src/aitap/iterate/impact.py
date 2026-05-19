"""Impact Analyzer for the iteration loop (Wave 4, Decision 4).

When the user iterates a single node inside a pipeline, every node that
*consumes* its output downstream is potentially affected: a tone tweak in
the ``outline`` step changes the input distribution feeding ``draft`` and
``polish``, even if their own templates didn't change.

This module turns that intuition into a small, pure-function data
contract that the rest of Wave 4 plugs into:

- :func:`analyze` walks the scanner's :class:`~aitap.scanner.models.Pipeline`
  DAG from the iterated node, BFS, and returns one
  :class:`DownstreamNode` per affected consumer with its hop distance and
  the kinds of dataflow edges traversed on the shortest path.
- :func:`assess_status` compares per-node weighted scores from before and
  after a downstream re-run and classifies each into the four states the
  banner / CLI surfaces (``verified`` / ``regressed`` / ``improved`` /
  ``unverified``).
- :func:`serialize_status_for_iterations` projects a list of
  :class:`DownstreamNode` into the ``{node_id: status}`` JSON shape
  persisted on the ``iterations.downstream_status`` column.

Design constraints worth flagging:

- **No LLM calls.** This file is the pure graph half of the loop; the
  expensive re-run / re-score happens in ``iterate/loop.py`` (separate
  worktree).
- **No DB writes.** Callers pass dictionaries in and get data back.
- **The :class:`Pipeline` contract is frozen** (``scanner/models.py``);
  we read it, we never mutate it.
- **Deterministic ordering.** Results are sorted by ``(distance, node_id)``
  so the banner and the persisted JSON are reproducible across runs.
"""

from __future__ import annotations

from collections.abc import Iterator
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from aitap.scanner.models import Pipeline


class DownstreamStatus(str, Enum):
    """Verification state of a single downstream consumer.

    The state machine matches the one in Decision 4 of the Wave 4 design
    doc — `unverified` is the post-iteration default, the user re-runs
    explicitly (or via ``--rerun-downstream``), and the comparison against
    the pre-iteration score moves the node into one of the terminal three
    states. ``improved`` is rare but tracked because a noticeable upstream
    rewrite can lift downstream quality and we want that visible in the
    history view, not silently smoothed into ``verified``.
    """

    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    REGRESSED = "regressed"
    IMPROVED = "improved"


class DownstreamNode(BaseModel):
    """One affected downstream consumer of an iterated node.

    ``edge_kinds`` is the *set* of edge kinds traversed on the shortest
    BFS path from the iterated node to this one — useful for the UI when
    deciding how prominently to flag the impact (a ``langchain_pipe`` is
    a hard wiring, an ``unresolved`` edge is a guess).
    """

    model_config = ConfigDict(frozen=True)

    node_id: str
    distance: int = Field(ge=1)
    edge_kinds: list[str] = Field(default_factory=list)
    status: DownstreamStatus = DownstreamStatus.UNVERIFIED


# --------------------------------------------------------------------------- #
# Graph helpers                                                               #
# --------------------------------------------------------------------------- #


def _build_adjacency(
    pipeline: Pipeline,
) -> tuple[dict[str, list[tuple[str, str]]], set[str]]:
    """Return forward-adjacency keyed by source plus the known node set.

    Each adjacency entry is ``(target_id, edge_kind_value)`` so the BFS
    can record edge kinds on the fly without a second lookup.

    Edges pointing at nodes that aren't part of ``pipeline.nodes`` are
    filtered out: the scanner's dedup/cycle detector is supposed to keep
    these consistent, but the analyzer being defensive here means a
    half-stale fixture or a buggy detector degrades to "ignore the
    dangling edge" rather than producing nonsense BFS frontiers.
    """
    node_ids: set[str] = {n.prompt_id for n in pipeline.nodes}
    adjacency: dict[str, list[tuple[str, str]]] = {nid: [] for nid in node_ids}
    for edge in pipeline.edges:
        if edge.source not in node_ids or edge.target not in node_ids:
            continue
        adjacency[edge.source].append((edge.target, edge.kind.value))
    return adjacency, node_ids


def _detect_cycle_reachable_from(
    start: str,
    adjacency: dict[str, list[tuple[str, str]]],
) -> bool:
    """DFS-flavoured cycle check over the sub-DAG reachable from ``start``.

    Only the reachable component matters for the analyzer's correctness:
    a cycle elsewhere in the graph can't affect BFS from ``start``, but a
    cycle on the path *would* loop forever if we trusted the input. The
    scanner already runs its own validator; this is a belt-and-braces
    fallback that converts the bug into a precise :class:`ValueError`.

    The traversal is intentionally **iterative** (explicit stack) rather
    than recursive: a perfectly valid 2000-node linear chain would blow
    Python's default 1000-frame recursion limit and turn a defensive
    safety net into a hard crash. Each stack frame carries the current
    node and a live ``Iterator`` over its remaining children so we
    resume the parent's loop after each child finishes — same
    three-colour (white/gray/black) algorithm as the recursive form,
    just hand-driven.
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {start: GRAY}
    stack: list[tuple[str, Iterator[tuple[str, str]]]] = [(start, iter(adjacency.get(start, [])))]

    while stack:
        node, neighbors = stack[-1]
        try:
            child, _kind = next(neighbors)
        except StopIteration:
            color[node] = BLACK
            stack.pop()
            continue
        child_color = color.get(child, WHITE)
        if child_color == GRAY:
            return True
        if child_color == BLACK:
            continue
        color[child] = GRAY
        stack.append((child, iter(adjacency.get(child, []))))

    return False


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #


def analyze(
    pipeline: Pipeline,
    iterated_node_id: str,
) -> list[DownstreamNode]:
    """Return all downstream consumers of ``iterated_node_id``.

    Algorithm: BFS from ``iterated_node_id`` over the pipeline's
    dataflow edges. The first time we reach a node, that's the shortest
    distance from the iterated node and the path's edge kinds are
    recorded on the resulting :class:`DownstreamNode`.

    Args:
        pipeline: The pipeline DAG to walk (immutable contract).
        iterated_node_id: ``prompt_id`` of the node the user just
            iterated. Must be present in ``pipeline.nodes``.

    Returns:
        A list of :class:`DownstreamNode`, sorted by ``(distance,
        node_id)`` so the output is deterministic and a stable diff for
        the persisted JSON column. The iterated node itself is excluded;
        callers asking "what was affected" don't want the source in
        their answer.

    Raises:
        ValueError: ``iterated_node_id`` is not in ``pipeline.nodes``,
            or the pipeline contains a cycle reachable from the iterated
            node. The scanner should never produce a cycle, but we
            refuse to silently loop if one slips through.
    """
    adjacency, node_ids = _build_adjacency(pipeline)
    if iterated_node_id not in node_ids:
        raise ValueError(
            f"iterated_node_id {iterated_node_id!r} is not in pipeline {pipeline.id!r}"
        )

    if _detect_cycle_reachable_from(iterated_node_id, adjacency):
        raise ValueError(
            f"pipeline {pipeline.id!r} contains a cycle reachable from "
            f"{iterated_node_id!r}; impact analysis aborted"
        )

    # Layered BFS: process the graph one distance ring at a time so that
    # when we expand a node's children we can union edge-kind
    # contributions from **all** equal-distance shortest paths that
    # arrived at that node, not just the first. Without this, a diamond
    # ``A -[variable]-> B -> D``, ``A -[langchain_pipe]-> C -> D``
    # would report ``D.edge_kinds = ["variable"]`` only (whichever
    # sibling drained the queue first), contradicting the module
    # docstring's "kinds of dataflow edges traversed on the shortest
    # path" contract for the consumer.
    #
    # ``merged_kinds`` is the source of truth for "kinds reachable to
    # this node along *some* shortest path"; we always extend children
    # from ``merged_kinds[parent]`` (fully unioned across the previous
    # ring) so downstream layers inherit kinds picked up via sibling
    # branches, not just the first-arrival branch. ``dict.fromkeys`` is
    # the codebase's existing ordered-set idiom; iteration order tracks
    # BFS discovery order, which gives a stable ``edge_kinds`` list
    # across runs without an explicit sort.
    distances: dict[str, int] = {iterated_node_id: 0}
    merged_kinds: dict[str, dict[str, None]] = {iterated_node_id: {}}
    current_ring: list[str] = [iterated_node_id]
    next_distance = 1

    while current_ring:
        next_ring: list[str] = []
        for parent in current_ring:
            parent_kinds = merged_kinds[parent]
            for target, edge_kind in adjacency.get(parent, []):
                existing_distance = distances.get(target)
                if existing_distance is None:
                    # First arrival — record shortest distance and seed
                    # kinds with the parent's union plus this edge.
                    distances[target] = next_distance
                    target_kinds: dict[str, None] = dict.fromkeys(parent_kinds)
                    target_kinds.setdefault(edge_kind, None)
                    merged_kinds[target] = target_kinds
                    next_ring.append(target)
                elif existing_distance == next_distance:
                    # Equal-distance alternate shortest path — union
                    # this branch's kinds into the existing bucket so
                    # the deeper ring inherits them too.
                    target_kinds = merged_kinds[target]
                    for kind in parent_kinds:
                        target_kinds.setdefault(kind, None)
                    target_kinds.setdefault(edge_kind, None)
                # else: existing_distance < next_distance — strictly
                # longer path through ``target``; skip. BFS guarantees
                # no shorter path will ever arrive after the first.
        current_ring = next_ring
        next_distance += 1

    # Sort by (distance, node_id) — distance dominates so the banner can
    # group "immediate downstream" first, node_id is the tie-breaker for
    # stable serialisation.
    nodes: list[DownstreamNode] = [
        DownstreamNode(
            node_id=nid,
            distance=distances[nid],
            edge_kinds=list(merged_kinds[nid]),
            status=DownstreamStatus.UNVERIFIED,
        )
        for nid in distances
        if nid != iterated_node_id
    ]
    return sorted(nodes, key=lambda n: (n.distance, n.node_id))


def assess_status(
    pre_scores: dict[str, float],
    post_scores: dict[str, float],
    *,
    epsilon: float = 0.02,
) -> dict[str, DownstreamStatus]:
    """Classify each pre-scored node by how much its post score moved.

    Compares ``post - pre`` per node:

    - ``|diff| <= epsilon`` → :attr:`DownstreamStatus.VERIFIED`
      (within noise, no surprise change)
    - ``diff > epsilon``  → :attr:`DownstreamStatus.IMPROVED`
    - ``diff < -epsilon`` → :attr:`DownstreamStatus.REGRESSED`
    - missing in ``post_scores`` → :attr:`DownstreamStatus.UNVERIFIED`
      (re-run for that node hasn't completed yet)

    ``epsilon`` defaults to the same 0.02 used in the convergence config
    (Decision 3) so a node whose score moved less than a stagnation step
    is considered visually unchanged.

    Args:
        pre_scores: Mapping ``node_id -> weighted_score`` from before the
            iteration (typically pulled from the previous iteration row).
        post_scores: Mapping ``node_id -> weighted_score`` after the
            re-run. May be a strict subset of ``pre_scores`` if only some
            downstream nodes were selected for re-run.
        epsilon: Non-negative threshold for "no meaningful change."

    Returns:
        Mapping ``node_id -> DownstreamStatus`` keyed by ``pre_scores``.
        Nodes present in ``post_scores`` but not in ``pre_scores`` are
        deliberately ignored — without a baseline the delta is
        meaningless and inventing a status would mislead the UI.

    Raises:
        ValueError: ``epsilon`` is negative. Zero is allowed (callers who
            want bit-exact reproducibility).
    """
    if epsilon < 0:
        raise ValueError(f"epsilon must be >= 0, got {epsilon!r}")

    result: dict[str, DownstreamStatus] = {}
    for node_id, pre in pre_scores.items():
        post = post_scores.get(node_id)
        if post is None:
            result[node_id] = DownstreamStatus.UNVERIFIED
            continue
        diff = post - pre
        # Branch on signed ``diff`` with strict ``>``/``<`` against
        # ``±epsilon`` so the boundary ``|diff| == epsilon`` falls into
        # VERIFIED, matching the docstring contract exactly. The earlier
        # ``abs(diff) <= epsilon`` form was equivalent on paper but
        # split the comparison across two predicates, which made the
        # boundary semantics easy to misread (and the corresponding
        # test name a lie — IEEE 754 means ``0.82 - 0.80`` is never
        # *exactly* 0.02). Keeping a single sign-aware ladder removes
        # the ambiguity.
        if diff > epsilon:
            result[node_id] = DownstreamStatus.IMPROVED
        elif diff < -epsilon:
            result[node_id] = DownstreamStatus.REGRESSED
        else:
            result[node_id] = DownstreamStatus.VERIFIED  # |diff| <= epsilon
    return result


def serialize_status_for_iterations(
    nodes: list[DownstreamNode],
) -> dict[str, str]:
    """Project to the JSON shape persisted on ``iterations.downstream_status``.

    The store layer (wt/iterations-store) holds this column as a TEXT
    JSON blob with the contract ``{node_id: status_string}``. Keeping the
    serialiser here means the loop worktree imports one function and
    doesn't need to know about :class:`DownstreamStatus` at all.

    The returned dict is ``str -> str`` (not ``DownstreamStatus``) so
    ``json.dumps`` produces ``"verified"``/``"regressed"`` straight from
    the values without going through pydantic's enum machinery.
    """
    return {node.node_id: node.status.value for node in nodes}


__all__ = [
    "DownstreamNode",
    "DownstreamStatus",
    "analyze",
    "assess_status",
    "serialize_status_for_iterations",
]
