"""Tests for ``aitap.images.factory.get_image_client_for_profile``.

The factory is the dispatch point of the image grid: it takes a
:class:`Profile` (the route layer already resolved the key and built
the model) and a raw API key, and returns a concrete
:class:`ImageClient` whose class is chosen by ``profile.protocol``.

These tests pin the dispatch table:

- ``protocol="openai-compat"`` returns an :class:`OpenAIImageClient`
  built with the profile's ``base_url`` and ``model_id``.
- ``protocol="anthropic"`` raises :class:`ImageProviderError` (Anthropic
  has no image-generation endpoint — the explicit refusal keeps the UI
  honest about which profiles can drive the grid).
- The ``api_key`` reaches the constructor untouched; an empty key is
  rejected up front.

We don't patch :mod:`aitap.secrets` here — the factory is intentionally
**ignorant of the secret store** (route layer's job).
"""

from __future__ import annotations

import pytest

from aitap.images.client import ImageProviderError
from aitap.images.factory import get_image_client_for_profile
from aitap.images.openai_client import OpenAIImageClient
from aitap.server.routes import Profile


def _profile(**overrides: object) -> Profile:
    """Build a minimally-valid :class:`Profile` for factory tests."""
    base: dict[str, object] = {
        "id": "openai-dalle",
        "label": "OpenAI DALL-E",
        "base_url": "https://api.openai.com/v1",
        "protocol": "openai-compat",
        "model_id": "dall-e-3",
        "notes": "",
        "key_configured": True,
        "key_source": "keyring",
        "key_masked": "sk-...zzzz",
    }
    base.update(overrides)
    return Profile(**base)


# --------------------------------------------------------------------------- #
# openai-compat → OpenAIImageClient                                           #
# --------------------------------------------------------------------------- #


def test_openai_compat_protocol_dispatches_to_openai_image_client() -> None:
    profile = _profile(protocol="openai-compat")
    client = get_image_client_for_profile(profile, api_key="sk-FAKE-image")
    assert isinstance(client, OpenAIImageClient)
    assert client.base_url == "https://api.openai.com/v1"
    assert client.model == "dall-e-3"
    assert client.api_key == "sk-FAKE-image"


def test_factory_passes_profile_base_url_verbatim() -> None:
    """A custom (non-canonical) base_url reaches the client unmodified —
    the same single ``base_url`` pivot the chat-side factory pins."""
    profile = _profile(
        id="self-hosted-images",
        label="Self-hosted images",
        base_url="https://images.example.internal/v1",
        protocol="openai-compat",
        model_id="dall-e-3",
    )
    client = get_image_client_for_profile(profile, api_key="sk-FAKE")
    assert isinstance(client, OpenAIImageClient)
    assert client.base_url == "https://images.example.internal/v1"
    assert client.model == "dall-e-3"


def test_factory_passes_through_dall_e_2() -> None:
    profile = _profile(model_id="dall-e-2")
    client = get_image_client_for_profile(profile, api_key="sk-FAKE")
    assert isinstance(client, OpenAIImageClient)
    assert client.model == "dall-e-2"


# --------------------------------------------------------------------------- #
# anthropic → ImageProviderError                                              #
# --------------------------------------------------------------------------- #


def test_anthropic_protocol_raises_image_provider_error() -> None:
    """Anthropic has no image-generation endpoint; the factory refuses
    construction so the UI can surface a plain-language message rather
    than silently 404-ing on the first generate() call."""
    profile = _profile(
        id="claude",
        label="Anthropic",
        base_url="https://api.anthropic.com",
        protocol="anthropic",
        model_id="claude-sonnet-4-6",
    )
    with pytest.raises(ImageProviderError, match="Anthropic"):
        get_image_client_for_profile(profile, api_key="sk-ant-FAKE")


def test_anthropic_refusal_message_names_the_next_action() -> None:
    """Plain-language UI copy rule: error messages name the next action.
    The Anthropic refusal must tell the user to use an OpenAI-compatible
    profile instead, not just declare the protocol unsupported."""
    profile = _profile(protocol="anthropic", model_id="claude-sonnet-4-6")
    with pytest.raises(ImageProviderError) as exc_info:
        get_image_client_for_profile(profile, api_key="sk-ant-FAKE")
    assert "OpenAI" in str(exc_info.value) or "DALL-E" in str(exc_info.value)


# --------------------------------------------------------------------------- #
# Empty key guard                                                             #
# --------------------------------------------------------------------------- #


def test_empty_api_key_raises_with_plain_language_detail() -> None:
    profile = _profile()
    with pytest.raises(ImageProviderError, match="Settings"):
        get_image_client_for_profile(profile, api_key="")
