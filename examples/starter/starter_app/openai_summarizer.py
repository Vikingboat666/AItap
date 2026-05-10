"""Step 1 of the example pipeline: summarize an email body with OpenAI."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openai import OpenAI


SYSTEM_PROMPT = (
    "You are a precise assistant that summarizes work emails. "
    "Return exactly one sentence, under 25 words, no greeting, no sign-off."
)


def summarize_email(client: OpenAI | Any, email_body: str) -> str:
    """Summarize ``email_body`` to one sentence using OpenAI chat completions.

    Accepts any client object exposing the ``chat.completions.create`` shape
    (the real ``openai.OpenAI`` client or a stand-in like
    :class:`starter_app.mocks.FakeOpenAI`).
    """
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Summarize this email:\n\n{email_body}"},
        ],
        temperature=0.2,
        max_tokens=120,
    )
    return response.choices[0].message.content
