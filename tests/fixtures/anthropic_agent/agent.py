"""Sample Anthropic-using project: a research-note agent wrapped behind a
function so the call site is one level removed from module scope."""

from __future__ import annotations

from anthropic import Anthropic

_SYSTEM_PROMPT = """You are a meticulous research assistant.
Always cite sources by URL when you can.
"""


def make_client() -> Anthropic:
    return Anthropic()


def research_note(question: str, sources: list[str]) -> str:
    client = make_client()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        temperature=0.3,
        system=_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    "Question: " + question + "\n\nAvailable sources:\n" + "\n".join(sources)
                ),
            },
        ],
    )
    text = response.content[0].text
    return text


def stream_chat(prompt: str) -> str:
    """Streaming variant — exercises the messages.stream signature."""
    client = make_client()
    chunks: list[str] = []
    with client.messages.stream(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for text_delta in stream.text_stream:
            chunks.append(text_delta)
    return "".join(chunks)
