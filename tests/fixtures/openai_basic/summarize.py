"""Sample OpenAI-basic project: a tiny email summariser."""

from __future__ import annotations

import os

from openai import OpenAI

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def summarize_email(body: str) -> str:
    """Return a one-sentence summary of an email body."""
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.2,
        max_tokens=120,
        messages=[
            {"role": "system", "content": "You are a concise email summariser."},
            {
                "role": "user",
                "content": f"Summarise the following email in one sentence:\n\n{body}",
            },
        ],
    )
    return response.choices[0].message.content or ""


def classify_intent(text: str) -> str:
    """Classify an email's intent into a short label."""
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0,
        max_tokens=10,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": "Classify the intent. Reply with a JSON object."},
            {"role": "user", "content": text},
        ],
    )
    return response.choices[0].message.content or ""
