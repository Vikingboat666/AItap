"""ImageClient contract.

Contract version: 1 (2026-06-02)

Provider-agnostic abstraction for text-to-image generation, parallel to
:class:`aitap.deep.client.LLMClient`. The Wave 5 Part B design doc
(``docs/wave-5-design.md`` §"B·Decision 1") records the rationale: image
generation has a different call shape than chat, and overloading the
chat ABC with ``generate_image`` would force every chat provider to
stub a method it can't implement.

Concrete providers live next to this file (``openai_client.py``,
``mock_client.py``) and register themselves via
:func:`register_image_provider`. Lazy imports keep the SDK deps optional
— installing aitap without the ``[openai]`` extra still works as long
as you don't try to actually generate an image.

Example consumer (route layer, future ``image-dispatch`` worktree):

    from aitap.images.factory import get_image_client_for_profile
    client = get_image_client_for_profile(profile, api_key=resolved_key)
    estimate = client.estimate_cost(prompt, size="1024x1024", quality="standard", n=4)
    if estimate.usd > 0.10:
        confirm_with_user(estimate)
    response = await client.generate(prompt, size="1024x1024", quality="standard", n=4)
    for img in response.images:
        write_png(img.bytes)
"""

from __future__ import annotations

import abc
from collections.abc import Callable
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- #
# Request / response value types                                              #
# --------------------------------------------------------------------------- #

# Standardised size tokens accepted across providers. The literal is
# narrow on purpose — the UI surfaces a fixed picker so a typo can't
# reach the wire. Individual providers may support a subset; an
# unsupported (model, size) pair is the provider implementation's
# responsibility to raise on.
ImageSize = Literal[
    "256x256",
    "512x512",
    "1024x1024",
    "1024x1792",
    "1792x1024",
]

# Quality knob. DALL-E 3 distinguishes standard / hd at different price
# points; DALL-E 2 ignores the field. We always send a value so the
# pricing-table lookup has a deterministic key.
ImageQuality = Literal["standard", "hd"]


class ImageGenerationRequest(BaseModel):
    """Validated input shape for :meth:`ImageClient.generate`.

    Kept frozen so the request can be hashed for deduplication / cache
    keys in a future caching layer without surprises.
    """

    model_config = ConfigDict(frozen=True)

    prompt: str = Field(min_length=1)
    size: ImageSize
    quality: ImageQuality = "standard"
    n: int = Field(ge=1, le=10)
    seed: int | None = None


class GeneratedImage(BaseModel):
    """One image returned from a generation call.

    ``bytes`` is the raw decoded payload (PNG / JPEG / whatever the
    provider returned). The ``image-dispatch`` worktree is responsible
    for writing it to disk at
    ``.aitap/runs/<id>/images/<case_index>_<variant>.png`` per
    ``docs/wave-5-design.md`` §"B·Decision 3"; this layer is bytes-in /
    bytes-out and never touches the filesystem.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    bytes: bytes
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    mime_type: str = "image/png"
    seed: int | None = None


class ImageTokenUsage(BaseModel):
    """Per-call usage counters surfaced to the cost-gate UI.

    Image providers don't bill on tokens the way chat providers do, so
    the field naming is intentionally different: ``images_generated`` is
    the count that drives the price-per-image cost-table lookup. The
    optional ``prompt_tokens`` slot is filled when the provider returns
    one (some endpoints do), and stays ``None`` otherwise.
    """

    model_config = ConfigDict(frozen=True)

    images_generated: int = Field(ge=0)
    prompt_tokens: int | None = None


class ImageGenerationResponse(BaseModel):
    """Result of a successful :meth:`ImageClient.generate` call."""

    model_config = ConfigDict(frozen=True)

    images: list[GeneratedImage]
    model: str
    usage: ImageTokenUsage
    cost_usd: float


class ImageCostEstimate(BaseModel):
    """Predicted USD cost of an image generation call before execution.

    Mirrors :class:`aitap.deep.client.CostEstimate` in spirit but keeps
    the image-specific knobs (``size``, ``quality``, ``n``) so the
    cost-confirmation gate (``docs/wave-5-design.md`` §"B·Decision 4")
    can render a clear breakdown.
    """

    model_config = ConfigDict(frozen=True)

    usd: float
    model: str
    n: int = Field(ge=1)
    size: ImageSize
    quality: ImageQuality = "standard"


# --------------------------------------------------------------------------- #
# Exception taxonomy — mirrors deep/client.py                                 #
# --------------------------------------------------------------------------- #


class ImageProviderError(Exception):
    """Base class for image-provider failures (auth, rate limit, transport).

    Mirrors :class:`aitap.deep.client.ProviderError`. The image stack
    keeps its own exception classes (rather than reusing the chat ones)
    so a caller's ``except`` clause can distinguish "image call failed"
    from "chat call failed" without inspecting message text.
    """


class ImageProviderAuthError(ImageProviderError):
    """Missing or invalid API key for an image-generation provider."""


class ImageProviderRateLimitError(ImageProviderError):
    """Provider returned 429 / rate limit on an image-generation call."""


# --------------------------------------------------------------------------- #
# ImageClient ABC                                                             #
# --------------------------------------------------------------------------- #


class ImageClient(abc.ABC):
    """Provider-agnostic text-to-image client.

    Implementations should:

    - Be safe to construct without making any network calls (the SDK is
      lazy-imported on the first :meth:`generate` / :meth:`estimate_cost`
      call so unit tests can build clients freely without the optional
      ``[openai]`` extra installed).
    - Validate API key availability lazily, the same way the chat
      clients do.
    - Wrap provider-specific exceptions in an :class:`ImageProviderError`
      subclass with a static plain-language message (PR #35 B2
      anti-leak: SDK exception bodies must not propagate to user-facing
      ``detail`` strings or logs).
    """

    def __init__(self, model: str, api_key: str | None = None) -> None:
        self.model = model
        self.api_key = api_key

    @property
    @abc.abstractmethod
    def provider_name(self) -> str:
        """Stable identifier (e.g., ``"openai"``, ``"mock"``)."""

    @abc.abstractmethod
    async def generate(
        self,
        prompt: str,
        *,
        size: ImageSize,
        quality: ImageQuality = "standard",
        n: int = 1,
        seed: int | None = None,
    ) -> ImageGenerationResponse:
        """Generate ``n`` images for ``prompt``.

        ``size`` and ``quality`` are passed through to the provider; the
        narrow :data:`ImageSize` / :data:`ImageQuality` literals are
        intentional — every supported size is a row in the pricing
        table, and an unknown value would cost-estimate as zero.

        ``seed`` is forwarded for providers that support reproducible
        sampling; providers that don't are free to ignore it (the
        returned :class:`GeneratedImage.seed` value will be ``None`` in
        that case, signalling non-determinism to the caller).

        Implementations **must** call :func:`validate_generation_kwargs`
        at the top of the body so the invariants the
        :class:`ImageGenerationRequest` Pydantic model advertises
        (non-empty prompt, ``1 <= n <= 10``) hold on the kwargs path too
        — the Pydantic guard only fires when the caller constructs the
        Request object explicitly.
        """

    @abc.abstractmethod
    def estimate_cost(
        self,
        prompt: str,
        *,
        size: ImageSize,
        quality: ImageQuality = "standard",
        n: int = 1,
    ) -> ImageCostEstimate:
        """Estimate USD cost of :meth:`generate` without sending the request.

        ``prompt`` is accepted for API symmetry with the chat layer but
        most providers price purely on (size, quality, n); implementations
        are free to ignore it.

        Implementations **must** call :func:`validate_generation_kwargs`
        at the top of the body so the invariants the
        :class:`ImageGenerationRequest` Pydantic model advertises hold
        on the kwargs path too.
        """


# Maximum image fan-out for a single ``generate`` call. Matches the
# ``ImageGenerationRequest.n`` Pydantic constraint so callers see the
# same ceiling no matter which entry point they use.
_MAX_N_PER_CALL = 10


def validate_generation_kwargs(prompt: str, n: int) -> None:
    """Raise :class:`ValueError` when the kwargs path violates the
    invariants the :class:`ImageGenerationRequest` Pydantic model
    advertises (non-empty prompt, ``1 <= n <= 10``).

    Centralising the guard here means every implementation can keep its
    ``generate`` / ``estimate_cost`` body straightforward and the rules
    only change in one place.
    """
    if not prompt or not prompt.strip():
        raise ValueError("prompt cannot be empty")
    if n < 1:
        raise ValueError(f"n must be >= 1 (got {n})")
    if n > _MAX_N_PER_CALL:
        raise ValueError(
            f"n must be <= {_MAX_N_PER_CALL} (got {n}); "
            "split into multiple generate() calls if you need more."
        )


# --------------------------------------------------------------------------- #
# Provider registry — separate from the chat registry by design (B·D1)        #
# --------------------------------------------------------------------------- #

ImageClientFactory = Callable[[str, str | None], ImageClient]


class _ImageProviderProtocol(Protocol):  # pyright: ignore[reportUnusedClass]
    """Marker protocol so tests can introspect the registry shape."""

    def __call__(self, model: str, api_key: str | None) -> ImageClient: ...


_REGISTRY: dict[str, ImageClientFactory] = {}


def register_image_provider(name: str, factory: ImageClientFactory) -> None:
    """Register an image-provider factory under a canonical name.

    Concrete provider modules call this at import time, e.g.::

        # in aitap/images/openai_client.py
        register_image_provider("openai", lambda model, key: OpenAIImageClient(model, key))

    The image registry is **separate** from
    :data:`aitap.deep.client._REGISTRY` on purpose (Wave 5 Part B
    Decision 1): a provider name like ``"openai"`` can mean two
    completely different clients depending on the surface — chat vs
    image — and merging the two would force a fake compound key.
    """
    _REGISTRY[name] = factory


def list_image_providers() -> list[str]:
    """Return the registered image-provider names, sorted."""
    return sorted(_REGISTRY)


def get_image_client(provider: str, model: str, api_key: str | None = None) -> ImageClient:
    """Get an :class:`ImageClient` for the named provider.

    Triggers a lazy import of the provider module if not yet registered;
    each provider module registers itself at import time. Mirrors the
    pattern in :func:`aitap.deep.client.get_client`.
    """
    if provider not in _REGISTRY:
        # Lazy import to populate the registry on first request.
        try:
            __import__(f"aitap.images.{provider}_client")
        except ImportError as exc:
            raise ImageProviderError(
                f"Image provider '{provider}' is not available. "
                f"Install with: pip install 'aitap[{provider}]'"
            ) from exc

    if provider not in _REGISTRY:
        raise ImageProviderError(f"Image provider '{provider}' did not register itself on import")

    return _REGISTRY[provider](model, api_key)


__all__ = [
    "GeneratedImage",
    "ImageClient",
    "ImageClientFactory",
    "ImageCostEstimate",
    "ImageGenerationRequest",
    "ImageGenerationResponse",
    "ImageProviderAuthError",
    "ImageProviderError",
    "ImageProviderRateLimitError",
    "ImageQuality",
    "ImageSize",
    "ImageTokenUsage",
    "get_image_client",
    "list_image_providers",
    "register_image_provider",
]
