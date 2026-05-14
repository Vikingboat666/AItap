"""Unit tests for individual L2 enrichers (wrapper / cross-file / purpose)."""

from __future__ import annotations

from aitap.deep.cross_file_resolver import resolve_unresolved
from aitap.deep.purpose_inferer import infer_purpose
from aitap.deep.testing import MockLLMClient
from aitap.deep.wrapper_detector import confirm_wrapper
from aitap.scanner.models import (
    CallParameters,
    CodeLocation,
    Confidence,
    Message,
    PromptSite,
    Provider,
    Role,
    TemplateKind,
)


def _site(
    *,
    confidence: Confidence = Confidence.MEDIUM,
    purpose: str | None = None,
    msg_kind: TemplateKind = TemplateKind.LITERAL,
    msg_text: str = "hi",
) -> PromptSite:
    return PromptSite(
        id="abc",
        name="my_wrapper",
        provider=Provider.OPENAI,
        location=CodeLocation(file="x.py", line_start=10, line_end=12),
        messages=[Message(role=Role.USER, template_text=msg_text, template_kind=msg_kind)],
        parameters=CallParameters(model="gpt-4o-mini"),
        purpose=purpose,
        confidence=confidence,
    )


# --------------------------------------------------------------------------- #
# wrapper_detector                                                            #
# --------------------------------------------------------------------------- #


async def test_wrapper_detector_passes_high_confidence_through_unchanged() -> None:
    client = MockLLMClient(scripted=["unused"])
    site = _site(confidence=Confidence.HIGH)
    out = await confirm_wrapper(client, site)
    assert out is site
    assert client.calls == []


async def test_wrapper_detector_promotes_when_llm_confirms() -> None:
    client = MockLLMClient(
        scripted=['{"is_llm_wrapper": true, "confidence": "high", "reason": "ok"}']
    )
    site = _site(confidence=Confidence.MEDIUM)
    out = await confirm_wrapper(client, site, snippet="def my_wrapper(): ...")
    assert out is not None
    assert out.confidence == Confidence.HIGH


async def test_wrapper_detector_drops_when_llm_rejects() -> None:
    client = MockLLMClient(
        scripted=['{"is_llm_wrapper": false, "confidence": "high", "reason": "no"}']
    )
    site = _site(confidence=Confidence.LOW)
    out = await confirm_wrapper(client, site)
    assert out is None


async def test_wrapper_detector_keeps_site_when_llm_returns_garbage() -> None:
    client = MockLLMClient(scripted=["not json at all"])
    site = _site(confidence=Confidence.MEDIUM)
    out = await confirm_wrapper(client, site)
    # Conservative: keep the site so the user can still see it.
    assert out is site


async def test_wrapper_detector_handles_code_fenced_json() -> None:
    """Some models wrap JSON in ```json blocks even when told not to."""
    client = MockLLMClient(
        scripted=['```json\n{"is_llm_wrapper": true, "confidence": "high", "reason": "ok"}\n```']
    )
    site = _site(confidence=Confidence.MEDIUM)
    out = await confirm_wrapper(client, site)
    assert out is not None
    assert out.confidence == Confidence.HIGH


# --------------------------------------------------------------------------- #
# cross_file_resolver                                                         #
# --------------------------------------------------------------------------- #


async def test_cross_file_resolver_skips_when_no_unresolved_messages() -> None:
    client = MockLLMClient(scripted=["unused"])
    site = _site(msg_kind=TemplateKind.LITERAL, msg_text="known")
    out = await resolve_unresolved(client, site)
    assert out is site
    assert client.calls == []


async def test_cross_file_resolver_replaces_unresolved_with_llm_output() -> None:
    client = MockLLMClient(
        scripted=[
            '{"resolved": true, "template_text": "Summarise: {body}", "kind": "fstring", "reason": ""}',
        ]
    )
    site = _site(msg_kind=TemplateKind.UNRESOLVED, msg_text="")
    out = await resolve_unresolved(client, site, snippet="full code here")
    assert out.messages[0].template_text == "Summarise: {body}"
    assert out.messages[0].template_kind == TemplateKind.FSTRING


async def test_cross_file_resolver_keeps_unresolved_when_llm_declines() -> None:
    client = MockLLMClient(
        scripted=[
            '{"resolved": false, "template_text": "", "kind": "unresolved", "reason": "ambiguous"}',
        ]
    )
    site = _site(msg_kind=TemplateKind.UNRESOLVED)
    out = await resolve_unresolved(client, site)
    assert out.messages[0].template_kind == TemplateKind.UNRESOLVED


async def test_cross_file_resolver_keeps_unresolved_on_invalid_json() -> None:
    client = MockLLMClient(scripted=["nope"])
    site = _site(msg_kind=TemplateKind.UNRESOLVED)
    out = await resolve_unresolved(client, site)
    assert out.messages[0].template_kind == TemplateKind.UNRESOLVED


async def test_cross_file_resolver_clamps_unknown_kind_to_literal() -> None:
    """If the LLM invents a kind we don't recognise, fall back to literal."""
    client = MockLLMClient(
        scripted=[
            '{"resolved": true, "template_text": "Hello", "kind": "yaml_template", "reason": ""}',
        ]
    )
    site = _site(msg_kind=TemplateKind.UNRESOLVED)
    out = await resolve_unresolved(client, site)
    assert out.messages[0].template_kind == TemplateKind.LITERAL


# --------------------------------------------------------------------------- #
# purpose_inferer                                                             #
# --------------------------------------------------------------------------- #


async def test_purpose_inferer_skips_sites_with_existing_purpose() -> None:
    client = MockLLMClient(scripted=["unused"])
    site = _site(purpose="already known")
    out = await infer_purpose(client, site)
    assert out is site
    assert client.calls == []


async def test_purpose_inferer_populates_from_llm() -> None:
    client = MockLLMClient(scripted=['{"purpose": "summarises emails"}'])
    site = _site()
    out = await infer_purpose(client, site)
    assert out.purpose == "summarises emails"


async def test_purpose_inferer_leaves_purpose_unchanged_on_garbage() -> None:
    client = MockLLMClient(scripted=["totally not json"])
    site = _site()
    out = await infer_purpose(client, site)
    assert out.purpose is None


async def test_purpose_inferer_strips_code_fences() -> None:
    client = MockLLMClient(scripted=['```json\n{"purpose": "classify intent"}\n```'])
    site = _site()
    out = await infer_purpose(client, site)
    assert out.purpose == "classify intent"


async def test_purpose_inferer_rejects_empty_purpose_string() -> None:
    client = MockLLMClient(scripted=['{"purpose": "   "}'])
    site = _site()
    out = await infer_purpose(client, site)
    assert out.purpose is None
