"""Mock :class:`LLMClient` for tests in this and downstream worktrees.

Lives under ``src/aitap/deep/`` (not ``tests/``) so other modules' test
suites can import it without depending on the test layout. The mock
records every chat call, hands back scripted responses in order, and
reports a deterministic cost so coverage stays meaningful even offline.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Literal

from aitap.deep.client import (
    ChatMessage,
    ChatResponse,
    CostEstimate,
    LLMClient,
    TokenUsage,
)


@dataclass
class _Call:
    messages: list[ChatMessage]
    temperature: float | None
    max_tokens: int | None
    top_p: float | None
    response_format: Literal["text", "json"] | None


@dataclass
class MockLLMClient(LLMClient):
    """In-memory LLMClient that returns scripted responses.

    Instantiate with ``MockLLMClient(scripted=["first reply", "second"])``
    and chat() returns them in order. After the script runs out, falls
    back to ``default_reply`` (defaults to empty string) so tests don't
    crash on extra calls — but the recorded ``calls`` list lets the test
    assert the expected count.
    """

    model: str = "mock-model"
    api_key: str | None = "mock-key"
    scripted: list[str] = field(default_factory=list)
    default_reply: str = ""
    calls: list[_Call] = field(default_factory=list)
    _iter: Iterator[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._iter = iter(self.scripted)

    @property
    def provider_name(self) -> str:
        return "mock"

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        response_format: Literal["text", "json"] | None = None,
    ) -> ChatResponse:
        self.calls.append(
            _Call(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                top_p=top_p,
                response_format=response_format,
            )
        )
        try:
            text = next(self._iter)
        except StopIteration:
            text = self.default_reply
        return ChatResponse(
            text=text,
            model=self.model,
            usage=TokenUsage(input_tokens=10, output_tokens=10),
            cost_usd=0.0001,
            raw={"mock": "true"},
            finish_reason="stop",
        )

    def estimate_cost(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int | None = None,
    ) -> CostEstimate:
        chars = sum(len(m.content) for m in messages)
        in_tokens = max(1, chars // 4)
        out_tokens = max_tokens if max_tokens is not None else 512
        return CostEstimate(
            input_tokens=in_tokens,
            estimated_output_tokens=out_tokens,
            usd=0.0001 * (in_tokens + out_tokens),
            model=self.model,
        )

    def reset(self) -> None:
        self.calls.clear()
        self._iter = iter(self.scripted)
