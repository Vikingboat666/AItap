"""Unit tests for the prompt-level playground runner.

Covers:
    * ``run_prompt`` fans cases out concurrently via ``asyncio.gather``
      (each MockLLMClient.chat call records the input messages, so we can
      both verify ordering and assert that one call ran per case).
    * Template variables in PromptSite.messages get filled from each
      case's ``inputs`` — so the LLM actually sees per-case prompts and
      not the raw template.
    * Cost + token usage roll up correctly across all cases.
    * A failing case captures its exception into ``RunOutput.error``
      without aborting the rest of the batch.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Literal

import pytest

from aitap.deep.client import ChatMessage, ChatResponse, CostEstimate, LLMClient, TokenUsage
from aitap.deep.testing import MockLLMClient
from aitap.playground.runner import run_prompt
from aitap.scanner.models import (
    CallParameters,
    CodeLocation,
    Confidence,
    Message,
    PromptSite,
    Provider,
    Role,
    TemplateKind,
    TemplateVariable,
)
from aitap.server.routes import DatasetCase


def _make_site(template_text: str = "Summarize: {topic}") -> PromptSite:
    """Build a minimal PromptSite with a single user message."""
    return PromptSite(
        id="prompt-1",
        name="summarize_topic",
        provider=Provider.ANTHROPIC,
        location=CodeLocation(file="app.py", line_start=10, line_end=12),
        messages=[
            Message(
                role=Role.USER,
                template_text=template_text,
                template_kind=TemplateKind.FSTRING,
                variables=[TemplateVariable(name="topic")],
            )
        ],
        confidence=Confidence.HIGH,
    )


# --------------------------------------------------------------------------- #
# happy paths                                                                 #
# --------------------------------------------------------------------------- #


async def test_run_prompt_returns_one_output_per_case() -> None:
    site = _make_site()
    client = MockLLMClient(scripted=["alpha summary", "beta summary"])
    cases = [
        DatasetCase(inputs={"topic": "alpha"}),
        DatasetCase(inputs={"topic": "beta"}),
    ]

    result = await run_prompt(
        site=site,
        version=1,
        dataset_cases=cases,
        client=client,
        parameters=CallParameters(temperature=0.2, max_tokens=128),
    )

    assert len(result.outputs) == 2
    assert result.outputs[0].case_index == 0
    assert result.outputs[1].case_index == 1
    assert result.outputs[0].text == "alpha summary"
    assert result.outputs[1].text == "beta summary"
    assert all(o.error is None for o in result.outputs)


async def test_run_prompt_renders_template_variables() -> None:
    """Each case's inputs are substituted into the user message before
    the LLM sees it — not the raw template."""
    site = _make_site("Translate '{phrase}' to {lang}.")
    client = MockLLMClient(scripted=["bonjour", "hola"])
    cases = [
        DatasetCase(inputs={"phrase": "hello", "lang": "French"}),
        DatasetCase(inputs={"phrase": "hello", "lang": "Spanish"}),
    ]

    await run_prompt(
        site=site,
        version=1,
        dataset_cases=cases,
        client=client,
        parameters=CallParameters(),
    )

    # MockLLMClient records every chat() invocation, including the
    # rendered message contents — exactly the surface we want to assert.
    assert len(client.calls) == 2
    rendered = [call.messages[0].content for call in client.calls]
    assert "Translate 'hello' to French." in rendered
    assert "Translate 'hello' to Spanish." in rendered


async def test_run_prompt_aggregates_cost_and_usage() -> None:
    """Every case contributes 10 input + 10 output tokens and $0.0001
    per the MockLLMClient defaults, so three cases roll up to 30/30 and
    $0.0003."""
    site = _make_site()
    client = MockLLMClient(scripted=["a", "b", "c"])
    cases = [DatasetCase(inputs={"topic": f"t{i}"}) for i in range(3)]

    result = await run_prompt(
        site=site,
        version=1,
        dataset_cases=cases,
        client=client,
        parameters=CallParameters(),
    )

    assert result.metrics.total_input_tokens == 30
    assert result.metrics.total_output_tokens == 30
    # Float comparison tolerates the $0.0001-per-call arithmetic drift.
    assert result.metrics.total_cost_usd == pytest.approx(0.0003, rel=1e-6)
    assert result.usage.input_tokens == 30
    assert result.usage.output_tokens == 30
    assert result.total_cost_usd == pytest.approx(0.0003, rel=1e-6)


async def test_run_prompt_handles_empty_case_list() -> None:
    """No cases -> no outputs, no cost. The function must not raise."""
    result = await run_prompt(
        site=_make_site(),
        version=1,
        dataset_cases=[],
        client=MockLLMClient(),
        parameters=CallParameters(),
    )
    assert result.outputs == []
    assert result.metrics.total_cost_usd == 0.0
    assert result.metrics.total_input_tokens == 0


# --------------------------------------------------------------------------- #
# error capture                                                               #
# --------------------------------------------------------------------------- #


class _FlakyClient(MockLLMClient):
    """LLMClient that raises on the case_index-th call.

    Inheriting from MockLLMClient gives us the recording machinery for
    free; we only override ``chat`` to inject the planned failure.
    """

    def __init__(self, fail_at: int, replies: list[str]) -> None:
        super().__init__(scripted=replies)
        self._fail_at = fail_at
        self._counter = 0

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        response_format: Literal["text", "json"] | None = None,
    ) -> ChatResponse:
        index = self._counter
        self._counter += 1
        if index == self._fail_at:
            raise RuntimeError("simulated provider 500")
        return await super().chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            response_format=response_format,
        )


async def test_run_prompt_captures_per_case_error() -> None:
    """A single case raising must not abort the rest of the batch — its
    error is recorded on its RunOutput and other cases still complete."""
    site = _make_site()
    client = _FlakyClient(fail_at=1, replies=["ok-0", "ok-2"])
    cases = [DatasetCase(inputs={"topic": f"t{i}"}) for i in range(3)]

    result = await run_prompt(
        site=site,
        version=1,
        dataset_cases=cases,
        client=client,
        parameters=CallParameters(),
    )

    assert result.outputs[0].error is None
    assert result.outputs[0].text == "ok-0"
    assert result.outputs[1].error is not None
    assert "simulated provider 500" in result.outputs[1].error
    assert result.outputs[1].text is None
    assert result.outputs[2].error is None
    # The failing case contributed nothing to the cost roll-up.
    assert result.metrics.total_input_tokens == 20
    assert result.metrics.total_output_tokens == 20


# --------------------------------------------------------------------------- #
# concurrency                                                                 #
# --------------------------------------------------------------------------- #


async def test_run_prompt_dispatches_concurrently() -> None:
    """``asyncio.gather`` should overlap the per-case calls — if cases
    were awaited sequentially, the second call would never see the first
    case still in flight.

    We assert this by counting in-flight calls inside a custom client
    that increments a counter on entry and decrements on exit; the peak
    must equal the case count (not 1).
    """
    import asyncio as _asyncio

    class _ConcurrencyTrackingClient(LLMClient):
        def __init__(self) -> None:
            super().__init__(model="track")
            self.in_flight = 0
            self.peak_in_flight = 0
            self._lock = _asyncio.Lock()

        @property
        def provider_name(self) -> str:
            return "track"

        async def chat(
            self,
            messages: list[ChatMessage],
            *,
            temperature: float | None = None,
            max_tokens: int | None = None,
            top_p: float | None = None,
            response_format: Literal["text", "json"] | None = None,
        ) -> ChatResponse:
            async with self._lock:
                self.in_flight += 1
                self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
            try:
                # Yield to the event loop so siblings can ramp up before
                # we tear ourselves down — otherwise a too-fast coroutine
                # could finish before any peer enters the critical section.
                await _asyncio.sleep(0.01)
                return ChatResponse(
                    text="ok",
                    model=self.model,
                    usage=TokenUsage(input_tokens=1, output_tokens=1),
                    cost_usd=0.0,
                )
            finally:
                async with self._lock:
                    self.in_flight -= 1

        def estimate_cost(
            self,
            messages: list[ChatMessage],
            *,
            max_tokens: int | None = None,
        ) -> CostEstimate:
            return CostEstimate(
                input_tokens=1, estimated_output_tokens=1, usd=0.0, model=self.model
            )

    client = _ConcurrencyTrackingClient()
    site = _make_site()
    cases = [DatasetCase(inputs={"topic": f"t{i}"}) for i in range(5)]

    coroutine: Awaitable[object] = run_prompt(
        site=site,
        version=1,
        dataset_cases=cases,
        client=client,
        parameters=CallParameters(),
    )
    await coroutine

    assert client.peak_in_flight >= 2, (
        "run_prompt is awaiting cases sequentially — fix it to use asyncio.gather"
    )
