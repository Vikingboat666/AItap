"""OpenAI Chat Completions binding for :class:`LLMClient`.

Lazy-imports the ``openai`` SDK; uses the >= 1.30 client surface.
"""

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
# pyright: reportUnknownVariableType=false, reportAttributeAccessIssue=false
# Lazy-imported optional SDK — type stubs aren't visible at module load.

from __future__ import annotations

import os
from typing import Any, Literal

from aitap.deep.client import (
    ChatMessage,
    ChatResponse,
    CostEstimate,
    LLMClient,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    TokenUsage,
    register_provider,
)
from aitap.deep.pricing import UnknownModelError, estimate_usd


class OpenAIClient(LLMClient):
    @property
    def provider_name(self) -> str:
        return "openai"

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        response_format: Literal["text", "json"] | None = None,
    ) -> ChatResponse:
        AsyncOpenAI, sdk_errors = _import_sdk()
        client = AsyncOpenAI(api_key=self._resolve_api_key())

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if top_p is not None:
            kwargs["top_p"] = top_p
        if response_format == "json":
            kwargs["response_format"] = {"type": "json_object"}

        try:
            raw = await client.chat.completions.create(**kwargs)
        except sdk_errors.AuthenticationError as exc:
            raise ProviderAuthError(str(exc)) from exc
        except sdk_errors.RateLimitError as exc:
            raise ProviderRateLimitError(str(exc)) from exc
        except sdk_errors.APIError as exc:
            raise ProviderError(f"openai API error: {exc}") from exc

        choice = raw.choices[0]
        text = (choice.message.content or "") if choice.message else ""
        usage = TokenUsage(
            input_tokens=raw.usage.prompt_tokens if raw.usage else 0,
            output_tokens=raw.usage.completion_tokens if raw.usage else 0,
        )
        cost = _safe_cost(self.model, usage)
        finish = _map_finish(getattr(choice, "finish_reason", None))

        return ChatResponse(
            text=text,
            model=self.model,
            usage=usage,
            cost_usd=cost,
            raw={"id": raw.id, "finish_reason": choice.finish_reason or ""},
            finish_reason=finish,
        )

    def estimate_cost(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int | None = None,
    ) -> CostEstimate:
        in_tokens = _estimate_input_tokens(messages)
        out_tokens = max_tokens if max_tokens is not None else 512
        usd = _safe_cost_from_tokens(self.model, in_tokens, out_tokens)
        return CostEstimate(
            input_tokens=in_tokens,
            estimated_output_tokens=out_tokens,
            usd=usd,
            model=self.model,
        )

    def _resolve_api_key(self) -> str:
        key = self.api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ProviderAuthError("OPENAI_API_KEY not set; pass api_key= or set the env var")
        return key


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _import_sdk() -> tuple[Any, Any]:
    """Lazy import of the openai SDK. Same Any-typed pattern as
    anthropic_client._import_sdk — keeps pyright strict on Python 3.10
    happy when the optional [openai] extra isn't installed."""
    try:
        import openai  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ProviderError(
            "openai SDK not installed; install with: pip install 'aitap[openai]'"
        ) from exc
    return openai.AsyncOpenAI, openai


_FINISH_MAP: dict[str, str] = {
    "stop": "stop",
    "length": "length",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "content_filter",
}


def _map_finish(
    reason: str | None,
) -> Literal["stop", "length", "tool_use", "content_filter", "error"]:
    return _FINISH_MAP.get(reason or "stop", "stop")  # type: ignore[return-value]


def _estimate_input_tokens(messages: list[ChatMessage]) -> int:
    chars = sum(len(m.content) for m in messages)
    return max(1, chars // 4)


def _safe_cost(model: str, usage: TokenUsage) -> float:
    return _safe_cost_from_tokens(model, usage.input_tokens, usage.output_tokens)


def _safe_cost_from_tokens(model: str, input_tokens: int, output_tokens: int) -> float:
    try:
        return estimate_usd("openai", model, input_tokens=input_tokens, output_tokens=output_tokens)
    except UnknownModelError:
        return 0.0


register_provider("openai", lambda model, key: OpenAIClient(model, key))
