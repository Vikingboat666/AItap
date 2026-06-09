"""Legacy ``register_provider("anthropic", ...)`` honours
``ANTHROPIC_BASE_URL``.

Why this exists
---------------

``wt/profile-cleanup`` (PR #43) deliberately left the legacy
provider-keyed factory wired into ``aitap.deep.client.get_client`` while
the new ``get_client_for_profile`` path took over the runs surface. The
``aitap scan --deep`` L2 enrichment still rides the legacy path, which
means a user with a DeepSeek (or any other Anthropic-protocol-compatible)
key cannot point L2 at the non-default endpoint without either
migrating L2 to the profile API (a substantial follow-up) or punching a
hole into the legacy factory.

The factory hook in ``aitap.deep.anthropic_client`` takes the smaller
hole: read ``ANTHROPIC_BASE_URL`` from the environment when constructing
the legacy client, fall back to the default Anthropic endpoint when the
var is unset / empty. This file pins the contracts the change has to
honour so a future refactor (full profile-dispatch migration, or a
different env var name) trips a test instead of silently regressing the
L2-via-DeepSeek workflow.

We exercise the **public** entry point ``aitap.deep.client.get_client``
rather than the module-private factory function — that's what
``aitap scan --deep`` actually calls, so this is also the path a
reader would reach for when reproducing the workflow.

Contracts pinned here
---------------------

1. **No env var → default endpoint.** Existing call sites that never
   set the var keep their byte-for-byte behaviour (the
   ``https://api.anthropic.com`` constant).
2. **Env var set → factory propagates it.** The constructed client's
   ``base_url`` attribute equals the env value verbatim. We don't
   reformat (no trailing-slash normalisation, no protocol injection).
3. **Whitespace-only env var → treated as unset.** ``""`` and ``"   "``
   both fall through to the default; this avoids a copy-paste accident
   silently breaking the legacy path.

The actual SDK call path (`AsyncAnthropic(base_url=...)`) is already
covered by ``test_anthropic_client_base_url.py``; we don't re-test the
SDK invocation here. The factory's job is *picking* the value, and that
is what we pin.
"""

from __future__ import annotations

import pytest

from aitap.deep.anthropic_client import AnthropicClient
from aitap.deep.client import get_client

_DEFAULT_ANTHROPIC_URL = "https://api.anthropic.com"


def _build(model: str = "claude-sonnet-4-6") -> AnthropicClient:
    """Helper — round-trip through the public legacy factory."""
    client = get_client(provider="anthropic", model=model, api_key="sk-ant-test")
    assert isinstance(client, AnthropicClient)
    return client


def test_factory_uses_default_base_url_when_env_var_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``ANTHROPIC_BASE_URL`` → the constructed client points at the
    canonical Anthropic endpoint. This is the byte-for-byte
    backward-compatibility contract: legacy users who never heard of
    the new env var see no behaviour change.
    """
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)

    client = _build()

    assert client.base_url == _DEFAULT_ANTHROPIC_URL
    assert client.model == "claude-sonnet-4-6"


def test_factory_uses_env_var_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env var set → factory propagates it verbatim. The motivating
    case is ``https://api.deepseek.com/anthropic`` for the
    DeepSeek-via-Anthropic-protocol workflow, but any
    Anthropic-protocol-compatible gateway works the same way (a
    self-hosted gateway, a future regional endpoint, etc.).
    """
    deepseek_url = "https://api.deepseek.com/anthropic"
    monkeypatch.setenv("ANTHROPIC_BASE_URL", deepseek_url)

    client = _build(model="deepseek-chat")

    assert client.base_url == deepseek_url
    assert client.model == "deepseek-chat"


@pytest.mark.parametrize("empty", ["", "   ", "\t\n"])
def test_factory_treats_whitespace_env_var_as_unset(
    monkeypatch: pytest.MonkeyPatch, empty: str
) -> None:
    """Whitespace-only env var → treated as unset → default endpoint.

    A user who exports the var to clear it (``export ANTHROPIC_BASE_URL=``)
    or accidentally pastes a blank value shouldn't end up pointing at
    a broken URL — the factory should fall through to the default. We
    parametrise over the three plausible "blank" shapes to make the
    intent explicit.
    """
    monkeypatch.setenv("ANTHROPIC_BASE_URL", empty)

    client = _build()

    assert client.base_url == _DEFAULT_ANTHROPIC_URL


def test_factory_strips_surrounding_whitespace_from_otherwise_valid_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leading / trailing whitespace around a valid URL → stripped, then
    used. RFC 3986 doesn't allow whitespace inside URLs, so trimming a
    copy-paste accident is safe and matches what every shell-set env
    var expects ("export FOO='  x  '" rarely intends the spaces).
    """
    deepseek_url = "https://api.deepseek.com/anthropic"
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"  {deepseek_url}  ")

    client = _build()

    assert client.base_url == deepseek_url


def test_env_override_does_not_leak_into_explicit_constructor_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The env var only affects the legacy factory. A direct
    ``AnthropicClient(model, key, base_url=...)`` constructor call
    must honour the explicit value, not the env override.

    This protects the new ``get_client_for_profile`` path (and any
    test or script that builds a client directly) from being
    surprised by an ambient env var. The factory's job is to read
    env, the constructor's job is to honour what it's told.
    """
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://wrong.example.com")

    explicit = "https://right.example.com/anthropic"
    client = AnthropicClient("claude-sonnet-4-6", "sk-ant-test", base_url=explicit)

    assert client.base_url == explicit


# --------------------------------------------------------------------------- #
# End-to-end pin: factory → AnthropicClient → AsyncAnthropic constructor      #
# --------------------------------------------------------------------------- #
#
# The four contract tests above stop at ``client.base_url`` — they pin the
# factory's choice but don't prove the choice flows through to the SDK
# call. A sibling file (``test_anthropic_client_base_url.py``) covers the
# constructor → SDK leg in isolation, but no test bridges both legs in
# one assertion path. The end-to-end pin below closes that seam: a future
# refactor that breaks the chain anywhere (factory forgets to forward,
# constructor stops honouring, ``chat()`` stops passing base_url to
# AsyncAnthropic) trips this single test.


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

    async def create(self, **_kwargs: object) -> _FakeMessage:
        return self._response


class _FakeAnthropicClient:
    def __init__(self, response: _FakeMessage) -> None:
        self.messages = _MessagesResource(response)


class _FakeAuthError(Exception):
    pass


class _FakeRateLimitError(Exception):
    pass


class _FakeAPIError(Exception):
    pass


def _install_recording_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    """Install a fake ``anthropic`` module whose AsyncAnthropic records
    the ``base_url`` it was constructed with into a shared dict.
    Returns the dict so the test can assert what the SDK actually saw.
    """
    import sys
    import types as _types

    recorded: dict[str, object] = {}
    fake_response = _FakeMessage("pong")
    fake_client = _FakeAnthropicClient(fake_response)

    def _AsyncAnthropic(*, api_key: str, base_url: str | None = None) -> _FakeAnthropicClient:
        recorded["api_key"] = api_key
        recorded["base_url"] = base_url
        return fake_client

    fake_module = _types.SimpleNamespace(
        AsyncAnthropic=_AsyncAnthropic,
        AuthenticationError=_FakeAuthError,
        RateLimitError=_FakeRateLimitError,
        APIError=_FakeAPIError,
    )
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    return recorded


async def test_factory_to_sdk_end_to_end_forwards_env_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bridge the four contract tests above with the sibling file's SDK
    pin: setting ``ANTHROPIC_BASE_URL``, building through
    ``get_client("anthropic", ...)``, then calling ``await chat(...)``
    must result in ``AsyncAnthropic(base_url=<env_value>)`` at the SDK
    boundary. If any link in the factory → constructor → ``chat`` →
    SDK chain drops the value, this test fails.
    """
    from aitap.deep.client import ChatMessage

    deepseek_url = "https://api.deepseek.com/anthropic"
    monkeypatch.setenv("ANTHROPIC_BASE_URL", deepseek_url)
    recorded = _install_recording_sdk(monkeypatch)

    client = _build(model="deepseek-chat")
    await client.chat([ChatMessage(role="user", content="ping")], max_tokens=4)

    assert recorded["base_url"] == deepseek_url
    assert recorded["api_key"] == "sk-ant-test"
