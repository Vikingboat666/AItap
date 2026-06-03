"""OpenAI Images API binding for :class:`ImageClient`.

Lazy-imports the ``openai`` SDK; uses the >= 1.30 client surface,
specifically the ``images.generate`` resource which accepts the
``model``, ``prompt``, ``n``, ``size``, ``quality``, and
``response_format`` knobs (the last is pinned to ``"b64_json"`` so the
decoded bytes can be handed straight to the run sidecar without a
second HTTP fetch — see ``docs/wave-5-design.md`` §"B·Decision 3").

PR #35 B2 anti-leak pattern is carried forward verbatim: SDK exception
bodies must **never** reach the user-facing ``ImageProviderError``
message or any log line — the route layer surfaces a static
plain-language detail and the technical body stays on the original
exception's ``__cause__`` for the maintainer to inspect manually.
"""

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
# pyright: reportUnknownVariableType=false, reportAttributeAccessIssue=false
# Lazy-imported optional SDK — type stubs aren't visible at module load.

from __future__ import annotations

import base64
from typing import Any

from aitap.images.client import (
    GeneratedImage,
    ImageClient,
    ImageCostEstimate,
    ImageGenerationResponse,
    ImageProviderAuthError,
    ImageProviderError,
    ImageProviderRateLimitError,
    ImageQuality,
    ImageSize,
    ImageTokenUsage,
    register_image_provider,
    validate_generation_kwargs,
)
from aitap.images.pricing import UnknownImageModelError, estimate_image_cost


class OpenAIImageClient(ImageClient):
    """Speaks the OpenAI Images wire protocol against any compatible host.

    Mirrors :class:`aitap.deep.openai_client.OpenAICompatClient` for the
    chat surface: ``base_url`` is **mandatory**, ``api_key`` is
    **mandatory**, and the rest of the surface (request shaping, error
    mapping, cost estimation) wraps a thin call into the OpenAI SDK.

    The class deliberately does NOT call into :mod:`aitap.secrets` —
    that's the route layer's job. The future ``image-dispatch`` worktree
    resolves the key per profile via
    :func:`aitap.secrets.get_key_for_profile` and hands it to the
    constructor here, keeping the secrets boundary clean and the
    AST-discipline allow-list short.
    """

    def __init__(self, *, base_url: str, model: str, api_key: str) -> None:
        if not base_url:
            raise ValueError("base_url is required for OpenAIImageClient")
        if not api_key:
            raise ValueError("api_key is required for OpenAIImageClient")
        super().__init__(model, api_key)
        self.base_url = base_url

    @property
    def provider_name(self) -> str:
        # Constant string so callers branching on the value treat every
        # OpenAI-compatible image endpoint the same (matches the
        # OpenAICompatClient convention on the chat side).
        return "openai"

    async def generate(
        self,
        prompt: str,
        *,
        size: ImageSize,
        quality: ImageQuality = "standard",
        n: int = 1,
        seed: int | None = None,
    ) -> ImageGenerationResponse:
        validate_generation_kwargs(prompt, n)
        AsyncOpenAI, sdk_errors = _import_openai()
        client = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key)

        kwargs: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "n": n,
            "size": size,
            # ``response_format="b64_json"`` keeps the call single-trip:
            # the SDK returns base64 the client decodes in-process,
            # which sidesteps the URL-expiry trap on the alternative
            # ``"url"`` response shape. ``docs/wave-5-design.md``
            # §"B·Decision 3" pins the storage layout for the decoded
            # bytes.
            "response_format": "b64_json",
        }
        # DALL-E 3 honours ``quality`` ("standard"/"hd"). Recent OpenAI
        # SDK builds reject ``quality="hd"`` against ``model="dall-e-2"``
        # with a 400 — drop the field for DALL-E 2 so a HD-by-default UI
        # picker doesn't trip a wire-level error the anti-leak guard
        # would then re-map to a confusing generic "retry" detail.
        if not self.model.startswith("dall-e-2"):
            kwargs["quality"] = quality

        try:
            raw = await client.images.generate(**kwargs)
        except sdk_errors.AuthenticationError as exc:
            # Static plain-language detail; SDK body stays on
            # ``__cause__`` per the PR #35 B2 anti-leak discipline.
            raise ImageProviderAuthError(
                "OpenAI rejected the image key. Open Settings to fix it."
            ) from exc
        except sdk_errors.RateLimitError as exc:
            raise ImageProviderRateLimitError(
                "OpenAI image API is rate-limited right now. Wait a bit and retry."
            ) from exc
        except sdk_errors.APIError as exc:
            raise ImageProviderError(
                "OpenAI image API returned an error. Retry or check the OpenAI status page."
            ) from exc

        # Parse the SDK response. ``raw.data`` is a list of image
        # records carrying base64-encoded payloads under ``b64_json``.
        # We decode to raw bytes here so the caller never sees base64.
        images: list[GeneratedImage] = []
        width, height = _parse_size(size)
        for record in raw.data:
            b64 = getattr(record, "b64_json", None) or ""
            try:
                payload = base64.b64decode(b64) if b64 else b""
            except (ValueError, TypeError) as exc:
                # Malformed base64 from the provider should never bleed
                # the SDK exception string out; use a static message.
                raise ImageProviderError(
                    "OpenAI returned an image we couldn't decode. Retry the generation."
                ) from exc
            images.append(
                GeneratedImage(
                    bytes=payload,
                    width=width,
                    height=height,
                    mime_type="image/png",
                    seed=seed,
                )
            )

        usage = ImageTokenUsage(images_generated=len(images))
        cost = _safe_image_cost(self.model, size=size, quality=quality, n=len(images))

        return ImageGenerationResponse(
            images=images,
            model=self.model,
            usage=usage,
            cost_usd=cost,
        )

    def estimate_cost(
        self,
        prompt: str,
        *,
        size: ImageSize,
        quality: ImageQuality = "standard",
        n: int = 1,
    ) -> ImageCostEstimate:
        validate_generation_kwargs(prompt, n)
        # ``prompt`` is accepted for ABC symmetry but DALL-E pricing
        # doesn't depend on prompt length — the pricing table keys on
        # (model, size, quality, n) only.
        del prompt
        try:
            return estimate_image_cost(self.model, size=size, quality=quality, n=n)
        except UnknownImageModelError:
            # Public surface degrades to a 0-cost estimate so the cost
            # gate can still decide; the route layer is responsible for
            # rendering ``cost: unknown`` to the UI (same convention as
            # the chat side, see OpenAICompatClient.estimate_cost).
            return ImageCostEstimate(
                usd=0.0,
                model=self.model,
                n=n,
                size=size,
                quality=quality,
            )


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _import_openai() -> tuple[Any, Any]:
    """Lazy import of the ``openai`` SDK.

    Same Any-typed pattern as
    :func:`aitap.deep.openai_client._import_sdk` — keeps pyright strict
    happy when the optional ``[openai]`` extra isn't installed.
    """
    try:
        import openai  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImageProviderError(
            "OpenAI SDK is not installed. Install it with: pip install 'aitap[openai]'"
        ) from exc
    return openai.AsyncOpenAI, openai


def _parse_size(size: ImageSize) -> tuple[int, int]:
    """Split a ``WxH`` size token into (width, height) ints.

    The :data:`ImageSize` literal is narrow enough that we trust the
    format, but we still guard the ``int()`` so a future contributor who
    widens the literal without updating this helper gets a clear error.
    """
    try:
        w_str, h_str = size.split("x", 1)
        return int(w_str), int(h_str)
    except (ValueError, AttributeError) as exc:
        raise ImageProviderError(
            f"Internal: image size {size!r} doesn't match the WxH format."
        ) from exc


def _safe_image_cost(model: str, *, size: ImageSize, quality: ImageQuality, n: int) -> float:
    """Look up cost; degrade unpriced models to 0 USD on the public surface.

    Unpriced models return 0 USD from this internal helper — the route
    layer is the seam where the UI renders ``cost: unknown``. Same
    "0 means unknown" convention :func:`aitap.deep.openai_client._safe_cost`
    uses on the chat side.
    """
    if n < 1:
        return 0.0
    try:
        estimate = estimate_image_cost(model, size=size, quality=quality, n=n)
    except UnknownImageModelError:
        return 0.0
    return estimate.usd


register_image_provider(
    "openai",
    lambda model, key: OpenAIImageClient(
        # The registry signature passes a single API key — the registry
        # is only meant for the simple "I know the provider name and a
        # key" case. The richer profile-aware construction goes through
        # ``aitap.images.factory.get_image_client_for_profile`` instead,
        # which knows about ``base_url``. For the registry path we fall
        # back to the canonical OpenAI endpoint so a plain
        # ``get_image_client("openai", "dall-e-3", key)`` works.
        base_url="https://api.openai.com/v1",
        model=model,
        api_key=key or "",
    ),
)


__all__ = ["OpenAIImageClient"]
