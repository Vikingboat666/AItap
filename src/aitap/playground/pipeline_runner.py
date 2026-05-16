"""Pipeline-level playground runner.

Three modes from the product plan map to three call shapes:

- ``node``: run **one** node inside a pipeline. Useful when the user
  isolates a single step in the UI graph and wants to iterate on just
  that prompt. Delegates straight to :func:`runner.run_prompt`.
- ``segment``: run a contiguous **slice** of node ids. Outputs of each
  node feed downstream via the dataflow edges captured by the scanner.
  Useful for "I trust the first two steps, let me re-run from step 3."
- ``end_to_end``: feed ``case.inputs`` at every entry point, walk the
  full DAG in topological order, and record **every** intermediate
  output to ``RunOutput.intermediate`` so the UI can render the per-node
  trace.

This module reads but never mutates the :class:`Pipeline` contract; the
scanner owns that data and we treat it as immutable input.

Topological order:
    We use Kahn's algorithm (BFS-style) rather than DFS so a cycle is
    detected immediately as "nodes left over with non-zero in-degree."
    The scanner is supposed to produce DAGs, but treating cycles as a
    *runtime* error here means a buggy upstream change degrades to a
    clean ValueError instead of an infinite loop.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Literal

from aitap.deep.client import LLMClient
from aitap.playground.runner import (
    PromptRunMetrics,
    PromptRunResult,
    run_prompt,
)
from aitap.scanner.models import CallParameters, Pipeline, PipelineEdge, PromptSite
from aitap.server.routes import DatasetCase, RunOutput

PipelineMode = Literal["node", "segment", "end_to_end"]


@dataclass(frozen=True)
class PipelineRunResult:
    """Outputs + aggregated metrics for a pipeline-level run.

    For ``node`` mode, ``outputs`` is the single node's run (one entry
    per case). For ``segment`` and ``end_to_end``, ``outputs`` has one
    entry per case, and each entry's ``intermediate`` map captures every
    visited node's text output keyed by ``prompt_id``.
    """

    outputs: list[RunOutput]
    metrics: PromptRunMetrics


def _build_indexes(
    pipeline: Pipeline,
) -> tuple[
    dict[str, list[str]],  # adjacency: source_id -> [target_id, ...]
    dict[str, list[str]],  # reverse adjacency: target_id -> [source_id, ...]
    dict[str, list[PipelineEdge]],  # incoming edges keyed by target_id
]:
    """Pre-compute O(1) graph lookups from the edge list.

    The scanner emits edges as a flat list, which is the right wire
    format but the wrong shape for a walker. Building these once per
    pipeline run is cheaper than re-scanning the edge list on every
    node visit, and keeping all three lookups together makes the topo
    walk below trivially readable.
    """
    adjacency: dict[str, list[str]] = {}
    reverse_adjacency: dict[str, list[str]] = {}
    incoming_edges: dict[str, list[PipelineEdge]] = {}

    for node in pipeline.nodes:
        adjacency.setdefault(node.prompt_id, [])
        reverse_adjacency.setdefault(node.prompt_id, [])
        incoming_edges.setdefault(node.prompt_id, [])

    for edge in pipeline.edges:
        adjacency.setdefault(edge.source, []).append(edge.target)
        reverse_adjacency.setdefault(edge.target, []).append(edge.source)
        incoming_edges.setdefault(edge.target, []).append(edge)

    return adjacency, reverse_adjacency, incoming_edges


def _topological_order(
    nodes: list[str],
    adjacency: dict[str, list[str]],
    reverse_adjacency: dict[str, list[str]],
) -> list[str]:
    """Kahn's algorithm restricted to the given node subset.

    ``nodes`` lets the caller restrict the walk to a segment without
    rebuilding the adjacency dict — edges that point outside the set
    are simply skipped in the in-degree count.
    """
    node_set = set(nodes)
    in_degree: dict[str, int] = {
        node: sum(1 for source in reverse_adjacency.get(node, []) if source in node_set)
        for node in nodes
    }
    queue: deque[str] = deque(node for node in nodes if in_degree[node] == 0)
    ordered: list[str] = []
    while queue:
        current = queue.popleft()
        ordered.append(current)
        for target in adjacency.get(current, []):
            if target not in node_set:
                continue
            in_degree[target] -= 1
            if in_degree[target] == 0:
                queue.append(target)
    if len(ordered) != len(nodes):
        # Cycle or dangling reference — surface it loudly. See module
        # docstring for why we treat this as a runtime error.
        raise ValueError(
            "pipeline contains a cycle or unreachable node in the requested subset; "
            f"ordered={ordered!r}, requested={nodes!r}"
        )
    return ordered


def _site_for(node_id: str, site_index: dict[str, PromptSite]) -> PromptSite:
    """Look up a node's PromptSite, with a precise error when missing.

    A missing entry usually means the caller forgot to include a
    transitive dependency in ``site_index`` — pointing at the offending
    id saves a chase through the call stack.
    """
    try:
        return site_index[node_id]
    except KeyError as exc:
        raise KeyError(f"site_index is missing PromptSite for node '{node_id}'") from exc


def _inputs_for_node(
    node_id: str,
    case: DatasetCase,
    upstream_outputs: dict[str, str],
    incoming_edges: dict[str, list[PipelineEdge]],
    entry_points: set[str],
) -> dict[str, object]:
    """Compose the inputs dict to feed a node.

    For entry points we hand the raw ``case.inputs`` straight in. For
    downstream nodes, each incoming edge contributes one slot in the
    inputs dict — keyed by the edge's ``via`` name (the variable that
    carries the upstream output) when present, falling back to the
    upstream node id. The raw ``case.inputs`` is also merged so a node
    can still reference top-level case fields if it wants to (later
    edges win on key collision — explicit dataflow trumps case-wide
    defaults).
    """
    composed: dict[str, object] = dict(case.inputs)
    for edge in incoming_edges.get(node_id, []):
        source_output = upstream_outputs.get(edge.source)
        if source_output is None:
            continue
        slot = edge.via or edge.source
        composed[slot] = source_output
    if node_id in entry_points:
        # Entry points get raw case inputs unmodified; merging above is a
        # no-op for them (incoming_edges is empty), so this branch is
        # only for clarity — it's the contract we document.
        return composed
    return composed


async def _run_single_case_segment(
    *,
    case_index: int,
    case: DatasetCase,
    ordered_nodes: list[str],
    site_index: dict[str, PromptSite],
    incoming_edges: dict[str, list[PipelineEdge]],
    entry_points: set[str],
    client: LLMClient,
    parameters: CallParameters,
    version: int,
) -> tuple[RunOutput, PromptRunMetrics]:
    """Walk the topo order for one case, threading outputs through edges.

    Returns one RunOutput (with the terminal node's text in ``text`` and
    every visited node's output in ``intermediate``) and the rolled-up
    metrics for that case so the caller can sum across cases.
    """
    node_outputs: dict[str, str] = {}
    total_input = 0
    total_output = 0
    total_cost = 0.0
    last_text: str | None = None
    last_error: str | None = None

    for node_id in ordered_nodes:
        site = _site_for(node_id, site_index)
        node_inputs = _inputs_for_node(
            node_id=node_id,
            case=case,
            upstream_outputs=node_outputs,
            incoming_edges=incoming_edges,
            entry_points=entry_points,
        )
        # Each node sees the case as a single-case batch; pipeline-level
        # fan-out over the *case list* happens one level up via gather.
        node_result = await run_prompt(
            site=site,
            version=version,
            dataset_cases=[DatasetCase(inputs=node_inputs)],
            client=client,
            parameters=parameters,
        )
        single_output = node_result.outputs[0]
        total_input += node_result.metrics.total_input_tokens
        total_output += node_result.metrics.total_output_tokens
        total_cost += node_result.metrics.total_cost_usd

        if single_output.error is not None:
            # Short-circuit on first node failure — downstream nodes
            # would only run on a missing input and produce garbage.
            last_error = f"node '{node_id}': {single_output.error}"
            break

        text = single_output.text or ""
        node_outputs[node_id] = text
        last_text = text

    return (
        RunOutput(
            case_index=case_index,
            text=last_text,
            error=last_error,
            intermediate=dict(node_outputs) if node_outputs else None,
        ),
        PromptRunMetrics(
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            total_cost_usd=total_cost,
        ),
    )


async def _run_segment_or_e2e(
    *,
    pipeline: Pipeline,
    node_ids: list[str],
    dataset_cases: list[DatasetCase],
    site_index: dict[str, PromptSite],
    client: LLMClient,
    parameters: CallParameters,
    version: int,
) -> PipelineRunResult:
    """Shared body for ``segment`` and ``end_to_end`` modes.

    The only difference between the two is *which* node ids are
    considered the entry points. ``end_to_end`` uses
    ``pipeline.entry_points`` (the scanner-marked sources); ``segment``
    treats the first nodes of the requested subset as entry points so
    they receive the raw case inputs without trying to read from
    upstream edges that aren't in scope.
    """
    adjacency, reverse_adjacency, incoming_edges = _build_indexes(pipeline)
    ordered_nodes = _topological_order(node_ids, adjacency, reverse_adjacency)

    node_set = set(node_ids)
    if set(pipeline.entry_points) & node_set:
        entry_points = {node_id for node_id in pipeline.entry_points if node_id in node_set}
    else:
        # Segment mode: nodes with no in-scope predecessor act as entry
        # points and consume the raw case inputs.
        entry_points = {
            node_id
            for node_id in node_ids
            if not any(source in node_set for source in reverse_adjacency.get(node_id, []))
        }

    case_results = await asyncio.gather(
        *[
            _run_single_case_segment(
                case_index=index,
                case=case,
                ordered_nodes=ordered_nodes,
                site_index=site_index,
                incoming_edges=incoming_edges,
                entry_points=entry_points,
                client=client,
                parameters=parameters,
                version=version,
            )
            for index, case in enumerate(dataset_cases)
        ]
    )

    outputs: list[RunOutput] = []
    total_input = 0
    total_output = 0
    total_cost = 0.0
    for run_output, metrics in case_results:
        outputs.append(run_output)
        total_input += metrics.total_input_tokens
        total_output += metrics.total_output_tokens
        total_cost += metrics.total_cost_usd

    return PipelineRunResult(
        outputs=outputs,
        metrics=PromptRunMetrics(
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            total_cost_usd=total_cost,
        ),
    )


async def run_pipeline(
    pipeline: Pipeline,
    mode: PipelineMode,
    *,
    dataset_cases: list[DatasetCase],
    site_index: dict[str, PromptSite],
    client: LLMClient,
    parameters: CallParameters,
    version: int = 1,
    node_id: str | None = None,
    segment: list[str] | None = None,
) -> PipelineRunResult:
    """Run ``pipeline`` in one of the three supported modes.

    Args:
        pipeline: Scanned pipeline DAG (immutable contract).
        mode: ``"node"`` | ``"segment"`` | ``"end_to_end"``.
        dataset_cases: Pipeline-level inputs (one per case).
        site_index: Lookup ``prompt_id -> PromptSite`` for every node the
            walk might visit. The API layer assembles this from the
            store; tests build it inline.
        client: Provider client used for every node's chat call.
        parameters: Call-time knobs forwarded to each node.
        version: Logical prompt version recorded against the run. Forwarded
            to :func:`run_prompt` unchanged.
        node_id: Required for ``mode="node"``. Must be a node in the pipeline.
        segment: Required for ``mode="segment"``. Subset of node ids; the
            walk topologically orders them and pipes outputs through.

    Raises:
        ValueError: ``mode`` is unknown, or required selector is missing,
            or ``segment`` includes ids not in the pipeline.
    """
    if mode == "node":
        if node_id is None:
            raise ValueError("mode='node' requires node_id=")
        if not any(n.prompt_id == node_id for n in pipeline.nodes):
            raise ValueError(f"node '{node_id}' is not part of pipeline '{pipeline.id}'")
        site = _site_for(node_id, site_index)
        prompt_result: PromptRunResult = await run_prompt(
            site=site,
            version=version,
            dataset_cases=dataset_cases,
            client=client,
            parameters=parameters,
        )
        # For node mode we drop responses but keep cost/usage; the
        # outputs are 1:1 with cases and have no intermediates.
        return PipelineRunResult(
            outputs=prompt_result.outputs,
            metrics=prompt_result.metrics,
        )

    if mode == "segment":
        if not segment:
            raise ValueError("mode='segment' requires a non-empty segment=")
        node_ids_in_pipeline = {n.prompt_id for n in pipeline.nodes}
        unknown = [n for n in segment if n not in node_ids_in_pipeline]
        if unknown:
            raise ValueError(
                f"segment references nodes not in pipeline '{pipeline.id}': {unknown!r}"
            )
        return await _run_segment_or_e2e(
            pipeline=pipeline,
            node_ids=list(segment),
            dataset_cases=dataset_cases,
            site_index=site_index,
            client=client,
            parameters=parameters,
            version=version,
        )

    if mode == "end_to_end":
        all_node_ids = [n.prompt_id for n in pipeline.nodes]
        return await _run_segment_or_e2e(
            pipeline=pipeline,
            node_ids=all_node_ids,
            dataset_cases=dataset_cases,
            site_index=site_index,
            client=client,
            parameters=parameters,
            version=version,
        )

    raise ValueError(f"unknown pipeline mode: {mode!r}")
