"""LLMClient contract.

Contract version: 1 (2026-05-09)

Provider-agnostic abstraction shared by:

- L2 deep scanner (`deep/wrapper_detector.py`, `cross_file_resolver.py`,
  `purpose_inferer.py`)
- Test case expansion (`dataset/llm_expander.py`)
- LLM-as-judge (`iterate/judge.py`)
- Critique-and-revise (`iterate/critic.py`)

Each concrete provider lives next to this file (e.g., `anthropic_client.py`)
and registers itself via `register_provider()`. Lazy imports keep the SDK
deps optional — installing aitap without `[anthropic]` extra still works
as long as you don't try to use Anthropic.

Example consumer:

    from aitap.deep.client import get_client
    client = get_client(provider="anthropic", model="claude-sonnet-4-6")
    estimate = client.estimate_cost([{"role": "user", "content": "hi"}])
    if estimate.usd > 0.05:
        confirm_with_user(estimate)
    response = await client.chat(messages, max_tokens=512)
"""

from __future__ import annotations

import abc
from collections.abc import Callable
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    """A single message in a chat-style request."""

    model_config = ConfigDict(frozen=True)

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: str | None = None


class CostEstimate(BaseModel):
    """Predicted cost of a chat call before execution."""

    model_config = ConfigDict(frozen=True)

    input_tokens: int
    estimated_output_tokens: int
    usd: float
    model: str


class TokenUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int
    output_tokens: int


class ChatResponse(BaseModel):
    """Result of a chat call."""

    model_config = ConfigDict(frozen=True)

    text: str
    model: str
    usage: TokenUsage
    cost_usd: float
    raw: dict[str, object] = Field(default_factory=dict)
    finish_reason: Literal["stop", "length", "tool_use", "content_filter", "error"] = "stop"


class LLMClient(abc.ABC):
    """Provider-agnostic chat client.

    Implementations should:
    - Be safe to construct without making any network calls.
    - Validate API key availability lazily (at first chat/estimate call).
    - Wrap provider-specific errors in a `ProviderError` subclass.
    """

    def __init__(self, model: str, api_key: str | None = None) -> None:
        self.model = model
        self.api_key = api_key

    @property
    @abc.abstractmethod
    def provider_name(self) -> str:
        """Stable identifier (e.g., 'anthropic', 'openai')."""

    @abc.abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
        response_format: Literal["text", "json"] | None = None,
    ) -> ChatResponse:
        """Send a chat request, return the assistant response."""

    @abc.abstractmethod
    def estimate_cost(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int | None = None,
    ) -> CostEstimate:
        """Estimate USD cost of `chat(messages)` without sending the request."""


class ProviderError(Exception):
    """Base class for provider-side failures (auth, rate limit, transport)."""


class ProviderAuthError(ProviderError):
    """Missing or invalid API key."""


class ProviderRateLimitError(ProviderError):
    """Provider returned 429 / rate limit."""


# ----- Provider registry -----

ClientFactory = Callable[[str, str | None], LLMClient]


class _ProviderProtocol(Protocol):  # pyright: ignore[reportUnusedClass]
    """Marker protocol — kept here so tests can introspect the registry."""

    def __call__(self, model: str, api_key: str | None) -> LLMClient: ...


_REGISTRY: dict[str, ClientFactory] = {}


def register_provider(name: str, factory: ClientFactory) -> None:
    """Register a provider factory under a canonical name.

    Concrete provider modules call this at import time, e.g.:

        # in aitap/deep/anthropic_client.py
        register_provider("anthropic", lambda model, key: AnthropicClient(model, key))
    """
    _REGISTRY[name] = factory


def list_providers() -> list[str]:
    return sorted(_REGISTRY)


def get_client(provider: str, model: str, api_key: str | None = None) -> LLMClient:
    """Get an LLMClient for the named provider.

    Triggers a lazy import of the provider module if not yet registered.
    """
    if provider not in _REGISTRY:
        # Lazy import to populate the registry on first request.
        # Each provider module registers itself at import time.
        try:
            __import__(f"aitap.deep.{provider}_client")
        except ImportError as e:
            raise ProviderError(
                f"Provider '{provider}' not available. "
                f"Install with: pip install 'aitap[{provider}]'"
            ) from e

    if provider not in _REGISTRY:
        raise ProviderError(f"Provider '{provider}' did not register itself on import")

    return _REGISTRY[provider](model, api_key)
