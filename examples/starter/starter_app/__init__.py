"""Tiny two-step LLM pipeline used to dogfood ``aitap scan``."""

from .anthropic_critic import critique_summary
from .openai_summarizer import summarize_email

__all__ = ["critique_summary", "summarize_email"]
