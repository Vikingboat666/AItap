"""Variable-flow fixture: two LLM calls connected via an explicit variable.

The dataflow detector should produce one Pipeline with two nodes and one
EdgeKind.VARIABLE edge.
"""

from __future__ import annotations

from openai import OpenAI

client = OpenAI()


def workflow(raw_topic: str) -> str:
    outline = (
        client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Produce a 5-bullet outline."},
                {"role": "user", "content": raw_topic},
            ],
            temperature=0.4,
        )
        .choices[0]
        .message.content
        or ""
    )

    polished = (
        client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Expand the outline into a draft."},
                {"role": "user", "content": outline},
            ],
            temperature=0.7,
            max_tokens=600,
        )
        .choices[0]
        .message.content
        or ""
    )

    return polished
