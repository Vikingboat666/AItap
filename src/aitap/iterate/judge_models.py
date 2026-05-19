"""Shared pydantic models for the LLM-as-judge.

Split out of :mod:`aitap.iterate.judge` so :mod:`aitap.iterate.judge_defaults`
can declare :data:`~aitap.iterate.judge_defaults.DEFAULT_DIMENSIONS` without
creating a circular import (the judge module imports the defaults at
load_dimensions time).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Dimension(BaseModel):
    """One scoring axis the judge uses to grade an output.

    ``weight`` is a per-dimension coefficient in [0, 1]; the judge's
    aggregator multiplies the LLM-returned per-dim score by the weight and
    sums across dimensions. Weights across the active rubric are expected
    to sum to ~1.0 but the aggregator does not enforce this — overriders
    are free to use any non-negative scaling.

    ``rubric`` is free-form text passed verbatim to the judge LLM. The
    rubric is what makes scores comparable across runs; under-specified
    rubrics let the judge drift between calls.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., min_length=1)
    weight: float = Field(..., ge=0.0, le=1.0)
    rubric: str = Field(..., min_length=1)


class JudgeScore(BaseModel):
    """One case's worth of judge output.

    ``weighted_total`` is the canonical score persisted onto the
    ``iterations`` row (owned by wt/iterations-store) for convergence
    checks. ``per_dim`` is the breakdown the critic targets and the UI
    renders as a radar chart. ``critique`` is the judge's free-text
    feedback — short, concrete, and the only signal the rewriter sees.
    """

    model_config = ConfigDict(frozen=True)

    weighted_total: float = Field(..., ge=0.0, le=1.0)
    per_dim: dict[str, float] = Field(default_factory=dict)
    critique: str = ""


__all__ = ["Dimension", "JudgeScore"]
