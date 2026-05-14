"""Tests for the L2 enrichment orchestrator + cost gate."""

from __future__ import annotations

from aitap.deep.orchestrator import L2CostEstimate, enrich_with_l2, estimate_l2_cost
from aitap.deep.testing import MockLLMClient
from aitap.scanner.models import (
    CallParameters,
    CodeLocation,
    Confidence,
    Message,
    PromptSite,
    Provider,
    Role,
    ScanResult,
    TemplateKind,
)


def _site(
    *,
    site_id: str = "abc",
    name: str = "summarise",
    confidence: Confidence = Confidence.HIGH,
    purpose: str | None = None,
    has_unresolved: bool = False,
) -> PromptSite:
    msg_kind = TemplateKind.UNRESOLVED if has_unresolved else TemplateKind.LITERAL
    return PromptSite(
        id=site_id,
        name=name,
        provider=Provider.OPENAI,
        location=CodeLocation(file="x.py", line_start=10, line_end=12),
        messages=[Message(role=Role.USER, template_text="hi", template_kind=msg_kind)],
        parameters=CallParameters(model="gpt-4o-mini"),
        purpose=purpose,
        confidence=confidence,
    )


def _result(*sites: PromptSite) -> ScanResult:
    return ScanResult(
        project_root="/tmp/proj",
        files_scanned=1,
        prompts=list(sites),
        pipelines=[],
        providers_detected=[],
    )


# --------------------------------------------------------------------------- #
# Cost estimation                                                             #
# --------------------------------------------------------------------------- #


def test_estimate_l2_cost_counts_each_workload_separately() -> None:
    result = _result(
        _site(site_id="1", confidence=Confidence.MEDIUM),  # needs check
        _site(site_id="2", has_unresolved=True),  # needs resolve
        _site(site_id="3", purpose=None),  # needs infer (also other two)
        _site(site_id="4", confidence=Confidence.HIGH, purpose="known"),  # nothing
    )
    estimate = estimate_l2_cost(MockLLMClient(scripted=[]), result)
    # 1 medium → check; 1 unresolved → resolve; 3 sites without purpose → infer
    assert estimate.sites_to_check == 1
    assert estimate.sites_to_resolve == 1
    assert estimate.sites_to_infer == 3
    assert estimate.total_calls == 5
    assert estimate.estimated_usd > 0


def test_estimate_l2_cost_zero_when_everything_already_high() -> None:
    result = _result(_site(confidence=Confidence.HIGH, purpose="known"))
    estimate = estimate_l2_cost(MockLLMClient(scripted=[]), result)
    assert estimate.total_calls == 0


# --------------------------------------------------------------------------- #
# enrich_with_l2 — confirm gate                                               #
# --------------------------------------------------------------------------- #


async def test_enrich_aborts_when_confirm_returns_false() -> None:
    client = MockLLMClient(scripted=["should not be called"])
    result = _result(_site(confidence=Confidence.MEDIUM))
    out = await enrich_with_l2(client, result, confirm=lambda _est: False)
    assert out.l2_used is False
    assert client.calls == []  # gate worked


async def test_enrich_proceeds_when_confirm_returns_true() -> None:
    # Need scripted responses for: 1 wrapper-confirm + 1 purpose (no resolve)
    client = MockLLMClient(
        scripted=[
            '{"is_llm_wrapper": true, "confidence": "high", "reason": "yep"}',
            '{"purpose": "summarise emails"}',
        ]
    )
    result = _result(_site(confidence=Confidence.MEDIUM))
    out = await enrich_with_l2(client, result, confirm=lambda _est: True)
    assert out.l2_used is True
    assert out.prompts[0].confidence == Confidence.HIGH
    assert out.prompts[0].purpose == "summarise emails"


async def test_enrich_supports_async_confirm() -> None:
    async def _confirm(_est: L2CostEstimate) -> bool:
        return True

    client = MockLLMClient(
        scripted=[
            '{"purpose": "do a thing"}',
        ]
    )
    result = _result(_site(confidence=Confidence.HIGH))  # only purpose enrichment needed
    out = await enrich_with_l2(client, result, confirm=_confirm)
    assert out.l2_used is True


# --------------------------------------------------------------------------- #
# enrich_with_l2 — drops sites the LLM rejects                                #
# --------------------------------------------------------------------------- #


async def test_enrich_drops_sites_rejected_by_wrapper_detector() -> None:
    client = MockLLMClient(
        scripted=[
            '{"is_llm_wrapper": false, "confidence": "high", "reason": "actually a math helper"}',
        ]
    )
    result = _result(_site(site_id="rejected", confidence=Confidence.LOW))
    out = await enrich_with_l2(client, result, confirm=None)
    assert out.l2_used is True
    assert out.prompts == []


# --------------------------------------------------------------------------- #
# enrich_with_l2 — high-confidence sites pass through unchanged               #
# --------------------------------------------------------------------------- #


async def test_enrich_does_not_re_litigate_high_confidence_sites() -> None:
    """HIGH-confidence sites skip wrapper detection (no LLM call)."""
    client = MockLLMClient(
        scripted=[
            # Only the purpose call should fire — no wrapper-confirm call.
            '{"purpose": "summarises emails"}',
        ]
    )
    result = _result(_site(confidence=Confidence.HIGH))
    out = await enrich_with_l2(client, result, confirm=None)
    assert out.l2_used is True
    assert out.prompts[0].purpose == "summarises emails"
    # Exactly one call (purpose only).
    assert len(client.calls) == 1
