"""L2 enrichment orchestrator.

One public function — :func:`enrich_with_l2` — runs all three L2
enrichers (wrapper detection, cross-file resolution, purpose inference)
concurrently against an existing :class:`ScanResult` and returns a new
ScanResult with the enriched data + ``l2_used=True``.

Cost-gate semantics:

- We compute a single :class:`L2CostEstimate` *before* running anything
  so the caller can show it to the user and ask for confirmation.
- The caller passes a confirmation hook (``confirm``); if it returns
  False, ``enrich_with_l2`` returns the original result with
  ``l2_used=False``.
- If ``confirm`` is None we treat that as auto-approve (used by
  ``--yes`` and by tests with the MockLLMClient).

Concurrency: each enricher's per-site calls are gathered with
``asyncio.gather`` so total wall-clock is roughly max(per-site latency)
rather than sum, even with a synchronous SDK underneath.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from aitap.deep.client import ChatMessage
from aitap.deep.cross_file_resolver import resolve_unresolved
from aitap.deep.purpose_inferer import infer_purpose
from aitap.deep.wrapper_detector import confirm_wrapper

if TYPE_CHECKING:
    from aitap.deep.client import LLMClient
    from aitap.scanner.models import PromptSite, ScanResult


@dataclasses.dataclass(frozen=True)
class L2CostEstimate:
    """Predicted cost of a full L2 pass — shown before any API call."""

    sites_to_check: int
    sites_to_resolve: int
    sites_to_infer: int
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_usd: float
    model: str

    @property
    def total_calls(self) -> int:
        return self.sites_to_check + self.sites_to_resolve + self.sites_to_infer


ConfirmHook = Callable[[L2CostEstimate], bool] | Callable[[L2CostEstimate], Awaitable[bool]]


def estimate_l2_cost(client: LLMClient, result: ScanResult) -> L2CostEstimate:
    """Predict the cost of running :func:`enrich_with_l2` on *result*.

    Uses :meth:`LLMClient.estimate_cost` per call site so the total
    matches whatever pricing table the client knows about.
    """
    from aitap.scanner.models import Confidence, TemplateKind

    sites_to_check = sum(1 for s in result.prompts if s.confidence != Confidence.HIGH)
    sites_to_resolve = sum(
        1
        for s in result.prompts
        if any(m.template_kind == TemplateKind.UNRESOLVED for m in s.messages)
    )
    sites_to_infer = sum(1 for s in result.prompts if not s.purpose)

    sample_messages = [ChatMessage(role="user", content="Site enrichment query.")]
    per_call = client.estimate_cost(sample_messages, max_tokens=200)
    total_calls = sites_to_check + sites_to_resolve + sites_to_infer

    return L2CostEstimate(
        sites_to_check=sites_to_check,
        sites_to_resolve=sites_to_resolve,
        sites_to_infer=sites_to_infer,
        estimated_input_tokens=per_call.input_tokens * total_calls,
        estimated_output_tokens=per_call.estimated_output_tokens * total_calls,
        estimated_usd=per_call.usd * total_calls,
        model=per_call.model,
    )


async def enrich_with_l2(
    client: LLMClient,
    result: ScanResult,
    *,
    snippet_for: Callable[[PromptSite], str | None] | None = None,
    confirm: ConfirmHook | None = None,
) -> ScanResult:
    """Run wrapper-detect, cross-file-resolve, purpose-infer in parallel.

    *snippet_for* (optional) returns a code snippet for the surrounding
    function/file to feed into the LLM context. Defaults to None
    (metadata-only prompts; cheaper but slightly less reliable).

    *confirm* is invoked once with the cost estimate. Sync or async.
    Return False to abort with the original result + ``l2_used=False``.
    """
    estimate = estimate_l2_cost(client, result)
    if confirm is not None:
        outcome = confirm(estimate)
        if asyncio.iscoroutine(outcome):
            outcome = await outcome
        if not outcome:
            return result.model_copy(update={"l2_used": False})

    def _no_snippet(_site: PromptSite) -> str | None:
        return None

    snippet_fn: Callable[[PromptSite], str | None] = snippet_for or _no_snippet

    confirm_tasks = [
        confirm_wrapper(client, site, snippet=snippet_fn(site)) for site in result.prompts
    ]
    confirmed = await asyncio.gather(*confirm_tasks)
    surviving = [s for s in confirmed if s is not None]

    resolve_tasks = [
        resolve_unresolved(client, site, snippet=snippet_fn(site)) for site in surviving
    ]
    resolved = await asyncio.gather(*resolve_tasks)

    infer_tasks = [infer_purpose(client, site) for site in resolved]
    enriched = await asyncio.gather(*infer_tasks)

    return result.model_copy(update={"prompts": list(enriched), "l2_used": True})
