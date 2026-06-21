"""Pipeline-node kind enum contract tests (B2-LG, wt/scanner-langgraph).

Contract addition: ``PipelineNode.kind: Literal["llm", "non_llm"] = "llm"``.

The LangGraph detector emits nodes for every step the DAG declares —
including non-LLM helper functions (``parse_json``, tool calls, etc.).
The contract distinguishes them so the UI can render non-LLM nodes
differently (dashed border / dimmer color) while keeping a single
node-list shape. Default is ``"llm"`` so every existing pre-LangGraph
node keeps its current semantics; existing fixtures don't need to
change.

These tests pin the additive contract before the detector lands so
the field name / default / round-trip are locked.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aitap.scanner.models import EdgeKind, PipelineNode


def test_pipeline_node_kind_defaults_to_llm() -> None:
    """Existing fixtures construct PipelineNode without ``kind`` — the
    default ``"llm"`` preserves their semantics byte-for-byte.
    """
    node = PipelineNode(prompt_id="prompt-x")
    assert node.kind == "llm"


def test_pipeline_node_kind_accepts_non_llm() -> None:
    """LangGraph's ``add_node("parse", parse_json)`` produces a node
    whose body has no PromptSite — we still record it so the DAG
    topology is complete; ``kind="non_llm"`` lets the UI render it
    distinctly.
    """
    node = PipelineNode(prompt_id="step-parse", kind="non_llm")
    assert node.kind == "non_llm"
    assert node.prompt_id == "step-parse"


def test_pipeline_node_kind_round_trips_through_json() -> None:
    """The wire shape (Pydantic ``model_dump`` → ``model_validate``)
    preserves ``kind`` so the openapi-generated TS client sees it.
    """
    node = PipelineNode(prompt_id="x", kind="non_llm", label="parse JSON")
    dumped = node.model_dump(mode="json")
    assert dumped["kind"] == "non_llm"
    assert dumped["label"] == "parse JSON"
    round_tripped = PipelineNode.model_validate(dumped)
    assert round_tripped == node


def test_pipeline_node_kind_rejects_unknown_literal() -> None:
    """Pydantic guards the wire surface — a typo (or an attacker
    crafting a payload) can't slip in a third value that the UI
    doesn't know how to render.
    """
    with pytest.raises(ValidationError):
        PipelineNode(prompt_id="x", kind="dynamic")  # type: ignore[arg-type]


def test_edge_kind_langgraph_enum_exists_and_serialises_to_lowercase() -> None:
    """LangGraph DAG edges get a dedicated ``EdgeKind`` value so the UI
    can render them as solid lines tagged with a LangGraph badge
    (separate from LCEL's ``lc_pipe`` and LlamaIndex's ``llamaindex``
    edges). Lowercase value keeps the wire shape consistent with the
    other enum entries.
    """
    assert EdgeKind.LANGGRAPH.value == "langgraph"
    # The enum is a ``str``-subclass so JSON serialisation is just the
    # string value — matches what the openapi schema declares.
    assert str(EdgeKind.LANGGRAPH.value) == "langgraph"
