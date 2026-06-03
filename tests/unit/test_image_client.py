"""Tests for the ImageClient ABC + registry.

Wave 5 Part B (``docs/wave-5-design.md`` §"B·Decision 1") settled on
a separate ImageClient abstraction parallel to LLMClient rather than an
extension of it. These tests pin both halves of that decision:

- The ABC's surface (``provider_name`` / ``generate`` / ``estimate_cost``)
  exists and is genuinely abstract.
- The image-provider registry is **separate** from the chat registry —
  registering an image provider must not poison the chat namespace and
  vice versa.
"""

from __future__ import annotations

import pytest

from aitap.deep.client import _REGISTRY as _CHAT_REGISTRY  # type: ignore[attr-defined]
from aitap.images.client import _REGISTRY as _IMAGE_REGISTRY
from aitap.images.client import (
    GeneratedImage,
    ImageClient,
    ImageCostEstimate,
    ImageGenerationRequest,
    ImageGenerationResponse,
    ImageProviderAuthError,
    ImageProviderError,
    ImageProviderRateLimitError,
    ImageTokenUsage,
    get_image_client,
    list_image_providers,
    register_image_provider,
    validate_generation_kwargs,
)

# --------------------------------------------------------------------------- #
# ABC surface                                                                 #
# --------------------------------------------------------------------------- #


def test_image_client_is_abstract() -> None:
    """Instantiating the bare ABC must fail — every concrete subclass is
    expected to implement ``provider_name`` / ``generate`` /
    ``estimate_cost``."""
    with pytest.raises(TypeError):
        ImageClient(model="dall-e-3")  # type: ignore[abstract]


def test_concrete_subclass_can_construct_and_exposes_provider_name() -> None:
    class _Fake(ImageClient):
        @property
        def provider_name(self) -> str:
            return "fake"

        async def generate(
            self,
            prompt: str,
            *,
            size: object,  # type: ignore[override]
            quality: object = "standard",  # type: ignore[override]
            n: int = 1,
            seed: int | None = None,
        ) -> ImageGenerationResponse:  # pragma: no cover - unused in this test
            raise NotImplementedError

        def estimate_cost(
            self,
            prompt: str,
            *,
            size: object,  # type: ignore[override]
            quality: object = "standard",  # type: ignore[override]
            n: int = 1,
        ) -> ImageCostEstimate:  # pragma: no cover - unused in this test
            raise NotImplementedError

    client = _Fake(model="fake-model", api_key="sk-FAKE")
    assert client.model == "fake-model"
    assert client.api_key == "sk-FAKE"
    assert client.provider_name == "fake"


# --------------------------------------------------------------------------- #
# Value-object validation                                                     #
# --------------------------------------------------------------------------- #


def test_image_generation_request_rejects_blank_prompt() -> None:
    """The Pydantic model enforces ``min_length=1`` so a blank prompt
    can't reach a provider — the cost gate would otherwise charge for
    an empty call."""
    with pytest.raises(ValueError):
        ImageGenerationRequest(prompt="", size="1024x1024", n=1)


def test_image_generation_request_rejects_zero_n() -> None:
    with pytest.raises(ValueError):
        ImageGenerationRequest(prompt="cat", size="1024x1024", n=0)


def test_image_generation_request_rejects_oversize_n() -> None:
    """n is capped at 10 so a typo can't accidentally fan out a paid
    call by orders of magnitude."""
    with pytest.raises(ValueError):
        ImageGenerationRequest(prompt="cat", size="1024x1024", n=11)


# --------------------------------------------------------------------------- #
# validate_generation_kwargs — N1 follow-up                                   #
# --------------------------------------------------------------------------- #
#
# The Pydantic model above advertises ``prompt`` non-empty and
# ``1 <= n <= 10``, but the abstract ``generate`` / ``estimate_cost``
# take raw kwargs — the Pydantic guards only fire when the caller
# materialises an :class:`ImageGenerationRequest`. The helper enforces
# the same invariants on the kwargs path so every implementation gets
# the guarantee for free; these tests pin its behaviour.


def test_validate_generation_kwargs_accepts_typical_call() -> None:
    # Returning ``None`` is success — nothing to assert beyond no raise.
    validate_generation_kwargs("a cat", n=1)
    validate_generation_kwargs("a cat", n=10)


def test_validate_generation_kwargs_rejects_empty_prompt() -> None:
    with pytest.raises(ValueError, match="prompt cannot be empty"):
        validate_generation_kwargs("", n=1)


def test_validate_generation_kwargs_rejects_whitespace_only_prompt() -> None:
    with pytest.raises(ValueError, match="prompt cannot be empty"):
        validate_generation_kwargs("   \n\t  ", n=1)


def test_validate_generation_kwargs_rejects_zero_n() -> None:
    with pytest.raises(ValueError, match=r"n must be >= 1"):
        validate_generation_kwargs("a cat", n=0)


def test_validate_generation_kwargs_rejects_oversize_n() -> None:
    with pytest.raises(ValueError, match=r"n must be <= 10"):
        validate_generation_kwargs("a cat", n=11)


def test_generated_image_carries_bytes_and_dimensions() -> None:
    img = GeneratedImage(
        bytes=b"\x89PNG\r\n\x1a\n",
        width=1024,
        height=1024,
        mime_type="image/png",
        seed=42,
    )
    assert img.bytes.startswith(b"\x89PNG")
    assert img.width == 1024
    assert img.height == 1024
    assert img.seed == 42


def test_image_token_usage_allows_none_prompt_tokens() -> None:
    """Not every provider returns a prompt-token count for image calls;
    the slot is optional so the response model doesn't force a fake."""
    usage = ImageTokenUsage(images_generated=4)
    assert usage.images_generated == 4
    assert usage.prompt_tokens is None


def test_image_cost_estimate_round_trip() -> None:
    estimate = ImageCostEstimate(
        usd=0.16,
        model="dall-e-3",
        n=2,
        size="1024x1024",
        quality="hd",
    )
    assert estimate.usd == pytest.approx(0.16)
    assert estimate.quality == "hd"


# --------------------------------------------------------------------------- #
# Exception taxonomy mirrors the chat side                                    #
# --------------------------------------------------------------------------- #


def test_auth_error_subclasses_image_provider_error() -> None:
    assert issubclass(ImageProviderAuthError, ImageProviderError)


def test_rate_limit_error_subclasses_image_provider_error() -> None:
    assert issubclass(ImageProviderRateLimitError, ImageProviderError)


# --------------------------------------------------------------------------- #
# Registry behaviour                                                          #
# --------------------------------------------------------------------------- #


def test_register_and_list_image_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """A registered factory shows up in ``list_image_providers`` and
    ``get_image_client`` returns its product."""
    # Snapshot the live registry so the test is isolated even if other
    # providers self-register on import.
    snapshot = dict(_IMAGE_REGISTRY)
    monkeypatch.setattr("aitap.images.client._REGISTRY", snapshot)

    class _Stub(ImageClient):
        @property
        def provider_name(self) -> str:
            return "stub"

        async def generate(  # pragma: no cover - unused in this test
            self,
            prompt: str,
            *,
            size: object,  # type: ignore[override]
            quality: object = "standard",  # type: ignore[override]
            n: int = 1,
            seed: int | None = None,
        ) -> ImageGenerationResponse:
            raise NotImplementedError

        def estimate_cost(  # pragma: no cover - unused in this test
            self,
            prompt: str,
            *,
            size: object,  # type: ignore[override]
            quality: object = "standard",  # type: ignore[override]
            n: int = 1,
        ) -> ImageCostEstimate:
            raise NotImplementedError

    register_image_provider("stub", lambda model, key: _Stub(model, key))
    assert "stub" in list_image_providers()

    client = get_image_client("stub", model="stub-model", api_key="sk-x")
    assert isinstance(client, _Stub)
    assert client.model == "stub-model"
    assert client.api_key == "sk-x"


def test_get_image_client_raises_for_unknown_provider() -> None:
    """Unknown providers raise a plain-language ``ImageProviderError``
    that names the next action (``pip install`` extra). The string is
    static so an SDK exception body can never poison the message."""
    with pytest.raises(ImageProviderError, match=r"pip install"):
        get_image_client("definitely-not-a-real-provider", model="x")


def test_image_registry_is_separate_from_chat_registry() -> None:
    """Wave 5 Part B Decision 1: a provider name like ``"openai"`` may
    register under both the chat (``aitap.deep.client``) and image
    (``aitap.images.client``) namespaces independently. Pin that the
    two registries are distinct mappings — sharing one would force a
    fake compound key.
    """
    assert _CHAT_REGISTRY is not _IMAGE_REGISTRY
