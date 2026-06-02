"""OpenAI Chat Completions binding for :class:`LLMClient`.

Lazy-imports the ``openai`` SDK; uses the >= 1.30 client surface.
"""

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
# pyright: reportUnknownVariableType=false, reportAttributeAccessIssue=false
# Lazy-imported optional SDK — type stubs aren't visible at module load.

from __future__ import annotations

from typing import Any, Literal

from aitap import secrets as _secrets
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
        # Single-owner key reads — see aitap.secrets. A vault key wins
        # over an env var so an in-UI update reaches the SDK immediately.
        key = self.api_key or _secrets.get_key("openai")
        if not key:
            raise ProviderAuthError(
                "No OpenAI API key set. Add one in aitap ui → Settings, "
                "or export OPENAI_API_KEY in your shell."
            )
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


# --------------------------------------------------------------------------- #
# OpenAICompatClient — multi-provider redesign (wt/profile-client)            #
# --------------------------------------------------------------------------- #
#
# Lives alongside :class:`OpenAIClient` rather than replacing it because the
# legacy provider-keyed surface (PR #35's ``POST /api/settings/key`` family,
# ``aitap.playground.dispatch``'s ``get_client("openai", ...)`` registry hit)
# still depends on the older zero-arg-base_url shape. wt/profile-cleanup
# retires the legacy half once the migration completes. See
# ``docs/profiles-design.md`` §"Backend architecture / LLM client construction"
# for the staged rollout.


class OpenAICompatClient(LLMClient):
    """Speaks the OpenAI chat-completions wire protocol against any host.

    The multi-provider redesign rests on the observation that almost every
    serious third-party LLM endpoint (DeepSeek, Moonshot/Kimi, MiMo, Groq,
    Together, SiliconFlow, Qwen DashScope, Ollama, LM Studio, ...) speaks
    the same chat-completions JSON OpenAI does. Pointing the SDK at a
    different ``base_url`` is the only thing that changes. This client
    captures that: ``base_url`` is **mandatory**, ``api_key`` is
    **mandatory**, and the rest of the surface (request shaping, error
    mapping, finish-reason translation, cost estimation) is identical to
    :class:`OpenAIClient`.

    The class deliberately does NOT call into :mod:`aitap.secrets` —
    that's the route layer's job per ``docs/profiles-design.md``: the
    handler resolves the key for the requested profile via
    :func:`aitap.secrets.get_key_for_profile` and hands it to the
    constructor here. Keeping the secret resolution out of this module
    means the AST-discipline test in ``test_secrets_import_discipline.py``
    has fewer entries on the allow-list, and the client is trivially safe
    to construct in any unit test by passing a fake key string.
    """

    def __init__(self, *, base_url: str, model: str, api_key: str) -> None:
        # base_url is mandatory — the whole point of this class is to
        # vary it. We don't even attempt a default; the design doc
        # Decision 3 has the route layer always pass one.
        if not base_url:
            raise ValueError("base_url is required for OpenAICompatClient")
        if not api_key:
            raise ValueError("api_key is required for OpenAICompatClient")
        super().__init__(model, api_key)
        self.base_url = base_url

    @property
    def provider_name(self) -> str:
        # Constant string rather than a per-endpoint label so callers
        # that branch on the value (e.g. cost-table lookup keys, log
        # filters) treat every OpenAI-compatible profile the same.
        return "openai-compat"

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
        # base_url + api_key as keyword args: the SDK supports both
        # signatures but the multi-provider design contract wants the
        # base_url explicit so a downstream reader of the code can see
        # "yes, this endpoint is the one the user configured".
        client = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key)

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
            raise ProviderError(f"openai-compat API error: {exc}") from exc

        choice = raw.choices[0]
        text = (choice.message.content or "") if choice.message else ""
        usage = TokenUsage(
            input_tokens=raw.usage.prompt_tokens if raw.usage else 0,
            output_tokens=raw.usage.completion_tokens if raw.usage else 0,
        )
        # Cost lookup uses the constant provider_name; the pricing table
        # (deep/pricing.py) holds rows for each known OpenAI-compatible
        # model under the ``"openai-compat"`` key.
        cost = _safe_compat_cost(self.model, usage)
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
        usd = _safe_compat_cost_from_tokens(self.model, in_tokens, out_tokens)
        return CostEstimate(
            input_tokens=in_tokens,
            estimated_output_tokens=out_tokens,
            usd=usd,
            model=self.model,
        )


def _safe_compat_cost(model: str, usage: TokenUsage) -> float:
    return _safe_compat_cost_from_tokens(model, usage.input_tokens, usage.output_tokens)


def _safe_compat_cost_from_tokens(model: str, input_tokens: int, output_tokens: int) -> float:
    """Look up cost under the ``"openai-compat"`` provider key.

    Unpriced models return 0 USD from this internal helper. The route
    layer renders ``cost: unknown`` in the UI for unknown models per the
    design doc; the public ``estimate_cost`` surface is the seam where
    the legacy "0 means unknown" tradition still applies.

    TODO(profile-runs-migration): swap this internal helper for one that
    returns ``float | None`` and propagate ``None`` up through
    ``ChatResponse.cost_usd`` so the UI's ``cost: unknown`` rendering
    survives all the way down from the SDK call site. Doing this here
    would cascade into ``LLMClient.chat`` (every implementation pins the
    return type to ``float``), so this is part of the same contract
    bump as the RunCreate -> profile_id migration.
    """
    try:
        return estimate_usd(
            "openai-compat",
            model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    except UnknownModelError:
        return 0.0
