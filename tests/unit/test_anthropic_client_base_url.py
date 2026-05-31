"""AnthropicClient ``base_url`` plumbing for the multi-provider redesign.

Pins the additive ``base_url`` constructor arg added in wt/profile-client:

- Default value (``https://api.anthropic.com``) keeps every existing
  call site working without edits.
- Custom value is forwarded to the SDK constructor verbatim, so a user
  can point at a self-hosted Anthropic-compatible gateway or a future
  regional endpoint by configuring the profile.

These tests live in a sibling file so the legacy
``test_anthropic_client.py`` stays a faithful regression suite for the
provider-keyed surface; the breaking-change discipline says new tests
land alongside, never on top of, the originals.
"""

from __future__ import annotations

import types
from typing import Any

import pytest

from aitap.deep.anthropic_client import AnthropicClient
from aitap.deep.client import ChatMessage


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeMessage:
    def __init__(self, text: str) -> None:
        self.id = "msg_test"
        self.content = [_FakeBlock(text)]
        self.usage = _FakeUsage(10, 5)
        self.stop_reason = "end_turn"


class _MessagesResource:
    def __init__(self, response: _FakeMessage) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _FakeMessage:
        self.calls.append(kwargs)
        return self._response


class _FakeAnthropicClient:
    def __init__(self, response: _FakeMessage) -> None:
        self.messages = _MessagesResource(response)


class FakeAuthError(Exception):
    pass


class FakeRateLimitError(Exception):
    pass


class FakeAPIError(Exception):
    pass


def _install_fake_sdk(
    monkeypatch: pytest.MonkeyPatch,
    response: _FakeMessage,
) -> _FakeAnthropicClient:
    """Install a fake ``anthropic`` SDK that records constructor kwargs.

    Records ``api_key`` AND ``base_url`` so the tests can assert the
    multi-provider redesign forwards both to the SDK.
    """
    fake_client = _FakeAnthropicClient(response)

    def _AsyncAnthropic(*, api_key: str, base_url: str | None = None) -> _FakeAnthropicClient:
        fake_client._used_api_key = api_key  # type: ignore[attr-defined]
        fake_client._used_base_url = base_url  # type: ignore[attr-defined]
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
# Default base_url — back-compat for the legacy call sites                    #
# --------------------------------------------------------------------------- #


def test_constructor_without_base_url_records_canonical_default() -> None:
    """Existing callers (``AnthropicClient(model, api_key)``) keep working.

    The new ``base_url`` parameter is keyword-only with a default of
    Anthropic's canonical hostname, so the legacy two-arg construction
    still passes pyright and runtime.
    """
    client = AnthropicClient(model="claude-sonnet-4-6", api_key="sk-FAKE")
    assert client.base_url == "https://api.anthropic.com"


async def test_chat_forwards_default_base_url_to_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _install_fake_sdk(monkeypatch, _FakeMessage("pong"))
    client = AnthropicClient(model="claude-sonnet-4-6", api_key="sk-FAKE")
    await client.chat([ChatMessage(role="user", content="ping")], max_tokens=4)
    assert fake._used_api_key == "sk-FAKE"  # type: ignore[attr-defined]
    assert fake._used_base_url == "https://api.anthropic.com"  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# Custom base_url — what the profile factory will pass in                     #
# --------------------------------------------------------------------------- #


def test_constructor_records_custom_base_url() -> None:
    client = AnthropicClient(
        model="claude-sonnet-4-6",
        api_key="sk-FAKE",
        base_url="https://anthropic.example-gateway.internal",
    )
    assert client.base_url == "https://anthropic.example-gateway.internal"


async def test_chat_forwards_custom_base_url_to_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the factory hands us a profile's base_url, it lands on the SDK."""
    fake = _install_fake_sdk(monkeypatch, _FakeMessage("pong"))
    client = AnthropicClient(
        model="claude-sonnet-4-6",
        api_key="sk-FAKE",
        base_url="https://anthropic.example-gateway.internal",
    )
    await client.chat([ChatMessage(role="user", content="ping")], max_tokens=4)
    assert fake._used_base_url == "https://anthropic.example-gateway.internal"  # type: ignore[attr-defined]
