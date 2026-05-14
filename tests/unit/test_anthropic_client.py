"""AnthropicClient tests using SDK mocks — no network."""

from __future__ import annotations

import types
from typing import Any

import pytest

from aitap.deep.anthropic_client import AnthropicClient
from aitap.deep.client import ChatMessage, ProviderAuthError, ProviderError, ProviderRateLimitError

# --------------------------------------------------------------------------- #
# SDK fakes — these mirror the shape of `anthropic` 0.25+ AsyncAnthropic      #
# --------------------------------------------------------------------------- #


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeMessage:
    def __init__(
        self,
        text: str,
        *,
        input_tokens: int = 100,
        output_tokens: int = 50,
        stop_reason: str = "end_turn",
    ) -> None:
        self.id = "msg_test"
        self.content = [_FakeBlock(text)]
        self.usage = _FakeUsage(input_tokens, output_tokens)
        self.stop_reason = stop_reason


class _MessagesResource:
    def __init__(self, response: _FakeMessage | Exception) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _FakeMessage:
        self.calls.append(kwargs)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _FakeAnthropicClient:
    def __init__(self, response: _FakeMessage | Exception) -> None:
        self.messages = _MessagesResource(response)


# Stable error classes — defined at module load so identity is consistent
# across every _install_fake_sdk call within a single test (the client
# catches by `except sdk_errors.AuthenticationError`; if we made fresh
# classes per install, the second install's classes wouldn't match
# exceptions raised from before).
class FakeAuthError(Exception):
    pass


class FakeRateLimitError(Exception):
    pass


class FakeAPIError(Exception):
    pass


def _install_fake_sdk(
    monkeypatch: pytest.MonkeyPatch,
    response: _FakeMessage | Exception,
) -> _FakeAnthropicClient:
    """Build a fake `anthropic` module exposing the bits AnthropicClient needs."""
    fake_client = _FakeAnthropicClient(response)

    def _AsyncAnthropic(api_key: str) -> _FakeAnthropicClient:
        fake_client._used_api_key = api_key  # type: ignore[attr-defined]
        return fake_client

    fake_module = types.SimpleNamespace(
        AsyncAnthropic=_AsyncAnthropic,
        AuthenticationError=FakeAuthError,
        RateLimitError=FakeRateLimitError,
        APIError=FakeAPIError,
    )

    import sys

    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    return fake_client


# --------------------------------------------------------------------------- #
# Construction / auth                                                         #
# --------------------------------------------------------------------------- #


def test_constructing_client_does_not_touch_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Should be safe to construct without env vars or installed SDK."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = AnthropicClient(model="claude-sonnet-4-6")
    assert client.provider_name == "anthropic"


async def test_chat_raises_provider_auth_error_when_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _install_fake_sdk(monkeypatch, _FakeMessage("hi"))
    client = AnthropicClient(model="claude-sonnet-4-6")
    with pytest.raises(ProviderAuthError):
        await client.chat([ChatMessage(role="user", content="hi")])


async def test_chat_uses_explicit_api_key_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    fake = _install_fake_sdk(monkeypatch, _FakeMessage("hi"))
    client = AnthropicClient(model="claude-sonnet-4-6", api_key="explicit-key")
    await client.chat([ChatMessage(role="user", content="hi")])
    assert fake._used_api_key == "explicit-key"  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# Request shaping                                                             #
# --------------------------------------------------------------------------- #


async def test_chat_separates_system_message_from_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install_fake_sdk(monkeypatch, _FakeMessage("ok"))
    client = AnthropicClient(model="claude-sonnet-4-6", api_key="x")
    await client.chat(
        [
            ChatMessage(role="system", content="You are helpful."),
            ChatMessage(role="user", content="hi"),
        ],
        max_tokens=200,
    )
    call = fake.messages.calls[0]
    assert call["system"] == "You are helpful."
    assert call["messages"] == [{"role": "user", "content": "hi"}]
    assert call["max_tokens"] == 200


async def test_chat_concatenates_multiple_system_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install_fake_sdk(monkeypatch, _FakeMessage("ok"))
    client = AnthropicClient(model="claude-sonnet-4-6", api_key="x")
    await client.chat(
        [
            ChatMessage(role="system", content="First."),
            ChatMessage(role="system", content="Second."),
            ChatMessage(role="user", content="hi"),
        ]
    )
    assert fake.messages.calls[0]["system"] == "First.\n\nSecond."


async def test_chat_passes_optional_params(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install_fake_sdk(monkeypatch, _FakeMessage("ok"))
    client = AnthropicClient(model="claude-sonnet-4-6", api_key="x")
    await client.chat(
        [ChatMessage(role="user", content="hi")],
        temperature=0.3,
        top_p=0.95,
        max_tokens=512,
    )
    call = fake.messages.calls[0]
    assert call["temperature"] == 0.3
    assert call["top_p"] == 0.95
    assert call["max_tokens"] == 512


async def test_chat_default_max_tokens_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anthropic requires max_tokens; we default to 1024 when caller omits."""
    fake = _install_fake_sdk(monkeypatch, _FakeMessage("ok"))
    client = AnthropicClient(model="claude-sonnet-4-6", api_key="x")
    await client.chat([ChatMessage(role="user", content="hi")])
    assert fake.messages.calls[0]["max_tokens"] == 1024


# --------------------------------------------------------------------------- #
# Response parsing                                                            #
# --------------------------------------------------------------------------- #


async def test_chat_returns_concatenated_text_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _FakeMessage("hello world")
    response.content = [_FakeBlock("hello "), _FakeBlock("world")]
    _install_fake_sdk(monkeypatch, response)
    client = AnthropicClient(model="claude-sonnet-4-6", api_key="x")
    result = await client.chat([ChatMessage(role="user", content="hi")])
    assert result.text == "hello world"


async def test_chat_returns_usage_and_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _FakeMessage("ok", input_tokens=200, output_tokens=100)
    _install_fake_sdk(monkeypatch, response)
    client = AnthropicClient(model="claude-sonnet-4-6", api_key="x")
    result = await client.chat([ChatMessage(role="user", content="hi")])
    assert result.usage.input_tokens == 200
    assert result.usage.output_tokens == 100
    # claude-sonnet-4-6: $3/1M input + $15/1M output → $0.0006 + $0.0015 = $0.0021
    assert result.cost_usd == pytest.approx(0.0021, rel=1e-3)


@pytest.mark.parametrize(
    ("stop_reason", "expected_finish"),
    [
        ("end_turn", "stop"),
        ("max_tokens", "length"),
        ("stop_sequence", "stop"),
        ("tool_use", "tool_use"),
        (None, "stop"),
        ("unknown_future_value", "stop"),
    ],
)
async def test_chat_maps_stop_reason(
    monkeypatch: pytest.MonkeyPatch, stop_reason: str | None, expected_finish: str
) -> None:
    _install_fake_sdk(monkeypatch, _FakeMessage("ok", stop_reason=stop_reason or ""))
    client = AnthropicClient(model="claude-sonnet-4-6", api_key="x")
    result = await client.chat([ChatMessage(role="user", content="hi")])
    assert result.finish_reason == expected_finish


# --------------------------------------------------------------------------- #
# Error wrapping                                                              #
# --------------------------------------------------------------------------- #


async def test_chat_wraps_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sdk(monkeypatch, FakeAuthError("bad key"))
    client = AnthropicClient(model="claude-sonnet-4-6", api_key="x")
    with pytest.raises(ProviderAuthError, match="bad key"):
        await client.chat([ChatMessage(role="user", content="hi")])


async def test_chat_wraps_rate_limit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sdk(monkeypatch, FakeRateLimitError("slow down"))
    client = AnthropicClient(model="claude-sonnet-4-6", api_key="x")
    with pytest.raises(ProviderRateLimitError, match="slow down"):
        await client.chat([ChatMessage(role="user", content="hi")])


async def test_chat_wraps_generic_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sdk(monkeypatch, FakeAPIError("server overloaded"))
    client = AnthropicClient(model="claude-sonnet-4-6", api_key="x")
    with pytest.raises(ProviderError, match="server overloaded"):
        await client.chat([ChatMessage(role="user", content="hi")])


async def test_chat_raises_when_sdk_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lazy-import path: missing SDK should surface ProviderError, not ImportError."""
    import sys

    monkeypatch.setitem(sys.modules, "anthropic", None)
    client = AnthropicClient(model="claude-sonnet-4-6", api_key="x")
    with pytest.raises(ProviderError, match="anthropic SDK not installed"):
        await client.chat([ChatMessage(role="user", content="hi")])


# --------------------------------------------------------------------------- #
# Cost estimation (no network)                                                #
# --------------------------------------------------------------------------- #


def test_estimate_cost_for_known_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    client = AnthropicClient(model="claude-sonnet-4-6")
    estimate = client.estimate_cost(
        [ChatMessage(role="user", content="this is roughly 40 chars long input.")],
        max_tokens=500,
    )
    assert estimate.input_tokens > 0
    assert estimate.estimated_output_tokens == 500
    assert estimate.usd > 0
    assert estimate.model == "claude-sonnet-4-6"


def test_estimate_cost_for_unpriced_model_returns_zero() -> None:
    """Internal helper falls back to 0 USD if the model isn't priced; the
    public surface lets the caller see the cost is wrong rather than blocking."""
    client = AnthropicClient(model="some-future-model", api_key="x")
    estimate = client.estimate_cost([ChatMessage(role="user", content="hi")])
    assert estimate.usd == 0.0


# --------------------------------------------------------------------------- #
# Registry self-registration                                                  #
# --------------------------------------------------------------------------- #


def test_anthropic_client_self_registers() -> None:
    from aitap.deep.client import get_client

    # Importing the module above already triggered register_provider.
    instance = get_client("anthropic", "claude-sonnet-4-6", api_key="x")
    assert isinstance(instance, AnthropicClient)
    assert instance.model == "claude-sonnet-4-6"
