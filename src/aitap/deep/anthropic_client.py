"""Anthropic Messages API binding for :class:`LLMClient`.

Lazy-imports the ``anthropic`` SDK inside method bodies so installing
``aitap`` without the ``[anthropic]`` extra still works — the registry
fails informatively only when the user actually tries to *use* anthropic.
"""

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
# pyright: reportUnknownVariableType=false, reportAttributeAccessIssue=false
# These are inherent to using a lazy-imported optional SDK whose type stubs
# aren't available at module-load time.

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Literal

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

if TYPE_CHECKING:
    pass


class AnthropicClient(LLMClient):
    """Anthropic Messages API client. Async via the SDK's ``AsyncAnthropic``."""

    @property
    def provider_name(self) -> str:
        return "anthropic"

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        response_format: Literal["text", "json"] | None = None,
    ) -> ChatResponse:
        AsyncAnthropic, sdk_errors = _import_sdk()
        client = AsyncAnthropic(api_key=self._resolve_api_key())

        system, anth_messages = _split_system(messages)

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": anth_messages,
            "max_tokens": max_tokens or 1024,
        }
        if system:
            kwargs["system"] = system
        if temperature is not None:
            kwargs["temperature"] = temperature
        if top_p is not None:
            kwargs["top_p"] = top_p
        # Anthropic doesn't have an OpenAI-style response_format flag; the
        # closest approximation is to wrap the system prompt with "respond
        # in JSON". We don't auto-do that — caller's responsibility.
        if response_format == "json":
            # Make it visible rather than a silent no-op.
            kwargs["metadata"] = {"aitap_response_format_request": "json"}

        try:
            raw = await client.messages.create(**kwargs)
        except sdk_errors.AuthenticationError as exc:
            raise ProviderAuthError(str(exc)) from exc
        except sdk_errors.RateLimitError as exc:
            raise ProviderRateLimitError(str(exc)) from exc
        except sdk_errors.APIError as exc:
            raise ProviderError(f"anthropic API error: {exc}") from exc

        text = _extract_text(raw)
        usage = TokenUsage(
            input_tokens=raw.usage.input_tokens,
            output_tokens=raw.usage.output_tokens,
        )
        cost = _safe_cost(self.model, usage)
        finish = _map_stop_reason(raw.stop_reason)

        return ChatResponse(
            text=text,
            model=self.model,
            usage=usage,
            cost_usd=cost,
            raw={"id": raw.id, "stop_reason": raw.stop_reason or ""},
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
        key = self.api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ProviderAuthError("ANTHROPIC_API_KEY not set; pass api_key= or set the env var")
        return key


# --------------------------------------------------------------------------- #
# Helpers (module-private)                                                    #
# --------------------------------------------------------------------------- #


def _import_sdk():
    """Lazy import of the anthropic SDK. Raises ProviderError if missing."""
    try:
        import anthropic  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ProviderError(
            "anthropic SDK not installed; install with: pip install 'aitap[anthropic]'"
        ) from exc
    return anthropic.AsyncAnthropic, anthropic


def _split_system(messages: list[ChatMessage]) -> tuple[str | None, list[dict[str, str]]]:
    """Anthropic puts ``system`` outside the message list; split it out."""
    sys_parts: list[str] = []
    body: list[dict[str, str]] = []
    for m in messages:
        if m.role == "system":
            sys_parts.append(m.content)
        elif m.role in ("user", "assistant"):
            body.append({"role": m.role, "content": m.content})
        # tool messages are not supported here; the test scope is text I/O.
    system = "\n\n".join(sys_parts) if sys_parts else None
    return system, body


def _extract_text(raw: object) -> str:
    """Pull the assistant text out of Anthropic's content-block list."""
    chunks: list[str] = []
    for block in getattr(raw, "content", []) or []:
        # Anthropic returns content blocks with type="text"; defensive in
        # case the SDK adds new block types — we only know how to read text.
        block_type = getattr(block, "type", None)
        if block_type == "text":
            chunks.append(getattr(block, "text", ""))
    return "".join(chunks)


_FINISH_MAP: dict[str, str] = {
    "end_turn": "stop",
    "max_tokens": "length",
    "stop_sequence": "stop",
    "tool_use": "tool_use",
}


def _map_stop_reason(
    stop: str | None,
) -> Literal["stop", "length", "tool_use", "content_filter", "error"]:
    mapped = _FINISH_MAP.get(stop or "", "stop")
    # Type assertion via the literal map keys is sound — every value above
    # is one of the literal options on ChatResponse.finish_reason.
    return mapped  # type: ignore[return-value]


def _estimate_input_tokens(messages: list[ChatMessage]) -> int:
    """Cheap heuristic: ~4 characters per token. Good enough for ±20% cost
    estimates which is the bar that "show cost before spending" needs.

    Real provider tokenisers exist (anthropic.count_tokens / tiktoken) but
    they require a network call (Anthropic) or a heavy import (tiktoken)
    — the cost-gate calls happen in the CLI startup path so we keep them
    cheap and offline. Worst case the user sees +/-20% and confirms anyway.
    """
    chars = sum(len(m.content) for m in messages)
    return max(1, chars // 4)


def _safe_cost(model: str, usage: TokenUsage) -> float:
    return _safe_cost_from_tokens(model, usage.input_tokens, usage.output_tokens)


def _safe_cost_from_tokens(model: str, input_tokens: int, output_tokens: int) -> float:
    try:
        return estimate_usd(
            "anthropic", model, input_tokens=input_tokens, output_tokens=output_tokens
        )
    except UnknownModelError:
        # Don't crash — return 0 from this internal helper but the public
        # estimate_cost / chat consumers should already have validated the
        # model. The pricing-test ensures every supported model is priced.
        return 0.0


register_provider("anthropic", lambda model, key: AnthropicClient(model, key))
