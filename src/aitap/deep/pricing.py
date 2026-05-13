"""Per-model pricing table for cost estimation.

Prices are USD per 1,000 tokens. Sources are documented per entry; we
intentionally hard-code a snapshot rather than fetching at runtime so:

- Cost estimates are deterministic across machines and offline.
- Network outages don't break ``aitap scan --deep``.
- Reviewers can audit price changes via PR diff.

When a provider raises or lowers prices, bump ``LAST_UPDATED`` and
update the affected rows. Tests in ``test_pricing.py`` enforce that
every model returned by ``known_models()`` has both input and output
prices defined.

Models we don't have a price for raise :class:`UnknownModelError` from
:func:`estimate_usd` rather than silently returning 0 — silent zero
defeats the whole "always show cost before spending" guarantee.
"""

from __future__ import annotations

from dataclasses import dataclass

LAST_UPDATED = "2026-05-12"


class UnknownModelError(KeyError):
    """Raised when we can't price a model — never silently return 0."""


@dataclass(frozen=True)
class _Price:
    """Per-1k-token prices in USD."""

    input_per_1k: float
    output_per_1k: float
    source: str


# Anthropic pricing (https://www.anthropic.com/pricing — checked 2026-05-12)
_ANTHROPIC: dict[str, _Price] = {
    "claude-opus-4-7": _Price(15.00 / 1000, 75.00 / 1000, "anthropic.com/pricing"),
    "claude-sonnet-4-6": _Price(3.00 / 1000, 15.00 / 1000, "anthropic.com/pricing"),
    "claude-haiku-4-5-20251001": _Price(0.80 / 1000, 4.00 / 1000, "anthropic.com/pricing"),
}

# OpenAI pricing (https://openai.com/api/pricing — checked 2026-05-12)
_OPENAI: dict[str, _Price] = {
    "gpt-4o": _Price(2.50 / 1000, 10.00 / 1000, "openai.com/api/pricing"),
    "gpt-4o-mini": _Price(0.15 / 1000, 0.60 / 1000, "openai.com/api/pricing"),
    "o1-mini": _Price(3.00 / 1000, 12.00 / 1000, "openai.com/api/pricing"),
}


_PRICES: dict[tuple[str, str], _Price] = {}
for model, price in _ANTHROPIC.items():
    _PRICES[("anthropic", model)] = price
for model, price in _OPENAI.items():
    _PRICES[("openai", model)] = price


def known_models(provider: str | None = None) -> list[str]:
    """Return the list of priced models (optionally filtered by provider)."""
    if provider is None:
        return sorted({m for _, m in _PRICES})
    return sorted(m for p, m in _PRICES if p == provider)


def estimate_usd(
    provider: str,
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Compute USD cost from token counts.

    Raises :class:`UnknownModelError` when ``(provider, model)`` isn't priced —
    callers should surface this to the user rather than letting an
    unpriced call run "for free".
    """
    price = _PRICES.get((provider, model))
    if price is None:
        raise UnknownModelError(
            f"no pricing for ({provider!r}, {model!r}); add to deep/pricing.py or pin a known model"
        )
    return price.input_per_1k * input_tokens / 1000 + price.output_per_1k * output_tokens / 1000
