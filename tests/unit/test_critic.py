"""Unit tests for :mod:`aitap.iterate.critic` — Wave 4 critique-and-revise.

The critic turns aggregated judge feedback + user thumbs/notes into a new
prompt template. It exposes three modes through a single entry point:

- ``auto``   — critic LLM rewrites the template freely.
- ``guided`` — critic LLM rewrites under a user-supplied instruction.
- ``manual`` — no LLM; user supplies the new template verbatim.

These tests pin the contract end-to-end with :class:`MockLLMClient`, never
talking to a real provider. The critic module returns a
:class:`RevisedPrompt` — it must **not** write to ``prompt_versions``
itself; that is the loop orchestrator's transaction boundary
(``wt/loop``).

Coverage matrix:

- manual mode does zero LLM work, echoes the supplied text, fixed rationale.
- auto mode round-trips a scripted critic reply (template + rationale).
- guided mode threads the user instruction into the critic prompt and the
  returned rationale.
- parameter validation: each mode raises on its missing inputs.
- the critic LLM call uses ``temperature=0`` (deterministic revise).
- malformed LLM JSON surfaces as a :class:`CriticError` so the loop knows.
- ``_aggregate_critique_for_llm`` picks the lowest-weighted dimension(s)
  first, drops empty critiques (judge failure sentinel), and tolerates an
  empty user-notes mapping.
- the module avoids any direct provider SDK import (asserted by reading
  the source file — defence in depth against accidental coupling).
- ``RevisedPrompt.mode`` mirrors the caller's mode argument exactly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aitap.deep.testing import MockLLMClient
from aitap.iterate.critic import (
    AggregatedFeedback,
    CriticError,
    RevisedPrompt,
    _aggregate_critique_for_llm,
    revise,
)
from aitap.iterate.judge_models import Dimension, JudgeScore
from aitap.scanner.models import (
    CallParameters,
    CodeLocation,
    Message,
    PromptSite,
    Provider,
    Role,
)

# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


def _prompt() -> PromptSite:
    return PromptSite(
        id="prompt-1",
        name="summarise_email",
        provider=Provider.OPENAI,
        location=CodeLocation(file="example.py", line_start=1, line_end=1),
        messages=[Message(role=Role.SYSTEM, template_text="You summarise emails.")],
        parameters=CallParameters(),
    )


def _dims() -> list[Dimension]:
    return [
        Dimension(name="accuracy", weight=0.4, rubric="acc rubric"),
        Dimension(name="relevance", weight=0.3, rubric="rel rubric"),
        Dimension(name="safety", weight=0.15, rubric="saf rubric"),
        Dimension(name="format", weight=0.15, rubric="fmt rubric"),
    ]


def _score(
    *,
    accuracy: float = 0.9,
    relevance: float = 0.9,
    safety: float = 1.0,
    format_: float = 1.0,
    critique: str = "good",
) -> JudgeScore:
    per_dim = {
        "accuracy": accuracy,
        "relevance": relevance,
        "safety": safety,
        "format": format_,
    }
    weighted = accuracy * 0.4 + relevance * 0.3 + safety * 0.15 + format_ * 0.15
    return JudgeScore(weighted_total=weighted, per_dim=per_dim, critique=critique)


def _feedback(
    scores: list[JudgeScore] | None = None,
    thumbs: dict[int, str] | None = None,
    notes: dict[int, str] | None = None,
) -> AggregatedFeedback:
    return AggregatedFeedback(
        judge_scores=scores
        if scores is not None
        else [_score(critique="case 0 ok"), _score(critique="case 1 ok")],
        user_thumbs=dict(thumbs) if thumbs else {},  # type: ignore[arg-type]
        user_notes=notes or {},
    )


def _critic_reply(*, template: str, rationale: str) -> str:
    return json.dumps({"revised_template": template, "rationale": rationale})


# --------------------------------------------------------------------------- #
# manual mode                                                                 #
# --------------------------------------------------------------------------- #


async def test_manual_mode_returns_user_text_without_llm_call() -> None:
    client = MockLLMClient(scripted=[])
    revised = await revise(
        prompt=_prompt(),
        current_template="OLD TEMPLATE",
        feedback=_feedback(),
        mode="manual",
        manual_text="NEW MANUAL TEMPLATE",
        client=client,  # ignored in manual mode
    )
    assert isinstance(revised, RevisedPrompt)
    assert revised.template_text == "NEW MANUAL TEMPLATE"
    assert revised.mode == "manual"
    assert revised.instruction is None
    assert revised.rationale == "manual edit by user"
    # Manual is a zero-LLM-call path — verify the spy never fired.
    assert client.calls == []


async def test_manual_mode_ignores_instruction_argument() -> None:
    revised = await revise(
        prompt=_prompt(),
        current_template="OLD",
        feedback=_feedback(),
        mode="manual",
        manual_text="NEW",
        instruction="this should be ignored",
    )
    assert revised.template_text == "NEW"
    assert revised.instruction is None


async def test_manual_mode_without_text_raises() -> None:
    with pytest.raises(ValueError, match="manual"):
        await revise(
            prompt=_prompt(),
            current_template="OLD",
            feedback=_feedback(),
            mode="manual",
        )


# --------------------------------------------------------------------------- #
# auto mode                                                                   #
# --------------------------------------------------------------------------- #


async def test_auto_mode_returns_parsed_revised_template() -> None:
    client = MockLLMClient(
        scripted=[
            _critic_reply(
                template="REWRITTEN PROMPT",
                rationale="Tightened factual constraints to lift accuracy.",
            )
        ]
    )
    revised = await revise(
        prompt=_prompt(),
        current_template="OLD TEMPLATE",
        feedback=_feedback(scores=[_score(accuracy=0.3, critique="hallucinated date")]),
        mode="auto",
        client=client,
        dimensions=_dims(),
    )
    assert revised.template_text == "REWRITTEN PROMPT"
    assert revised.mode == "auto"
    assert revised.instruction is None
    assert "factual" in revised.rationale.lower()
    assert len(client.calls) == 1


async def test_auto_mode_without_client_raises() -> None:
    with pytest.raises(ValueError, match="auto mode requires an LLMClient"):
        await revise(
            prompt=_prompt(),
            current_template="OLD",
            feedback=_feedback(),
            mode="auto",
        )


# --------------------------------------------------------------------------- #
# guided mode                                                                 #
# --------------------------------------------------------------------------- #


async def test_guided_mode_threads_instruction_into_prompt_and_rationale() -> None:
    client = MockLLMClient(
        scripted=[
            _critic_reply(
                template="MORE PROFESSIONAL PROMPT",
                rationale="Switched to formal register per user direction.",
            )
        ]
    )
    instruction = "make the tone more professional"
    revised = await revise(
        prompt=_prompt(),
        current_template="OLD TEMPLATE",
        feedback=_feedback(),
        mode="guided",
        client=client,
        instruction=instruction,
        dimensions=_dims(),
    )
    assert revised.template_text == "MORE PROFESSIONAL PROMPT"
    assert revised.mode == "guided"
    assert revised.instruction == instruction
    # The rationale composes the user's instruction with the critic's words.
    assert instruction in revised.rationale
    assert "formal register" in revised.rationale
    # And the user instruction is actually visible to the critic LLM.
    assert len(client.calls) == 1
    user_msg = client.calls[0].messages[-1].content
    assert instruction in user_msg


async def test_guided_mode_without_client_raises() -> None:
    with pytest.raises(ValueError, match="guided mode requires an LLMClient"):
        await revise(
            prompt=_prompt(),
            current_template="OLD",
            feedback=_feedback(),
            mode="guided",
            instruction="be concise",
        )


async def test_guided_mode_without_instruction_raises() -> None:
    client = MockLLMClient(scripted=[])
    with pytest.raises(ValueError, match="guided mode requires an instruction"):
        await revise(
            prompt=_prompt(),
            current_template="OLD",
            feedback=_feedback(),
            mode="guided",
            client=client,
        )


# --------------------------------------------------------------------------- #
# Determinism and prompt grounding                                            #
# --------------------------------------------------------------------------- #


async def test_critic_llm_call_uses_temperature_zero() -> None:
    client = MockLLMClient(scripted=[_critic_reply(template="X", rationale="Y")])
    await revise(
        prompt=_prompt(),
        current_template="OLD",
        feedback=_feedback(),
        mode="auto",
        client=client,
    )
    assert len(client.calls) == 1
    assert client.calls[0].temperature == 0.0


async def test_critic_user_prompt_contains_current_template() -> None:
    client = MockLLMClient(scripted=[_critic_reply(template="NEW", rationale="rationale")])
    await revise(
        prompt=_prompt(),
        current_template="OLD TEMPLATE CONTENT",
        feedback=_feedback(),
        mode="auto",
        client=client,
    )
    user_content = client.calls[0].messages[-1].content
    assert "OLD TEMPLATE CONTENT" in user_content


async def test_critic_user_prompt_contains_low_dim_critique() -> None:
    """Low-scoring dimension critique must reach the critic so it can target it."""
    client = MockLLMClient(scripted=[_critic_reply(template="NEW", rationale="r")])
    bad = _score(accuracy=0.2, critique="invented a name not in the source")
    good = _score(accuracy=0.95, critique="all good")
    await revise(
        prompt=_prompt(),
        current_template="OLD",
        feedback=_feedback(scores=[bad, good]),
        mode="auto",
        client=client,
        dimensions=_dims(),
    )
    user_content = client.calls[0].messages[-1].content
    assert "invented a name not in the source" in user_content


async def test_critic_user_prompt_includes_user_notes() -> None:
    client = MockLLMClient(scripted=[_critic_reply(template="NEW", rationale="r")])
    notes = {0: "the second sentence was too casual"}
    await revise(
        prompt=_prompt(),
        current_template="OLD",
        feedback=_feedback(notes=notes),
        mode="auto",
        client=client,
    )
    user_content = client.calls[0].messages[-1].content
    assert "the second sentence was too casual" in user_content


# --------------------------------------------------------------------------- #
# Failure surface                                                             #
# --------------------------------------------------------------------------- #


async def test_critic_unparseable_reply_raises_critic_error() -> None:
    client = MockLLMClient(scripted=["this is not json at all"])
    with pytest.raises(CriticError):
        await revise(
            prompt=_prompt(),
            current_template="OLD",
            feedback=_feedback(),
            mode="auto",
            client=client,
        )


async def test_critic_missing_template_field_raises() -> None:
    client = MockLLMClient(scripted=[json.dumps({"rationale": "no template here"})])
    with pytest.raises(CriticError):
        await revise(
            prompt=_prompt(),
            current_template="OLD",
            feedback=_feedback(),
            mode="auto",
            client=client,
        )


# --------------------------------------------------------------------------- #
# _aggregate_critique_for_llm                                                 #
# --------------------------------------------------------------------------- #


def test_aggregate_picks_lowest_weighted_dimension_first() -> None:
    """The aggregator must surface the weakest dim so the critic targets it."""
    dims = _dims()
    scores = [
        # accuracy is the lowest-scoring dim across the cases.
        _score(accuracy=0.2, relevance=0.9, critique="bad accuracy"),
        _score(accuracy=0.3, relevance=0.95, critique="also bad accuracy"),
    ]
    feedback = AggregatedFeedback(
        judge_scores=scores,
        user_thumbs={},
        user_notes={},
    )
    text = _aggregate_critique_for_llm(feedback, dims)
    # Accuracy named explicitly as the weakest axis.
    assert "accuracy" in text.lower()
    # At least one case critique surfaces verbatim so the LLM has grounding.
    assert "bad accuracy" in text or "also bad accuracy" in text


def test_aggregate_skips_empty_critique_cases() -> None:
    """Empty critique signals judge failure (wt/judge sentinel) — skip it."""
    dims = _dims()
    scores = [
        _score(critique=""),  # judge failure
        _score(accuracy=0.4, critique="real critique surviving"),
    ]
    feedback = AggregatedFeedback(
        judge_scores=scores,
        user_thumbs={},
        user_notes={},
    )
    text = _aggregate_critique_for_llm(feedback, dims)
    assert "real critique surviving" in text


def test_aggregate_with_no_notes_and_no_thumbs_does_not_crash() -> None:
    dims = _dims()
    feedback = AggregatedFeedback(
        judge_scores=[_score()],
        user_thumbs={},
        user_notes={},
    )
    text = _aggregate_critique_for_llm(feedback, dims)
    # Defensive: result is a string we can hand to a prompt builder.
    assert isinstance(text, str)
    assert text.strip() != ""


def test_aggregate_surfaces_user_thumbs_down() -> None:
    dims = _dims()
    feedback = AggregatedFeedback(
        judge_scores=[_score(), _score()],
        user_thumbs={1: "down"},
        user_notes={1: "this case is wrong"},
    )
    text = _aggregate_critique_for_llm(feedback, dims)
    # Thumbs-down case index appears and the note travels along.
    assert "this case is wrong" in text


def test_aggregate_all_empty_critiques_still_returns_text() -> None:
    """All judge failures + no user notes: aggregator still returns something."""
    dims = _dims()
    feedback = AggregatedFeedback(
        judge_scores=[_score(critique=""), _score(critique="")],
        user_thumbs={},
        user_notes={},
    )
    text = _aggregate_critique_for_llm(feedback, dims)
    assert isinstance(text, str)


# --------------------------------------------------------------------------- #
# Structural guarantees                                                       #
# --------------------------------------------------------------------------- #


def test_critic_module_does_not_import_provider_sdks() -> None:
    """Defence in depth: critic must talk to LLMs only via LLMClient."""
    here = Path(__file__).resolve().parents[2]
    critic_src = (here / "src" / "aitap" / "iterate" / "critic.py").read_text(encoding="utf-8")
    assert "import openai" not in critic_src
    assert "import anthropic" not in critic_src
    assert "from openai" not in critic_src
    assert "from anthropic" not in critic_src


def test_revised_prompt_mode_field_is_literal() -> None:
    # Round-trip a manual RevisedPrompt to make sure pydantic accepts the
    # three valid mode strings.
    for m in ("auto", "guided", "manual"):
        rp = RevisedPrompt(
            template_text="x",
            mode=m,  # type: ignore[arg-type]
            instruction=None,
            rationale="r",
        )
        assert rp.mode == m
