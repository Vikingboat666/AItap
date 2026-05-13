"""L2 enricher: infer the *purpose* of every PromptSite using an LLM.

The result populates ``PromptSite.purpose`` and downstream test-case
generation (``dataset/llm_expander``) reads it to produce semantically
appropriate inputs. Without this, every prompt looks like an opaque
LLM call.

The inferer never invents purposes — it asks the LLM to read the
prompt template + a small surrounding code snippet and return a one-
line description. If the LLM declines (returns no/empty purpose),
we leave ``PromptSite.purpose`` unchanged.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aitap.deep.client import ChatMessage

if TYPE_CHECKING:
    from aitap.deep.client import LLMClient
    from aitap.scanner.models import PromptSite


_system_prompt_cache: str | None = None


def _system_prompt() -> str:
    """Read the L2 system prompt from the bundled ``prompts/`` directory.

    Cached after first read; the .md files don't change at runtime.
    """
    global _system_prompt_cache
    if _system_prompt_cache is None:
        try:
            ref = resources.files("aitap.deep.prompts").joinpath("purpose_infer.md")
            _system_prompt_cache = ref.read_text(encoding="utf-8")
        except (FileNotFoundError, ModuleNotFoundError):
            # Worktree-time fallback: read directly off the source tree.
            here = Path(__file__).resolve().parent / "prompts" / "purpose_infer.md"
            _system_prompt_cache = here.read_text(encoding="utf-8")
    return _system_prompt_cache


async def infer_purpose(client: LLMClient, site: PromptSite) -> PromptSite:
    """Return a copy of *site* with ``purpose`` populated (or unchanged)."""
    if site.purpose:
        return site

    user_block = _format_site_for_prompt(site)
    response = await client.chat(
        [
            ChatMessage(role="system", content=_system_prompt()),
            ChatMessage(role="user", content=user_block),
        ],
        temperature=0.0,
        max_tokens=200,
        response_format="json",
    )

    purpose = _extract_purpose(response.text)
    if not purpose:
        return site
    return site.model_copy(update={"purpose": purpose})


def _format_site_for_prompt(site: PromptSite) -> str:
    msg_dump = "\n".join(
        f"  [{m.role.value}] ({m.template_kind.value}): {m.template_text!r}" for m in site.messages
    )
    params: list[str] = []
    if site.parameters.model:
        params.append(f"model={site.parameters.model}")
    if site.parameters.temperature is not None:
        params.append(f"temperature={site.parameters.temperature}")
    if site.parameters.max_tokens is not None:
        params.append(f"max_tokens={site.parameters.max_tokens}")
    params_str = ", ".join(params) or "(none)"

    return (
        f"Function name: {site.name}\n"
        f"Provider: {site.provider.value}\n"
        f"Location: {site.location.file}:{site.location.line_start}\n"
        f"Parameters: {params_str}\n"
        f"Messages:\n{msg_dump}\n"
    )


def _extract_purpose(text: str) -> str | None:
    """Pull `purpose` from the JSON response. Tolerates wrapping prose."""
    text = text.strip()
    # Tolerate code-fenced JSON blocks (LLMs love those even when told no).
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
    data: dict[str, Any] = raw  # narrow for downstream attribute access
    purpose: Any = data.get("purpose")
    if not isinstance(purpose, str) or not purpose.strip():
        return None
    return purpose.strip()
