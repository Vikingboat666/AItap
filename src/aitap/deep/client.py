"""LLMClient contract.

Contract version: 2 (2026-06-14) — A2-P3 deleted the legacy provider-keyed
registry (``ClientFactory`` / ``_REGISTRY`` / ``register_provider`` /
``get_client`` / ``list_providers``) along with
``RunCreate.provider`` / ``RunCreate.model``. The only remaining public
surface here is the :class:`LLMClient` ABC plus its message / response /
error types — concrete clients are constructed via
:mod:`aitap.deep.factory` keyed on
:class:`~aitap.config.ProfileConfig` (or its API-layer twin
:class:`~aitap.server.routes.Profile`). See ``docs/profiles-design.md``
for the redesign rationale.

Example consumer:

    from aitap.deep.factory import get_client_for_profile_config
    from aitap.secrets import get_key_for_profile
    client = get_client_for_profile_config(profile, get_key_for_profile(profile.id))
    estimate = client.estimate_cost([{"role": "user", "content": "hi"}])
    if estimate.usd > 0.05:
        confirm_with_user(estimate)
    response = await client.chat(messages, max_tokens=512)
"""

from __future__ import annotations

import abc
from typing import Literal

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


# The legacy provider-keyed registry (``ClientFactory`` / ``_REGISTRY`` /
# ``register_provider`` / ``list_providers`` / ``get_client``) was
# removed in contract v2 (A2-P3). Concrete clients are now constructed
# via :mod:`aitap.deep.factory` keyed on
# :class:`~aitap.config.ProfileConfig`; new callers should use
# :func:`~aitap.deep.factory.get_client_for_profile_config` (or the
# API-twin :func:`~aitap.deep.factory.get_client_for_profile`).
