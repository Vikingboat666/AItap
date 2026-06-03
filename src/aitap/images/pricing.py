"""Per-image pricing table for cost estimation.

Prices are USD per image. Sources are documented per entry; we
intentionally hard-code a snapshot rather than fetching at runtime so:

- Cost estimates are deterministic across machines and offline.
- Network outages don't break the cost-confirmation gate.
- Reviewers can audit price changes via PR diff.

Mirrors :mod:`aitap.deep.pricing` in shape but with an image-specific
key structure: ``(model, size, quality)`` rather than ``(provider,
model)`` because image price varies inside a model by size and quality.

Models we don't have a price for raise :class:`UnknownImageModelError`
from :func:`estimate_image_cost` rather than silently returning 0 —
silent zero defeats the "always show cost before spending" guarantee
the cost-confirmation gate (``docs/wave-5-design.md`` §"B·Decision 4")
relies on.
"""

from __future__ import annotations

from dataclasses import dataclass

from aitap.images.client import ImageCostEstimate, ImageQuality, ImageSize

LAST_UPDATED = "2026-06-02"


class UnknownImageModelError(KeyError):
    """Raised when we can't price an image-generation model — never silent 0."""


@dataclass(frozen=True)
class _ImagePrice:
    """Per-image USD price tagged with its source URL."""

    usd_per_image: float
    source: str


# DALL-E 3 pricing (https://openai.com/api/pricing — checked 2026-06-02).
# Standard tier vs HD tier prices differ for every supported size.
# The 1024x1024 square is the cheapest at $0.040 standard / $0.080 HD;
# the two portrait/landscape rectangles share the same higher tier of
# $0.080 standard / $0.120 HD.
_DALL_E_3: dict[tuple[ImageSize, ImageQuality], _ImagePrice] = {
    ("1024x1024", "standard"): _ImagePrice(0.040, "openai.com/api/pricing"),
    ("1024x1024", "hd"): _ImagePrice(0.080, "openai.com/api/pricing"),
    ("1024x1792", "standard"): _ImagePrice(0.080, "openai.com/api/pricing"),
    ("1024x1792", "hd"): _ImagePrice(0.120, "openai.com/api/pricing"),
    ("1792x1024", "standard"): _ImagePrice(0.080, "openai.com/api/pricing"),
    ("1792x1024", "hd"): _ImagePrice(0.120, "openai.com/api/pricing"),
}

# DALL-E 2 pricing (https://openai.com/api/pricing — checked 2026-06-02).
# DALL-E 2 has no quality tier on the wire; we accept both quality
# values and price them identically so the lookup key is uniform.
_DALL_E_2: dict[tuple[ImageSize, ImageQuality], _ImagePrice] = {
    ("1024x1024", "standard"): _ImagePrice(0.020, "openai.com/api/pricing"),
    ("1024x1024", "hd"): _ImagePrice(0.020, "openai.com/api/pricing"),
    ("512x512", "standard"): _ImagePrice(0.018, "openai.com/api/pricing"),
    ("512x512", "hd"): _ImagePrice(0.018, "openai.com/api/pricing"),
    ("256x256", "standard"): _ImagePrice(0.016, "openai.com/api/pricing"),
    ("256x256", "hd"): _ImagePrice(0.016, "openai.com/api/pricing"),
}


# Master table keyed by (model, size, quality). Vendor-specific maps
# above merge into this single namespace at module load time. A model
# name collision across vendors would silently win whichever import
# order runs last; we don't have one today (dall-e-2 / dall-e-3 are
# vendor-distinctive) but a future row should pick a vendor-prefixed
# name to stay safe — same convention as :mod:`aitap.deep.pricing`.
_PRICES: dict[tuple[str, ImageSize, ImageQuality], _ImagePrice] = {}
for _size_quality, _price in _DALL_E_3.items():
    _size, _quality = _size_quality
    _PRICES[("dall-e-3", _size, _quality)] = _price
for _size_quality, _price in _DALL_E_2.items():
    _size, _quality = _size_quality
    _PRICES[("dall-e-2", _size, _quality)] = _price


def known_image_models() -> list[str]:
    """Return the list of priced image models, sorted."""
    return sorted({model for model, _, _ in _PRICES})


def estimate_image_cost(
    model: str,
    *,
    size: ImageSize,
    quality: ImageQuality = "standard",
    n: int = 1,
) -> ImageCostEstimate:
    """Compute USD cost for ``n`` images of ``(model, size, quality)``.

    Raises :class:`UnknownImageModelError` when the triple isn't priced
    — callers should surface this to the user (the cost-gate UI renders
    ``cost: unknown`` for these rather than letting an unpriced call
    run "for free").
    """
    if n < 1:
        raise ValueError("n must be >= 1")
    price = _PRICES.get((model, size, quality))
    if price is None:
        raise UnknownImageModelError(
            f"no pricing for (model={model!r}, size={size!r}, quality={quality!r}); "
            "add to images/pricing.py or pin a supported (model, size, quality)"
        )
    return ImageCostEstimate(
        usd=price.usd_per_image * n,
        model=model,
        n=n,
        size=size,
        quality=quality,
    )


__all__ = [
    "LAST_UPDATED",
    "UnknownImageModelError",
    "estimate_image_cost",
    "known_image_models",
]
