"""L2 enricher: confirm or reject MEDIUM/LOW-confidence PromptSites.

L1 emits ``Confidence.MEDIUM`` when it sees an SDK-shaped call but
can't confirm the messages list is well-formed (e.g., dynamic dict
construction), and ``Confidence.LOW`` when the signal is weak enough
that the L1 rule itself wasn't sure (custom wrappers).

This enricher takes those uncertain sites, hands the surrounding
function source to the LLM, and either:

- **Promotes** the site to ``Confidence.HIGH`` (LLM says yes, it's a
  real wrapper); or
- **Marks for removal** by returning ``None`` (LLM says it's not an
  LLM wrapper at all). The orchestrator filters these out before
  returning the new ScanResult.

We never *invent* a new site here — the surface is "confirm what L1
already proposed."
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aitap.deep.client import ChatMessage
from aitap.scanner.models import Confidence

if TYPE_CHECKING:
    from aitap.deep.client import LLMClient
    from aitap.scanner.models import PromptSite


_system_prompt_cache: str | None = None


def _system_prompt() -> str:
    global _system_prompt_cache
    if _system_prompt_cache is None:
        try:
            ref = resources.files("aitap.deep.prompts").joinpath("wrapper_detect.md")
            _system_prompt_cache = ref.read_text(encoding="utf-8")
        except (FileNotFoundError, ModuleNotFoundError):
            here = Path(__file__).resolve().parent / "prompts" / "wrapper_detect.md"
            _system_prompt_cache = here.read_text(encoding="utf-8")
    return _system_prompt_cache


async def confirm_wrapper(
    client: LLMClient,
    site: PromptSite,
    *,
    snippet: str | None = None,
) -> PromptSite | None:
    """Promote *site* if it's a real LLM wrapper, drop it if not.

    HIGH-confidence sites pass through unchanged — we never re-litigate
    something the rules already nailed.

    *snippet* is the surrounding source code (typically the enclosing
    function body); when omitted we make the call with site metadata
    only, which is less reliable but still useful.
    """
    if site.confidence == Confidence.HIGH:
        return site

    user_block = _format_question(site, snippet)
    response = await client.chat(
        [
            ChatMessage(role="system", content=_system_prompt()),
            ChatMessage(role="user", content=user_block),
        ],
        temperature=0.0,
        max_tokens=120,
        response_format="json",
    )

    verdict = _parse_verdict(response.text)
    if verdict is None:
        # LLM declined to answer — leave the site as-is so the user
        # can still see it (downgraded but present).
        return site

    if not verdict["is_llm_wrapper"]:
        return None  # Filter out — not actually an LLM call.

    new_confidence = (
        Confidence.HIGH if verdict["confidence"] in ("high", "medium") else Confidence.MEDIUM
    )
    return site.model_copy(update={"confidence": new_confidence})


def _format_question(site: PromptSite, snippet: str | None) -> str:
    parts = [
        f"Function name: {site.name}",
        f"Apparent provider: {site.provider.value}",
        f"L1 confidence: {site.confidence.value}",
        f"Messages found: {len(site.messages)}",
    ]
    for i, m in enumerate(site.messages):
        parts.append(f"  [{i}] role={m.role.value} kind={m.template_kind.value}")
        # Truncate to keep the prompt bounded.
        body = m.template_text[:300] + ("..." if len(m.template_text) > 300 else "")
        parts.append(f"      template: {body!r}")
    if snippet:
        # Truncate to a few hundred lines worst case.
        parts.append("\nSurrounding source:\n```python\n")
        parts.append(snippet[:4000])
        parts.append("\n```")
    return "\n".join(parts)


def _parse_verdict(text: str) -> dict[str, Any] | None:
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
    if not isinstance(raw, dict):
        return None
    data: dict[str, Any] = raw
    if "is_llm_wrapper" not in data or not isinstance(data["is_llm_wrapper"], bool):
        return None
    confidence: Any = data.get("confidence", "medium")
    if confidence not in ("high", "medium", "low"):
        confidence = "medium"
    return {"is_llm_wrapper": data["is_llm_wrapper"], "confidence": confidence}
