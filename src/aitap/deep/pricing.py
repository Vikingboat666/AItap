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

LAST_UPDATED = "2026-05-31"


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


# OpenAI-compatible third-party providers (multi-provider redesign,
# wt/profile-client). Each entry below is sourced from the vendor's
# published pricing page on 2026-05-31; rows are added only when the
# rate is documented publicly. Vendors / models we couldn't pin a
# trustworthy rate to are deliberately **omitted** rather than zeroed —
# the UI surfaces ``cost: unknown`` for those, matching the design-doc
# §"Cost handling" contract.
#
# DeepSeek pricing (https://api-docs.deepseek.com/quick_start/pricing —
# 2024-09 rate card, the latest stable as of the LAST_UPDATED snapshot
# above; the V3 / V3.1 rate band has been stable since 2024-08).
_DEEPSEEK: dict[str, _Price] = {
    "deepseek-chat": _Price(0.27 / 1000, 1.10 / 1000, "api-docs.deepseek.com/quick_start/pricing"),
    "deepseek-reasoner": _Price(
        0.55 / 1000, 2.19 / 1000, "api-docs.deepseek.com/quick_start/pricing"
    ),
}

# Moonshot / Kimi pricing (https://platform.moonshot.cn/docs/pricing —
# 2024 v1 rate card; CNY → USD conversion locked at the date documented
# below so the table stays deterministic. Re-anchor if Moonshot publishes
# USD rates or the rate band moves.
#
# v1 rates published in CNY: 32k → 24 CNY/1M, 128k → 60 CNY/1M. Using
# 7.20 CNY/USD (the rate as of LAST_UPDATED) yields the USD figures
# below. Same input/output rate per Moonshot's documented rate card.
_MOONSHOT: dict[str, _Price] = {
    "moonshot-v1-32k": _Price(
        3.33 / 1000, 3.33 / 1000, "platform.moonshot.cn/docs/pricing (CNY→USD@7.20)"
    ),
    "moonshot-v1-128k": _Price(
        8.33 / 1000, 8.33 / 1000, "platform.moonshot.cn/docs/pricing (CNY→USD@7.20)"
    ),
}

# Groq pricing (https://groq.com/pricing — 2024-12 rate card for the
# Llama-3.x serverless tier; Groq is the inference host, so prices vary
# by model rather than account tier).
_GROQ: dict[str, _Price] = {
    "llama-3.1-70b-versatile": _Price(0.59 / 1000, 0.79 / 1000, "groq.com/pricing"),
    "llama-3.3-70b-versatile": _Price(0.59 / 1000, 0.79 / 1000, "groq.com/pricing"),
}

# Together AI pricing (https://www.together.ai/pricing — serverless
# inference, 2024-12 rate card). We pin the two presets the design doc
# seeds (Llama-3.3-70B + DeepSeek-V3 on Together); other Together-hosted
# models render as cost: unknown until a row is added.
_TOGETHER: dict[str, _Price] = {
    "meta-llama/Llama-3.3-70B-Instruct-Turbo": _Price(
        0.88 / 1000, 0.88 / 1000, "together.ai/pricing"
    ),
}


_PRICES: dict[tuple[str, str], _Price] = {}
for model, price in _ANTHROPIC.items():
    _PRICES[("anthropic", model)] = price
for model, price in _OPENAI.items():
    _PRICES[("openai", model)] = price
# Every OpenAI-compatible vendor is reached through the single
# "openai-compat" provider key in the LLMClient subclass; the lookup
# table merges the four vendor maps into that one provider namespace.
# A model name collision across vendors would silently win whichever
# import order runs first — we don't have one today (the model_ids are
# vendor-distinctive: deepseek-chat, moonshot-v1-*, llama-3.*) but a
# future row should pick a vendor-prefixed name to stay safe.
for model, price in _DEEPSEEK.items():
    _PRICES[("openai-compat", model)] = price
for model, price in _MOONSHOT.items():
    _PRICES[("openai-compat", model)] = price
for model, price in _GROQ.items():
    _PRICES[("openai-compat", model)] = price
for model, price in _TOGETHER.items():
    _PRICES[("openai-compat", model)] = price


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
