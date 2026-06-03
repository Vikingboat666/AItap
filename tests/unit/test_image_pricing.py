"""Tests for the image pricing table.

Wave 5 Part B (``docs/wave-5-design.md`` §"B·Decision 4") gates every
image-generation call on a USD estimate the user can confirm. The
pricing table is the contract that gate relies on; the most important
properties are:

- Every documented (model, size, quality) row prices to > 0 USD.
- Unknown rows raise :class:`UnknownImageModelError` rather than
  silently returning 0 (the design-doc commitment is to render
  ``cost: unknown`` for those, never to charge as free).
"""

from __future__ import annotations

import datetime

import pytest

from aitap.images import pricing

# --------------------------------------------------------------------------- #
# LAST_UPDATED                                                                #
# --------------------------------------------------------------------------- #


def test_last_updated_is_iso_date() -> None:
    """Helps the maintainer audit how stale the price table is."""
    parsed = datetime.date.fromisoformat(pricing.LAST_UPDATED)
    assert parsed.year >= 2026


# --------------------------------------------------------------------------- #
# DALL-E 3 — six (size, quality) rows                                         #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("size", "quality", "expected_usd"),
    [
        ("1024x1024", "standard", 0.040),
        ("1024x1024", "hd", 0.080),
        ("1024x1792", "standard", 0.080),
        ("1024x1792", "hd", 0.120),
        ("1792x1024", "standard", 0.080),
        ("1792x1024", "hd", 0.120),
    ],
)
def test_dall_e_3_price_table_matches_openai_pricing_page(
    size: str, quality: str, expected_usd: float
) -> None:
    """Pin every documented DALL-E 3 row to the openai.com/api/pricing
    snapshot LAST_UPDATED records. A future maintainer who bumps the
    snapshot must also bump these assertions, keeping the test as a
    fail-fast review aid."""
    estimate = pricing.estimate_image_cost("dall-e-3", size=size, quality=quality, n=1)  # type: ignore[arg-type]
    assert estimate.usd == pytest.approx(expected_usd)
    assert estimate.model == "dall-e-3"
    assert estimate.n == 1


def test_dall_e_3_cost_scales_linearly_with_n() -> None:
    one = pricing.estimate_image_cost("dall-e-3", size="1024x1024", quality="standard", n=1)
    four = pricing.estimate_image_cost("dall-e-3", size="1024x1024", quality="standard", n=4)
    assert four.usd == pytest.approx(one.usd * 4)
    assert four.n == 4


# --------------------------------------------------------------------------- #
# DALL-E 2 — three sizes, quality ignored                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("size", "expected_usd"),
    [
        ("1024x1024", 0.020),
        ("512x512", 0.018),
        ("256x256", 0.016),
    ],
)
def test_dall_e_2_price_table_matches_openai_pricing_page(size: str, expected_usd: float) -> None:
    estimate = pricing.estimate_image_cost("dall-e-2", size=size, quality="standard", n=1)  # type: ignore[arg-type]
    assert estimate.usd == pytest.approx(expected_usd)
    assert estimate.model == "dall-e-2"


def test_dall_e_2_ignores_quality_tier() -> None:
    """DALL-E 2 has no HD tier on the wire; we accept both quality
    values and price them identically so the cost gate doesn't have
    to special-case the model."""
    standard = pricing.estimate_image_cost("dall-e-2", size="1024x1024", quality="standard", n=1)
    hd = pricing.estimate_image_cost("dall-e-2", size="1024x1024", quality="hd", n=1)
    assert standard.usd == hd.usd


# --------------------------------------------------------------------------- #
# Unknown rows                                                                #
# --------------------------------------------------------------------------- #


def test_estimate_image_cost_raises_for_unknown_model() -> None:
    with pytest.raises(pricing.UnknownImageModelError):
        pricing.estimate_image_cost("midjourney-v8", size="1024x1024", quality="standard", n=1)


def test_estimate_image_cost_raises_for_unsupported_size() -> None:
    """DALL-E 3 doesn't sell a 256x256 tier; asking for one must raise
    rather than fall back to a different model's row."""
    with pytest.raises(pricing.UnknownImageModelError):
        pricing.estimate_image_cost("dall-e-3", size="256x256", quality="standard", n=1)  # type: ignore[arg-type]


def test_estimate_image_cost_rejects_zero_n() -> None:
    with pytest.raises(ValueError):
        pricing.estimate_image_cost("dall-e-3", size="1024x1024", quality="standard", n=0)


# --------------------------------------------------------------------------- #
# known_image_models                                                          #
# --------------------------------------------------------------------------- #


def test_known_image_models_lists_both_dall_e_versions() -> None:
    models = pricing.known_image_models()
    assert "dall-e-2" in models
    assert "dall-e-3" in models
