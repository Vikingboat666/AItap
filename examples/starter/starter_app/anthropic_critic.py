"""Step 2 of the example pipeline: critique the summary with Anthropic."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from anthropic import Anthropic


CRITIC_SYSTEM = (
    "You are a strict copy editor. Given a one-sentence email summary, "
    "score it 1-5 on faithfulness and brevity, then suggest one concrete "
    'improvement. Respond as JSON: {"score": int, "suggestion": str}.'
)


def critique_summary(client: Anthropic | Any, summary: str) -> str:
    """Critique a summary using Anthropic messages API.

    Accepts any client exposing the ``messages.create`` shape (the real
    ``anthropic.Anthropic`` client or a stand-in like
    :class:`starter_app.mocks.FakeAnthropic`).
    """
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        system=CRITIC_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": f"Critique this summary:\n\n{summary}",
            },
        ],
        temperature=0.0,
    )
    return response.content[0].text
