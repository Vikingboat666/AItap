"""Tests for the price table.

The pricing table is the contract every cost gate relies on; the most
important property is "if a model name is in known_models(), it must be
priced for both input and output tokens".
"""

from __future__ import annotations

import pytest

from aitap.deep import pricing


def test_known_models_returns_both_providers() -> None:
    all_models = pricing.known_models()
    assert any(m.startswith("claude") for m in all_models)
    assert any(m.startswith("gpt") for m in all_models)


def test_known_models_filters_by_provider() -> None:
    anthropic_models = pricing.known_models("anthropic")
    openai_models = pricing.known_models("openai")
    assert all(m.startswith(("claude", "haiku", "opus", "sonnet")) for m in anthropic_models)
    assert all(m.startswith(("gpt", "o1")) for m in openai_models)


@pytest.mark.parametrize("model", pricing.known_models())
def test_every_known_model_has_pricing(model: str) -> None:
    """Find which provider this model belongs to and verify cost > 0 for non-zero usage."""
    for provider in ("anthropic", "openai"):
        try:
            cost = pricing.estimate_usd(provider, model, input_tokens=1000, output_tokens=1000)
            assert cost > 0, f"{provider}/{model} priced 0 for 1k+1k tokens"
            return
        except pricing.UnknownModelError:
            continue
    pytest.fail(f"model {model} listed in known_models() but no pricing entry")


def test_estimate_usd_raises_for_unknown_model() -> None:
    with pytest.raises(pricing.UnknownModelError):
        pricing.estimate_usd("anthropic", "non-existent-model", input_tokens=10, output_tokens=10)


def test_estimate_usd_zero_tokens_zero_cost() -> None:
    cost = pricing.estimate_usd("openai", "gpt-4o-mini", input_tokens=0, output_tokens=0)
    assert cost == 0.0


def test_pricing_is_provider_specific() -> None:
    """Same model name shouldn't accidentally exist under both providers."""
    anthropic_models = set(pricing.known_models("anthropic"))
    openai_models = set(pricing.known_models("openai"))
    assert anthropic_models.isdisjoint(openai_models)


def test_last_updated_is_iso_date() -> None:
    """Helps the maintainer audit how stale the price table is."""
    import datetime

    parsed = datetime.date.fromisoformat(pricing.LAST_UPDATED)
    assert parsed.year >= 2026
