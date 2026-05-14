"""A custom LLM wrapper that L1 won't recognise as a known SDK call.

Designed to test the L2 wrapper_detector: ``my_summarise`` does the
real work via a generic ``llm_invoke`` helper that doesn't match any
of the known SDK signatures, so L1 either misses it or marks it
LOW/MEDIUM. L2 should be able to confirm it's a wrapper.
"""

from __future__ import annotations


def llm_invoke(model: str, messages: list[dict[str, str]], temperature: float = 0.0) -> str:
    """Hypothetical LLM-call helper. Real impl would hit a model gateway."""
    return ""  # placeholder; the file is scan-only, never executed for real


def my_summarise(text: str) -> str:
    """Wraps llm_invoke to summarise an email."""
    return llm_invoke(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Summarise the email in one sentence."},
            {"role": "user", "content": text},
        ],
        temperature=0.0,
    )


def not_a_wrapper(value: int) -> int:
    """A control function — definitely not an LLM wrapper."""
    return value * 2
