"""L2 enricher: try to resolve UNRESOLVED prompt templates.

L1 marks ``TemplateKind.UNRESOLVED`` whenever the prompt is built up
in a way the rule-based extractor can't pin down (cross-file constants,
dynamic dict construction, etc). This enricher feeds the surrounding
source to the LLM and asks for the reconstructed template body.

Conservative: if the LLM isn't confident, we leave the message alone.
The product gets a "still unresolved" indicator in the UI, never a
fabricated fake template.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aitap.deep.client import ChatMessage
from aitap.scanner.models import Message, TemplateKind

if TYPE_CHECKING:
    from aitap.deep.client import LLMClient
    from aitap.scanner.models import PromptSite


_system_prompt_cache: str | None = None


def _system_prompt() -> str:
    global _system_prompt_cache
    if _system_prompt_cache is None:
        try:
            ref = resources.files("aitap.deep.prompts").joinpath("cross_file_resolve.md")
            _system_prompt_cache = ref.read_text(encoding="utf-8")
        except (FileNotFoundError, ModuleNotFoundError):
            here = Path(__file__).resolve().parent / "prompts" / "cross_file_resolve.md"
            _system_prompt_cache = here.read_text(encoding="utf-8")
    return _system_prompt_cache


_VALID_KINDS = {k.value for k in TemplateKind}


async def resolve_unresolved(
    client: LLMClient,
    site: PromptSite,
    *,
    snippet: str | None = None,
) -> PromptSite:
    """Replace UNRESOLVED messages in *site* with LLM-reconstructed ones."""
    if not any(m.template_kind == TemplateKind.UNRESOLVED for m in site.messages):
        return site

    new_messages: list[Message] = []
    for msg in site.messages:
        if msg.template_kind != TemplateKind.UNRESOLVED:
            new_messages.append(msg)
            continue
        resolved = await _resolve_one(client, site, msg, snippet)
        new_messages.append(resolved)
    return site.model_copy(update={"messages": new_messages})


async def _resolve_one(
    client: LLMClient,
    site: PromptSite,
    msg: Message,
    snippet: str | None,
) -> Message:
    user_block = _format_question(site, msg, snippet)
    response = await client.chat(
        [
            ChatMessage(role="system", content=_system_prompt()),
            ChatMessage(role="user", content=user_block),
        ],
        temperature=0.0,
        max_tokens=600,
        response_format="json",
    )
    parsed = _parse_resolution(response.text)
    if parsed is None or not parsed.get("resolved"):
        return msg

    new_text = parsed.get("template_text") or ""
    if not isinstance(new_text, str) or not new_text.strip():
        return msg

    new_kind_value = parsed.get("kind", "literal")
    if new_kind_value not in _VALID_KINDS:
        new_kind_value = "literal"
    new_kind = TemplateKind(new_kind_value)

    return msg.model_copy(update={"template_text": new_text, "template_kind": new_kind})


def _format_question(site: PromptSite, msg: Message, snippet: str | None) -> str:
    parts = [
        f"Function: {site.name} ({site.location.file}:{site.location.line_start})",
        f"Provider: {site.provider.value}",
        f"Role: {msg.role.value}",
        f"L1 kind: {msg.template_kind.value}",
        f"L1 partial template: {msg.template_text!r}",
    ]
    if snippet:
        parts.append("\nSurrounding source:\n```python\n")
        parts.append(snippet[:4000])
        parts.append("\n```")
    return "\n".join(parts)


def _parse_resolution(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        raw: Any = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict) or "resolved" not in raw:
        return None
    data: dict[str, Any] = raw
    return data
