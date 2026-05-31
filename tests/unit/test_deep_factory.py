"""Tests for ``aitap.deep.factory.get_client_for_profile``.

The factory is the dispatch point of the multi-provider redesign: it
takes a :class:`Profile` (the route layer already resolved the key and
built the model) and a raw API key, and returns a concrete
:class:`LLMClient` whose class is chosen by ``profile.protocol``.

These tests pin the dispatch table:

- ``protocol="openai-compat"`` returns an :class:`OpenAICompatClient`
  built with the profile's ``base_url`` and ``model_id``.
- ``protocol="anthropic"`` returns an :class:`AnthropicClient` built
  with the profile's ``base_url`` and ``model_id``.
- The api_key reaches the constructor untouched.

We don't patch :mod:`aitap.secrets` here — the factory is intentionally
**ignorant of the secret store**: per ``docs/profiles-design.md``, the
route handler resolves the key via ``secrets.get_key_for_profile`` and
hands it in. Keeping factory tests offline-only also means no env
mutation is required to run them.
"""

from __future__ import annotations

from aitap.deep.anthropic_client import AnthropicClient
from aitap.deep.factory import get_client_for_profile
from aitap.deep.openai_client import OpenAICompatClient
from aitap.server.routes import Profile


def _profile(**overrides: object) -> Profile:
    """Build a minimally-valid :class:`Profile` for the factory tests.

    Status fields don't influence dispatch; ``key_configured=True`` is
    a defensible default so the value reads like a real configured row.
    """
    base: dict[str, object] = {
        "id": "deepseek",
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "protocol": "openai-compat",
        "model_id": "deepseek-chat",
        "notes": "",
        "key_configured": True,
        "key_source": "keyring",
        "key_masked": "sk-...zzzz",
    }
    base.update(overrides)
    return Profile(**base)


def test_openai_compat_protocol_dispatches_to_openai_compat_client() -> None:
    profile = _profile(protocol="openai-compat")
    client = get_client_for_profile(profile, api_key="sk-FAKE-deepseek")
    assert isinstance(client, OpenAICompatClient)
    assert client.base_url == "https://api.deepseek.com/v1"
    assert client.model == "deepseek-chat"
    assert client.api_key == "sk-FAKE-deepseek"


def test_anthropic_protocol_dispatches_to_anthropic_client() -> None:
    profile = _profile(
        id="claude",
        label="Anthropic",
        base_url="https://api.anthropic.com",
        protocol="anthropic",
        model_id="claude-sonnet-4-6",
    )
    client = get_client_for_profile(profile, api_key="sk-ant-FAKE-key")
    assert isinstance(client, AnthropicClient)
    assert client.base_url == "https://api.anthropic.com"
    assert client.model == "claude-sonnet-4-6"
    assert client.api_key == "sk-ant-FAKE-key"


def test_factory_passes_profile_base_url_to_openai_compat_client_verbatim() -> None:
    """A custom (non-canonical) base_url reaches the client unmodified.

    Pins the multi-provider promise: every endpoint that speaks the
    OpenAI wire protocol becomes one ``base_url`` change away.
    """
    profile = _profile(
        id="kimi",
        label="Kimi",
        base_url="https://api.moonshot.cn/v1",
        protocol="openai-compat",
        model_id="moonshot-v1-32k",
    )
    client = get_client_for_profile(profile, api_key="sk-FAKE-kimi")
    assert isinstance(client, OpenAICompatClient)
    assert client.base_url == "https://api.moonshot.cn/v1"
    assert client.model == "moonshot-v1-32k"


def test_factory_passes_custom_anthropic_base_url() -> None:
    profile = _profile(
        id="anthropic-gateway",
        label="Anthropic via gateway",
        base_url="https://anthropic.example-gateway.internal",
        protocol="anthropic",
        model_id="claude-sonnet-4-6",
    )
    client = get_client_for_profile(profile, api_key="sk-ant-FAKE")
    assert isinstance(client, AnthropicClient)
    assert client.base_url == "https://anthropic.example-gateway.internal"
