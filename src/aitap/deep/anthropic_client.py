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

if TYPE_CHECKING:
    pass


_ANTHROPIC_DEFAULT_BASE_URL = "https://api.anthropic.com"


class AnthropicClient(LLMClient):
    """Anthropic Messages API client. Async via the SDK's ``AsyncAnthropic``.

    The multi-provider redesign (wt/profile-client) adds an explicit
    ``base_url`` constructor arg so the factory in
    :func:`aitap.deep.client.get_client_for_profile` can point this
    client at a user-configured endpoint (a self-hosted Anthropic
    gateway, a future regional endpoint, etc.). The default value
    (``https://api.anthropic.com``) preserves byte-for-byte behaviour
    for every legacy call site that constructed this class with just
    ``(model, api_key)``.
    """

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        *,
        base_url: str = _ANTHROPIC_DEFAULT_BASE_URL,
    ) -> None:
        super().__init__(model, api_key)
        self.base_url = base_url

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
        # base_url goes through alongside the api_key — Anthropic SDK
        # accepts both as keyword args. Passing the documented default
        # explicitly (rather than relying on the SDK's implicit
        # constant) makes the configured endpoint visible in error
        # messages + debugger frames.
        client = AsyncAnthropic(api_key=self._resolve_api_key(), base_url=self.base_url)

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
        # The vault module is the single owner of key reads — env-var
        # fallback included. Going through ``secrets.get_key`` (instead
        # of poking ``os.environ`` directly) means a UI-saved key in the
        # OS keyring wins over a stale env var, which is what the user
        # picked the most recently.
        key = self.api_key or _secrets.get_key("anthropic")
        if not key:
            raise ProviderAuthError(
                "No Anthropic API key set. Add one in aitap ui → Settings, "
                "or export ANTHROPIC_API_KEY in your shell."
            )
        return key


# --------------------------------------------------------------------------- #
# Helpers (module-private)                                                    #
# --------------------------------------------------------------------------- #


def _import_sdk() -> tuple[Any, Any]:
    """Lazy import of the anthropic SDK. Raises ProviderError if missing.

    Return type is ``tuple[Any, Any]`` (not the literal SDK class + module)
    because the ``anthropic`` package is an optional extra; declaring it
    as ``Any`` keeps pyright/strict happy on Python 3.10 (which is stricter
    than 3.11+ about reporting unknown types in unannotated returns).
    """
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


def anthropic_factory(model: str, key: str | None) -> AnthropicClient:
    """Build an :class:`AnthropicClient` for the legacy provider-keyed
    factory (``register_provider("anthropic", ...)``), honouring the
    ``ANTHROPIC_BASE_URL`` environment variable when set.

    This is the seam that lets users run ``aitap scan --deep`` against
    any Anthropic-protocol-compatible gateway — including DeepSeek's
    ``https://api.deepseek.com/anthropic`` endpoint, a self-hosted
    Anthropic gateway, or a future regional endpoint — without
    waiting on the full ``wt/deep-profile-dispatch`` migration the
    `wt/profile-cleanup` design doc flags as a follow-up.

    Resolution order matches the rest of ``aitap.deep``:

    1. ``ANTHROPIC_BASE_URL`` env var if non-empty (after ``.strip()``);
    2. otherwise ``AnthropicClient``'s default
       (``https://api.anthropic.com``) preserves byte-for-byte
       behaviour for every existing caller that never set the var.

    Whitespace-only env values (``""``, ``"   "``, ``"\\t\\n"``) are
    treated as unset so a copy-paste accident doesn't silently break
    the legacy path. Leading / trailing whitespace around an otherwise
    valid URL is also stripped (URLs don't contain whitespace per
    RFC 3986, so stripping is safe).

    The new profile-keyed path (``get_client_for_profile``) is the
    long-term answer; this hook keeps the legacy path useful in the
    meantime. We deliberately do NOT consult the env var inside
    :class:`AnthropicClient` itself — direct constructor callers and
    the profile factory both pass ``base_url`` explicitly, and an
    inner override would silently shadow their choice.

    Interaction with the Anthropic SDK's own env var
    ------------------------------------------------

    The Anthropic Python SDK *also* reads ``ANTHROPIC_BASE_URL`` from
    the environment (see ``anthropic._client``: when its constructor
    receives ``base_url=None`` it falls back to ``os.environ.get``).
    Because :class:`AnthropicClient` always forwards a non-``None``
    ``base_url`` to the SDK (its constructor default is the canonical
    Anthropic hostname, never ``None``), the SDK's own env-var read is
    permanently shadowed — **aitap is the sole interpreter of
    ``ANTHROPIC_BASE_URL``**.

    This is intentional, not an oversight. A single owner of the
    env-var contract means the profile path stays predictable: a
    ``Profile`` configured with ``base_url=A`` always hits A, even
    when the user has ``ANTHROPIC_BASE_URL=B`` exported for some
    other tool's benefit. The cost is that users who already learned
    the SDK's behaviour see ``ANTHROPIC_BASE_URL`` work *only* on
    aitap's legacy factory path (this function), not on a direct
    ``AnthropicClient(...)`` construction in code. The CHANGELOG and
    PR #58 description spell that out for downstream users.

    If we ever want the env var to behave SDK-style on direct
    construction too, the right move is a separate aitap-namespaced
    ``AITAP_ANTHROPIC_BASE_URL`` variable so the two contracts don't
    overlap; this PR keeps the SDK name to minimise the
    learning-curve barrier for the immediate DeepSeek workflow.
    """
    env_base = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
    if env_base:
        return AnthropicClient(model, key, base_url=env_base)
    return AnthropicClient(model, key)


register_provider("anthropic", anthropic_factory)
