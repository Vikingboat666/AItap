"""Tiny two-step LLM pipeline used to dogfood ``aitap scan``."""

from starter_app.anthropic_critic import critique_summary
from starter_app.openai_summarizer import summarize_email

__all__ = ["critique_summary", "summarize_email"]
