"""``PipelineNode.kind = 'declared_llm'`` + ``EdgeKind.CREWAI`` contract
tests (B2-CrewAI, wt/scanner-crewai).

The B2-LG worktree added two values to ``PipelineNode.kind``: ``"llm"``
(default — a node backed by a concrete :class:`PromptSite` in the
user's code) and ``"non_llm"`` (a DAG step the framework declared but
that doesn't itself call an LLM — LangGraph's ``add_node("parse",
parse_json)`` shape).

CrewAI introduces a third concept the existing two values can't honestly
express: a node that **represents a real LLM call** (CrewAI's
runtime makes the agent execute it) but whose prompt **lives inside
the framework**, not in user code we can scan. Standing in front of a
CrewAI `Task(description="Research X", agent=researcher)` we know the
LLM will be called, but we can't link to a PromptSite because the user
never wrote one — the framework builds the prompt at runtime from the
agent's role/goal/backstory plus the task description.

Marking these as ``"non_llm"`` would mislead users into thinking they
don't cost tokens (they do). Marking them as ``"llm"`` would lie about
having a backing PromptSite and crash the playground runner the same
way mid-DAG ``non_llm`` nodes did pre-fix in B2-LG. So we add a third
value, ``"declared_llm"``, with its own UI treatment (solid green
border, distinct from llm's blue and non_llm's dashed grey).

``EdgeKind.CREWAI`` is the corresponding edge enum value for
``Task(context=[other_task])`` dependencies and ``Crew(tasks=[a, b, c],
process=Process.sequential)`` implicit ordering.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aitap.scanner.models import EdgeKind, PipelineNode


def test_pipeline_node_kind_accepts_declared_llm() -> None:
    """A CrewAI Task node is a real LLM call whose prompt the
    framework owns. ``kind="declared_llm"`` lets us record it
    honestly: yes it costs tokens (vs ``non_llm``), no we can't link
    to a backing PromptSite (vs ``llm``).
    """
    node = PipelineNode(prompt_id="crewai:app.py:crew:research", kind="declared_llm")
    assert node.kind == "declared_llm"


def test_pipeline_node_kind_still_rejects_unknown_literal() -> None:
    """The earlier 'reject typos' guarantee still holds with the new
    value — only the three named literals are valid.
    """
    with pytest.raises(ValidationError):
        PipelineNode(prompt_id="x", kind="autogen_llm")  # type: ignore[arg-type]


def test_pipeline_node_kind_round_trips_declared_llm_through_json() -> None:
    """JSON round-trip for the new value — necessary because the UI
    consumes nodes via the openapi-generated TS client."""
    node = PipelineNode(
        prompt_id="crewai:app.py:crew:write",
        kind="declared_llm",
        label="write blog post",
    )
    dumped = node.model_dump(mode="json")
    assert dumped["kind"] == "declared_llm"
    round_tripped = PipelineNode.model_validate(dumped)
    assert round_tripped == node


def test_edge_kind_crewai_enum_exists_and_serialises_to_lowercase() -> None:
    """CrewAI edges get their own EdgeKind value so the UI can render
    them distinctly. Lowercase value keeps the wire shape consistent
    with the other enum entries (``langgraph`` / ``llamaindex`` / …).
    """
    assert EdgeKind.CREWAI.value == "crewai"
