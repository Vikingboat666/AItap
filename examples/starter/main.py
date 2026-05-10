"""Run the starter example end-to-end with mocked clients.

Usage::

    python examples/starter/main.py

This script lives outside the ``starter_app`` package so users can also
``import starter_app`` directly from a REPL or notebook.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make ``starter_app`` importable when running this file directly.
sys.path.insert(0, str(Path(__file__).parent))

from starter_app.anthropic_critic import critique_summary
from starter_app.mocks import FakeAnthropic, FakeOpenAI
from starter_app.openai_summarizer import summarize_email

SAMPLE_EMAIL = """\
Hi team,

Quick heads up: the planning meeting we had on the calendar for Thursday
is moving to Friday at 3pm in the upstairs room. Same agenda, please come
with the Q3 numbers.

Thanks,
Riley
"""


def run_pipeline(email_body: str) -> dict[str, str]:
    """Two-step pipeline: OpenAI summarize → Anthropic critique."""
    openai_client = FakeOpenAI()
    anthropic_client = FakeAnthropic()

    summary = summarize_email(openai_client, email_body)
    critique = critique_summary(anthropic_client, summary)

    return {"summary": summary, "critique": critique}


def main() -> None:
    result = run_pipeline(SAMPLE_EMAIL)
    print("=== summary ===")
    print(result["summary"])
    print()
    print("=== critique ===")
    print(result["critique"])


if __name__ == "__main__":
    main()
