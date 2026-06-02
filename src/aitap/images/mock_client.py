"""Offline :class:`ImageClient` for tests and demos.

The Wave 5 Part B design doc (``docs/wave-5-design.md`` §"Image —
testing") calls for an offline image client so the future
``image-dispatch`` and ``image-ui`` test suites can exercise the grid
end-to-end without burning real DALL-E spend or requiring a network
connection in CI.

This client returns a deterministic 1x1 PNG payload (a minimal valid
PNG file embedded as a class constant) for every request. The image
bytes are constant across calls — tests can byte-compare the returned
:class:`GeneratedImage.bytes` to a fixture.

Cost estimation always returns 0 USD; the mock isn't priced (and it
shouldn't be — if a test path forgets to swap the mock for a real
client and runs against production, the cost-gate UI rendering
``cost: unknown`` is exactly the safety net we want, not a silent
$0.00).
"""

from __future__ import annotations

from aitap.images.client import (
    GeneratedImage,
    ImageClient,
    ImageCostEstimate,
    ImageGenerationResponse,
    ImageQuality,
    ImageSize,
    ImageTokenUsage,
    register_image_provider,
)

# A genuine, minimal 1x1 transparent PNG. Constructed by hand once and
# pasted here so unit tests can byte-compare to a constant. The eight
# header bytes are the PNG magic; the IHDR / IDAT / IEND chunks declare
# a 1x1 RGBA image with a single transparent pixel. CRCs are correct so
# Pillow / browser image decoders read it as a valid PNG.
_ONE_PIXEL_TRANSPARENT_PNG: bytes = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDAT"
    b"x\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


class MockImageClient(ImageClient):
    """In-process :class:`ImageClient` that returns deterministic bytes.

    Never makes a network call; the SDK is never imported. Useful both
    as a substitute in dispatch / route tests and as a documentation
    example of what the contract surface looks like to a consumer.

    The constructor accepts a positional ``model`` and optional
    ``api_key`` so the class is drop-in compatible with the registry
    factory signature, even though neither value influences behaviour.
    """

    @property
    def provider_name(self) -> str:
        return "mock"

    async def generate(
        self,
        prompt: str,
        *,
        size: ImageSize,
        quality: ImageQuality = "standard",
        n: int = 1,
        seed: int | None = None,
    ) -> ImageGenerationResponse:
        # ``prompt`` and ``quality`` are accepted for ABC symmetry but
        # don't change the deterministic output. ``size`` is parsed so
        # the returned :class:`GeneratedImage.width` / ``.height`` match
        # what the grid UI will lay out.
        del prompt, quality
        width, height = _parse_size(size)
        images = [
            GeneratedImage(
                bytes=_ONE_PIXEL_TRANSPARENT_PNG,
                width=width,
                height=height,
                mime_type="image/png",
                seed=seed,
            )
            for _ in range(n)
        ]
        return ImageGenerationResponse(
            images=images,
            model=self.model,
            usage=ImageTokenUsage(images_generated=n),
            cost_usd=0.0,
        )

    def estimate_cost(
        self,
        prompt: str,
        *,
        size: ImageSize,
        quality: ImageQuality = "standard",
        n: int = 1,
    ) -> ImageCostEstimate:
        del prompt
        return ImageCostEstimate(
            usd=0.0,
            model=self.model,
            n=n,
            size=size,
            quality=quality,
        )


def _parse_size(size: ImageSize) -> tuple[int, int]:
    w_str, h_str = size.split("x", 1)
    return int(w_str), int(h_str)


# Register under a stable provider name so test suites can grab it via
# the registry path (``get_image_client("mock", ...)``) without
# importing this module directly.
register_image_provider(
    "mock",
    lambda model, key: MockImageClient(model, key),
)


__all__ = ["MockImageClient"]
