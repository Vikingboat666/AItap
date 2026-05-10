"""Minimal stand-ins for the OpenAI / Anthropic clients.

These let the starter example run end-to-end without an API key, so the
example doubles as a smoke test for the wiring code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class _OpenAIMessage:
    content: str


@dataclass
class _OpenAIChoice:
    message: _OpenAIMessage


@dataclass
class _OpenAIResponse:
    choices: list[_OpenAIChoice]


class _FakeOpenAICompletions:
    def create(self, **_kwargs: Any) -> _OpenAIResponse:
        return _OpenAIResponse(
            choices=[
                _OpenAIChoice(
                    message=_OpenAIMessage(content="Mock summary: meeting moved to Friday at 3pm.")
                )
            ]
        )


class _FakeOpenAIChat:
    def __init__(self) -> None:
        self.completions = _FakeOpenAICompletions()


class FakeOpenAI:
    """Drop-in for ``openai.OpenAI`` that returns a canned summary."""

    def __init__(self) -> None:
        self.chat = _FakeOpenAIChat()


@dataclass
class _AnthropicTextBlock:
    text: str
    type: str = "text"


@dataclass
class _AnthropicResponse:
    content: list[_AnthropicTextBlock]


class _FakeAnthropicMessages:
    def create(self, **_kwargs: Any) -> _AnthropicResponse:
        return _AnthropicResponse(
            content=[
                _AnthropicTextBlock(
                    text='{"score": 4, "suggestion": "Drop the leading \\"Mock summary:\\" prefix."}'
                )
            ]
        )


class FakeAnthropic:
    """Drop-in for ``anthropic.Anthropic`` that returns a canned critique."""

    def __init__(self) -> None:
        self.messages = _FakeAnthropicMessages()
