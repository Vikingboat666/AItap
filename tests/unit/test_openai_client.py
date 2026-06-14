"""OpenAIClient tests using SDK mocks — no network."""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

import pytest

from aitap.deep.client import (
    ChatMessage,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
)
from aitap.deep.openai_client import OpenAIClient

# --------------------------------------------------------------------------- #
# SDK fakes                                                                   #
# --------------------------------------------------------------------------- #


class _FakeUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str, finish_reason: str = "stop") -> None:
        self.message = _FakeMessage(content)
        self.finish_reason = finish_reason


class _FakeCompletion:
    def __init__(
        self,
        text: str,
        *,
        prompt_tokens: int = 80,
        completion_tokens: int = 40,
        finish_reason: str = "stop",
    ) -> None:
        self.id = "chatcmpl_test"
        self.choices = [_FakeChoice(text, finish_reason)]
        self.usage = _FakeUsage(prompt_tokens, completion_tokens)


class _CompletionsResource:
    def __init__(self, response: _FakeCompletion | Exception) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _FakeCompletion:
        self.calls.append(kwargs)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _ChatResource:
    def __init__(self, response: _FakeCompletion | Exception) -> None:
        self.completions = _CompletionsResource(response)


class _FakeOpenAIClient:
    def __init__(self, response: _FakeCompletion | Exception) -> None:
        self.chat = _ChatResource(response)


class FakeAuthError(Exception):
    pass


class FakeRateLimitError(Exception):
    pass


class FakeAPIError(Exception):
    pass


def _install_fake_sdk(
    monkeypatch: pytest.MonkeyPatch,
    response: _FakeCompletion | Exception,
) -> _FakeOpenAIClient:
    fake_client = _FakeOpenAIClient(response)

    def _AsyncOpenAI(api_key: str) -> _FakeOpenAIClient:
        fake_client._used_api_key = api_key  # type: ignore[attr-defined]
        return fake_client

    fake_module = types.SimpleNamespace(
        AsyncOpenAI=_AsyncOpenAI,
        AuthenticationError=FakeAuthError,
        RateLimitError=FakeRateLimitError,
        APIError=FakeAPIError,
    )

    import sys

    monkeypatch.setitem(sys.modules, "openai", fake_module)
    return fake_client


# --------------------------------------------------------------------------- #
# Construction / auth                                                         #
# --------------------------------------------------------------------------- #


def test_construction_does_not_touch_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = OpenAIClient(model="gpt-4o-mini")
    assert client.provider_name == "openai"


async def test_chat_raises_provider_auth_error_when_key_missing(
    monkeypatch: pytest.MonkeyPatch,
    isolated_secrets_home: Path,
) -> None:
    """Symmetric to ``test_anthropic_client``'s same-named test. See
    ``tests/conftest.py:isolated_secrets_home`` for why the HOME-
    relocation fixture is required: ``secrets.get_key("openai")``
    consults keyring → fallback file → env var, so deleting only the
    env var leaves the fallback file path intact and a developer
    machine with ``profile:openai`` or ``openai:`` set in
    ``~/.aitap/secrets.yaml`` fails this assertion locally.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _install_fake_sdk(monkeypatch, _FakeCompletion("ok"))
    client = OpenAIClient(model="gpt-4o-mini")
    with pytest.raises(ProviderAuthError):
        await client.chat([ChatMessage(role="user", content="hi")])


async def test_chat_explicit_key_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    fake = _install_fake_sdk(monkeypatch, _FakeCompletion("ok"))
    client = OpenAIClient(model="gpt-4o-mini", api_key="explicit-key")
    await client.chat([ChatMessage(role="user", content="hi")])
    assert fake._used_api_key == "explicit-key"  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# Request shaping                                                             #
# --------------------------------------------------------------------------- #


async def test_chat_passes_messages_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install_fake_sdk(monkeypatch, _FakeCompletion("ok"))
    client = OpenAIClient(model="gpt-4o-mini", api_key="x")
    await client.chat(
        [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="usr"),
        ]
    )
    call = fake.chat.completions.calls[0]
    assert call["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
    ]


async def test_chat_response_format_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install_fake_sdk(monkeypatch, _FakeCompletion('{"ok": true}'))
    client = OpenAIClient(model="gpt-4o-mini", api_key="x")
    await client.chat(
        [ChatMessage(role="user", content="give me json")],
        response_format="json",
    )
    assert fake.chat.completions.calls[0]["response_format"] == {"type": "json_object"}


async def test_chat_passes_optional_params(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install_fake_sdk(monkeypatch, _FakeCompletion("ok"))
    client = OpenAIClient(model="gpt-4o-mini", api_key="x")
    await client.chat(
        [ChatMessage(role="user", content="hi")],
        temperature=0.5,
        top_p=0.9,
        max_tokens=400,
    )
    call = fake.chat.completions.calls[0]
    assert call["temperature"] == 0.5
    assert call["top_p"] == 0.9
    assert call["max_tokens"] == 400


# --------------------------------------------------------------------------- #
# Response parsing                                                            #
# --------------------------------------------------------------------------- #


async def test_chat_returns_text_and_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sdk(
        monkeypatch, _FakeCompletion("hello", prompt_tokens=200, completion_tokens=100)
    )
    client = OpenAIClient(model="gpt-4o-mini", api_key="x")
    result = await client.chat([ChatMessage(role="user", content="hi")])
    assert result.text == "hello"
    assert result.usage.input_tokens == 200
    assert result.usage.output_tokens == 100
    # gpt-4o-mini: $0.15/1M input + $0.60/1M output → $0.00003 + $0.00006 = $0.00009
    assert result.cost_usd == pytest.approx(0.00009, rel=1e-2)


@pytest.mark.parametrize(
    ("finish", "expected"),
    [
        ("stop", "stop"),
        ("length", "length"),
        ("tool_calls", "tool_use"),
        ("function_call", "tool_use"),
        ("content_filter", "content_filter"),
        ("unknown_future", "stop"),
        (None, "stop"),
    ],
)
async def test_chat_maps_finish_reason(
    monkeypatch: pytest.MonkeyPatch, finish: str | None, expected: str
) -> None:
    _install_fake_sdk(monkeypatch, _FakeCompletion("ok", finish_reason=finish or ""))
    client = OpenAIClient(model="gpt-4o-mini", api_key="x")
    result = await client.chat([ChatMessage(role="user", content="hi")])
    assert result.finish_reason == expected


# --------------------------------------------------------------------------- #
# Error wrapping                                                              #
# --------------------------------------------------------------------------- #


async def test_chat_wraps_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sdk(monkeypatch, FakeAuthError("bad key"))
    client = OpenAIClient(model="gpt-4o-mini", api_key="x")
    with pytest.raises(ProviderAuthError, match="bad key"):
        await client.chat([ChatMessage(role="user", content="hi")])


async def test_chat_wraps_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sdk(monkeypatch, FakeRateLimitError("slow down"))
    client = OpenAIClient(model="gpt-4o-mini", api_key="x")
    with pytest.raises(ProviderRateLimitError, match="slow down"):
        await client.chat([ChatMessage(role="user", content="hi")])


async def test_chat_raises_when_sdk_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "openai", None)
    client = OpenAIClient(model="gpt-4o-mini", api_key="x")
    with pytest.raises(ProviderError, match="openai SDK not installed"):
        await client.chat([ChatMessage(role="user", content="hi")])


# --------------------------------------------------------------------------- #
# Cost estimation                                                             #
# --------------------------------------------------------------------------- #


def test_estimate_cost_for_priced_model() -> None:
    client = OpenAIClient(model="gpt-4o", api_key="x")
    estimate = client.estimate_cost(
        [ChatMessage(role="user", content="x" * 200)],
        max_tokens=300,
    )
    assert estimate.input_tokens > 0
    assert estimate.estimated_output_tokens == 300
    assert estimate.usd > 0


# --------------------------------------------------------------------------- #
# Registry                                                                    #
# --------------------------------------------------------------------------- #


def test_openai_self_registers() -> None:
    from aitap.deep.client import get_client

    instance = get_client("openai", "gpt-4o-mini", api_key="x")
    assert isinstance(instance, OpenAIClient)
    assert instance.model == "gpt-4o-mini"
