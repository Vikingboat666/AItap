"""Unit tests for the pipeline-level playground runner.

Fixture: a 3-node DAG modelling a classic RAG-style chain
    retrieve -> rerank -> summarize

with edges that carry data via named variables:
    retrieve --[docs]--> rerank --[top_docs]--> summarize

Each test exercises one of the three supported modes:
    * ``node``: runs only the middle node in isolation
    * ``segment``: runs the first two nodes (retrieve + rerank) and pipes
      the data through, but doesn't visit summarize
    * ``end_to_end``: walks the whole DAG; every node's text shows up in
      the per-case ``intermediate`` map.
"""

from __future__ import annotations

import pytest

from aitap.deep.testing import MockLLMClient
from aitap.playground.pipeline_runner import run_pipeline
from aitap.scanner.models import (
    CallParameters,
    CodeLocation,
    Confidence,
    EdgeKind,
    Message,
    Pipeline,
    PipelineEdge,
    PipelineNode,
    PromptSite,
    Provider,
    Role,
    TemplateKind,
    TemplateVariable,
)
from aitap.server.routes import DatasetCase


def _site(prompt_id: str, template_text: str, variables: list[str]) -> PromptSite:
    return PromptSite(
        id=prompt_id,
        name=prompt_id,
        provider=Provider.OPENAI,
        location=CodeLocation(file="chain.py", line_start=1, line_end=2),
        messages=[
            Message(
                role=Role.USER,
                template_text=template_text,
                template_kind=TemplateKind.FSTRING,
                variables=[TemplateVariable(name=v) for v in variables],
            )
        ],
        confidence=Confidence.HIGH,
    )


@pytest.fixture()
def three_node_pipeline() -> tuple[Pipeline, dict[str, PromptSite]]:
    """retrieve -> rerank -> summarize, with named edges.

    The ``via`` slot on each edge is what the downstream node's template
    expects in its inputs dict — so ``retrieve``'s output flows in as
    ``{docs}`` for ``rerank``, etc.
    """
    site_index: dict[str, PromptSite] = {
        "retrieve": _site("retrieve", "Find docs for: {query}", ["query"]),
        "rerank": _site("rerank", "Rerank these docs: {docs}", ["docs"]),
        "summarize": _site("summarize", "Summarize: {top_docs}", ["top_docs"]),
    }
    pipeline = Pipeline(
        id="rag-chain",
        name="rag_chain",
        nodes=[
            PipelineNode(prompt_id="retrieve"),
            PipelineNode(prompt_id="rerank"),
            PipelineNode(prompt_id="summarize"),
        ],
        edges=[
            PipelineEdge(
                source="retrieve",
                target="rerank",
                kind=EdgeKind.VARIABLE,
                via="docs",
            ),
            PipelineEdge(
                source="rerank",
                target="summarize",
                kind=EdgeKind.VARIABLE,
                via="top_docs",
            ),
        ],
        entry_points=["retrieve"],
        exit_points=["summarize"],
    )
    return pipeline, site_index


# --------------------------------------------------------------------------- #
# node mode                                                                   #
# --------------------------------------------------------------------------- #


async def test_node_mode_runs_only_selected_node(
    three_node_pipeline: tuple[Pipeline, dict[str, PromptSite]],
) -> None:
    pipeline, site_index = three_node_pipeline
    client = MockLLMClient(scripted=["rerank says: A,B,C"])
    cases = [DatasetCase(inputs={"docs": "doc-A doc-B doc-C"})]

    result = await run_pipeline(
        pipeline=pipeline,
        mode="node",
        dataset_cases=cases,
        site_index=site_index,
        client=client,
        parameters=CallParameters(),
        node_id="rerank",
    )

    assert len(result.outputs) == 1
    assert result.outputs[0].text == "rerank says: A,B,C"
    # node mode does not populate intermediates — it's a single-node call.
    assert result.outputs[0].intermediate is None
    # Exactly one chat call was issued (rerank, not retrieve or summarize).
    assert len(client.calls) == 1
    assert "Rerank these docs: doc-A doc-B doc-C" in client.calls[0].messages[0].content


async def test_node_mode_rejects_missing_node_id(
    three_node_pipeline: tuple[Pipeline, dict[str, PromptSite]],
) -> None:
    pipeline, site_index = three_node_pipeline
    with pytest.raises(ValueError, match="node_id"):
        await run_pipeline(
            pipeline=pipeline,
            mode="node",
            dataset_cases=[DatasetCase(inputs={})],
            site_index=site_index,
            client=MockLLMClient(),
            parameters=CallParameters(),
        )


async def test_node_mode_rejects_unknown_node(
    three_node_pipeline: tuple[Pipeline, dict[str, PromptSite]],
) -> None:
    pipeline, site_index = three_node_pipeline
    with pytest.raises(ValueError, match="not part of pipeline"):
        await run_pipeline(
            pipeline=pipeline,
            mode="node",
            dataset_cases=[DatasetCase(inputs={})],
            site_index=site_index,
            client=MockLLMClient(),
            parameters=CallParameters(),
            node_id="not-a-real-node",
        )


# --------------------------------------------------------------------------- #
# segment mode                                                                #
# --------------------------------------------------------------------------- #


async def test_segment_mode_threads_outputs(
    three_node_pipeline: tuple[Pipeline, dict[str, PromptSite]],
) -> None:
    """retrieve -> rerank, but not summarize. The intermediate map must
    contain both visited nodes, and rerank's input must include
    retrieve's output threaded via the ``docs`` edge."""
    pipeline, site_index = three_node_pipeline
    client = MockLLMClient(scripted=["retrieved-docs", "reranked-docs"])
    cases = [DatasetCase(inputs={"query": "weather in Paris"})]

    result = await run_pipeline(
        pipeline=pipeline,
        mode="segment",
        dataset_cases=cases,
        site_index=site_index,
        client=client,
        parameters=CallParameters(),
        segment=["retrieve", "rerank"],
    )

    assert len(result.outputs) == 1
    out = result.outputs[0]
    assert out.error is None
    # The segment's terminal node is rerank, so text reflects that node.
    assert out.text == "reranked-docs"
    # Both visited nodes appear in intermediates.
    assert out.intermediate == {
        "retrieve": "retrieved-docs",
        "rerank": "reranked-docs",
    }
    # The second LLM call was made for rerank with retrieve's output
    # threaded in as ``docs``.
    assert len(client.calls) == 2
    rerank_message = client.calls[1].messages[0].content
    assert "retrieved-docs" in rerank_message


async def test_segment_mode_rejects_unknown_node(
    three_node_pipeline: tuple[Pipeline, dict[str, PromptSite]],
) -> None:
    pipeline, site_index = three_node_pipeline
    with pytest.raises(ValueError, match="nodes not in pipeline"):
        await run_pipeline(
            pipeline=pipeline,
            mode="segment",
            dataset_cases=[DatasetCase(inputs={})],
            site_index=site_index,
            client=MockLLMClient(),
            parameters=CallParameters(),
            segment=["retrieve", "ghost-node"],
        )


# --------------------------------------------------------------------------- #
# end_to_end mode                                                             #
# --------------------------------------------------------------------------- #


async def test_end_to_end_mode_captures_all_intermediates(
    three_node_pipeline: tuple[Pipeline, dict[str, PromptSite]],
) -> None:
    """Feed inputs at the entry point, walk the whole DAG, every node's
    output shows up in the intermediates map and the final node's text
    is in ``output.text``."""
    pipeline, site_index = three_node_pipeline
    client = MockLLMClient(
        scripted=["docs-1", "ranked-1", "summary-1", "docs-2", "ranked-2", "summary-2"]
    )
    cases = [
        DatasetCase(inputs={"query": "first"}),
        DatasetCase(inputs={"query": "second"}),
    ]

    result = await run_pipeline(
        pipeline=pipeline,
        mode="end_to_end",
        dataset_cases=cases,
        site_index=site_index,
        client=client,
        parameters=CallParameters(),
    )

    assert len(result.outputs) == 2
    # The MockLLMClient hands out scripted replies in the order chat() is
    # called; with two cases run via asyncio.gather the *interleaving* of
    # cases is non-deterministic, but within each case the node order
    # (retrieve -> rerank -> summarize) is guaranteed. So we only assert
    # shape, not specific text content.
    for output in result.outputs:
        assert output.error is None
        assert output.intermediate is not None
        assert set(output.intermediate.keys()) == {"retrieve", "rerank", "summarize"}
        # The terminal node's text becomes the case's top-level text.
        assert output.text == output.intermediate["summarize"]

    # 3 nodes * 2 cases = 6 total chat invocations.
    assert len(client.calls) == 6


async def test_end_to_end_mode_aggregates_cost(
    three_node_pipeline: tuple[Pipeline, dict[str, PromptSite]],
) -> None:
    """3 nodes * 2 cases * $0.0001 per call = $0.0006."""
    pipeline, site_index = three_node_pipeline
    client = MockLLMClient(default_reply="ok")
    cases = [DatasetCase(inputs={"query": f"q{i}"}) for i in range(2)]

    result = await run_pipeline(
        pipeline=pipeline,
        mode="end_to_end",
        dataset_cases=cases,
        site_index=site_index,
        client=client,
        parameters=CallParameters(),
    )

    assert result.metrics.total_cost_usd == pytest.approx(0.0006, rel=1e-6)
    # 6 calls * 10 in + 10 out per call.
    assert result.metrics.total_input_tokens == 60
    assert result.metrics.total_output_tokens == 60


async def test_end_to_end_short_circuits_on_node_failure(
    three_node_pipeline: tuple[Pipeline, dict[str, PromptSite]],
) -> None:
    """If a node errors, downstream nodes for that case shouldn't run."""
    from typing import Literal

    from aitap.deep.client import ChatMessage, ChatResponse

    class _RerankBreaksClient(MockLLMClient):
        async def chat(
            self,
            messages: list[ChatMessage],
            *,
            temperature: float | None = None,
            max_tokens: int | None = None,
            top_p: float | None = None,
            response_format: Literal["text", "json"] | None = None,
        ) -> ChatResponse:
            text = messages[0].content
            if "Rerank" in text:
                raise RuntimeError("rerank model down")
            return await super().chat(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                top_p=top_p,
                response_format=response_format,
            )

    pipeline, site_index = three_node_pipeline
    client = _RerankBreaksClient(default_reply="docs-ok")
    cases = [DatasetCase(inputs={"query": "x"})]

    result = await run_pipeline(
        pipeline=pipeline,
        mode="end_to_end",
        dataset_cases=cases,
        site_index=site_index,
        client=client,
        parameters=CallParameters(),
    )

    output = result.outputs[0]
    assert output.error is not None
    assert "rerank model down" in output.error
    # Only retrieve made it; summarize was skipped because rerank failed.
    assert output.intermediate is not None
    assert "retrieve" in output.intermediate
    assert "summarize" not in output.intermediate


# --------------------------------------------------------------------------- #
# unknown mode                                                                #
# --------------------------------------------------------------------------- #


async def test_unknown_mode_raises(
    three_node_pipeline: tuple[Pipeline, dict[str, PromptSite]],
) -> None:
    pipeline, site_index = three_node_pipeline
    with pytest.raises(ValueError, match="unknown pipeline mode"):
        await run_pipeline(
            pipeline=pipeline,
            mode="not-a-mode",  # type: ignore[arg-type]
            dataset_cases=[],
            site_index=site_index,
            client=MockLLMClient(),
            parameters=CallParameters(),
        )
