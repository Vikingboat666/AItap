"""Second OpenAI call site — exercises the legacy completions endpoint and the
plain-string ``prompt=`` shape."""

from __future__ import annotations

from openai import OpenAI

client = OpenAI()


def write_followup(name: str, topic: str) -> str:
    """Draft a follow-up message using the legacy completion API."""
    return (
        client.completions.create(
            model="gpt-3.5-turbo-instruct",
            max_tokens=200,
            prompt=f"Draft a friendly follow-up to {name} about {topic}.",
        )
        .choices[0]
        .text
        or ""
    )
