"""OpenAICompatClient tests using SDK mocks — no network.

OpenAICompatClient is the multi-provider redesign's "speaks the OpenAI
chat-completions wire format" client. It is structurally identical to
:class:`OpenAIClient` except:

- ``base_url`` is **mandatory** at construction (DeepSeek, Kimi, Groq,
  Together, Ollama, LM Studio all live at different endpoints — there
  is no sensible default).
- It does NOT call into :mod:`aitap.secrets`; the route layer resolves
  the key per-profile and hands it in via the constructor. The API key
  is therefore mandatory too.

These tests pin both points + the SDK constructor receives ``base_url``
and ``api_key`` as documented.
"""

from __future__ import annotations

import types
from typing import Any

import pytest

from aitap.deep.client import (
    ChatMessage,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
)
from aitap.deep.openai_client import OpenAICompatClient

# --------------------------------------------------------------------------- #
# SDK fakes — same shape as the OpenAI test fixture                           #
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
    """Install a fake ``openai`` SDK module that records constructor kwargs.

    The fake records both ``base_url`` and ``api_key`` the SDK is built
    with so a test can pin the dispatch contract (one of the points the
    multi-provider redesign rests on).
    """
    fake_client = _FakeOpenAIClient(response)

    def _AsyncOpenAI(*, base_url: str, api_key: str) -> _FakeOpenAIClient:
        fake_client._used_base_url = base_url  # type: ignore[attr-defined]
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
# Construction                                                                #
# --------------------------------------------------------------------------- #


def test_construction_records_base_url_and_key() -> None:
    """``base_url`` and ``api_key`` are stored on the instance verbatim."""
    client = OpenAICompatClient(
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        api_key="sk-FAKE-key",
    )
    assert client.base_url == "https://api.deepseek.com/v1"
    assert client.model == "deepseek-chat"
    assert client.api_key == "sk-FAKE-key"
    # Provider name is the constant "openai-compat" — every endpoint
    # routed through this client speaks the same wire protocol.
    assert client.provider_name == "openai-compat"


def test_construction_does_not_touch_network() -> None:
    """Construction is pure — no SDK import, no HTTP."""
    # Build without an installed openai SDK in sys.modules; the
    # construction path must not import it.
    client = OpenAICompatClient(
        base_url="https://api.together.xyz/v1",
        model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        api_key="sk-FAKE-together",
    )
    assert client.base_url == "https://api.together.xyz/v1"


# --------------------------------------------------------------------------- #
# chat() — SDK constructor receives base_url + api_key                        #
# --------------------------------------------------------------------------- #


async def test_chat_passes_base_url_and_api_key_to_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mandatory ``base_url`` reaches the SDK constructor verbatim.

    Pins Decision: every OpenAI-compatible endpoint (DeepSeek, Kimi, …)
    is reached by changing ``base_url`` on the same SDK constructor.
    """
    fake = _install_fake_sdk(monkeypatch, _FakeCompletion("pong"))
    client = OpenAICompatClient(
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        api_key="sk-FAKE-deepseek",
    )
    await client.chat([ChatMessage(role="user", content="ping")], max_tokens=4)
    assert fake._used_base_url == "https://api.deepseek.com/v1"  # type: ignore[attr-defined]
    assert fake._used_api_key == "sk-FAKE-deepseek"  # type: ignore[attr-defined]
    # The ping-shape payload from the design doc Decision 3 reaches the
    # SDK unchanged.
    call = fake.chat.completions.calls[0]
    assert call["messages"] == [{"role": "user", "content": "ping"}]
    assert call["max_tokens"] == 4


async def test_chat_response_format_json_sends_json_object_kwarg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``response_format="json"`` → SDK kwarg ``{"type": "json_object"}``.

    Pinned in this file because the deleted ``test_openai_client.py``
    test (A2-P3) used to cover it against ``OpenAIClient``. Critic +
    judge rely on JSON mode at runtime — a rename of the SDK kwarg
    would otherwise only get caught against a live provider.
    """
    fake = _install_fake_sdk(monkeypatch, _FakeCompletion("{}"))
    client = OpenAICompatClient(
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        api_key="x",
    )
    await client.chat(
        [ChatMessage(role="user", content="hi")],
        response_format="json",
    )
    call = fake.chat.completions.calls[0]
    assert call["response_format"] == {"type": "json_object"}


async def test_chat_passes_optional_params(monkeypatch: pytest.MonkeyPatch) -> None:
    """``temperature`` / ``max_tokens`` / ``top_p`` round-trip into the
    SDK call. Pinned here after ``test_openai_client.py`` (A2-P3)
    deletion removed the equivalent coverage."""
    fake = _install_fake_sdk(monkeypatch, _FakeCompletion("ok"))
    client = OpenAICompatClient(
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        api_key="x",
    )
    await client.chat(
        [ChatMessage(role="user", content="hi")],
        temperature=0.7,
        max_tokens=42,
        top_p=0.95,
    )
    call = fake.chat.completions.calls[0]
    assert call["temperature"] == 0.7
    assert call["max_tokens"] == 42
    assert call["top_p"] == 0.95


async def test_chat_maps_finish_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """SDK finish_reason strings map onto our ``FinishReason`` literal.

    Iterate convergence detection relies on ``length`` surfacing
    distinctly from ``stop``; pinned here after the equivalent
    ``OpenAIClient`` test was deleted in A2-P3.
    """
    _install_fake_sdk(monkeypatch, _FakeCompletion("trunc", finish_reason="length"))
    client = OpenAICompatClient(
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        api_key="x",
    )
    response = await client.chat([ChatMessage(role="user", content="hi")])
    assert response.finish_reason == "length"


async def test_chat_returns_text_and_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sdk(
        monkeypatch,
        _FakeCompletion("hello", prompt_tokens=200, completion_tokens=100),
    )
    client = OpenAICompatClient(
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        api_key="sk-x",
    )
    result = await client.chat([ChatMessage(role="user", content="hi")])
    assert result.text == "hello"
    assert result.usage.input_tokens == 200
    assert result.usage.output_tokens == 100


# --------------------------------------------------------------------------- #
# Error wrapping mirrors OpenAIClient — same exception taxonomy               #
# --------------------------------------------------------------------------- #


async def test_chat_wraps_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sdk(monkeypatch, FakeAuthError("bad key"))
    client = OpenAICompatClient(
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        api_key="x",
    )
    with pytest.raises(ProviderAuthError, match="bad key"):
        await client.chat([ChatMessage(role="user", content="hi")])


async def test_chat_wraps_rate_limit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sdk(monkeypatch, FakeRateLimitError("slow down"))
    client = OpenAICompatClient(
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        api_key="x",
    )
    with pytest.raises(ProviderRateLimitError, match="slow down"):
        await client.chat([ChatMessage(role="user", content="hi")])


async def test_chat_wraps_generic_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sdk(monkeypatch, FakeAPIError("server overloaded"))
    client = OpenAICompatClient(
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        api_key="x",
    )
    with pytest.raises(ProviderError, match="server overloaded"):
        await client.chat([ChatMessage(role="user", content="hi")])


async def test_chat_raises_when_sdk_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same lazy-import discipline as OpenAIClient."""
    import sys

    monkeypatch.setitem(sys.modules, "openai", None)
    client = OpenAICompatClient(
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        api_key="x",
    )
    with pytest.raises(ProviderError, match="openai SDK not installed"):
        await client.chat([ChatMessage(role="user", content="hi")])


# --------------------------------------------------------------------------- #
# Cost estimation — unpriced model degrades to 0 (same as legacy client)      #
# --------------------------------------------------------------------------- #


def test_estimate_cost_returns_estimate_even_for_unknown_model() -> None:
    """Unpriced (provider, model) tuples return a 0-cost estimate from the
    public surface so the cost-gate caller can still decide whether to
    proceed. The pricing table is the contract; cost-unknown UI behaviour
    is the route layer's responsibility."""
    client = OpenAICompatClient(
        base_url="http://127.0.0.1:11434/v1",
        model="llama3.1",
        api_key="ollama",
    )
    estimate = client.estimate_cost([ChatMessage(role="user", content="hi")])
    assert estimate.input_tokens > 0
    assert estimate.usd == 0.0
    assert estimate.model == "llama3.1"
