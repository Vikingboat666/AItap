"""Unit tests for the orchestrator :func:`aitap.dataset.generate_cases`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aitap.dataset import generate_cases, save_seeds
from aitap.deep.testing import MockLLMClient
from aitap.scanner.models import (
    CodeLocation,
    Confidence,
    Message,
    PromptSite,
    Provider,
    Role,
)

TESTS_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_ROOT = TESTS_ROOT / "fixtures"


def _site(
    *,
    file: str = "openai_basic/summarize.py",
    line: int = 14,
    name: str = "summarize_email",
    purpose: str | None = "Summarises customer support emails.",
) -> PromptSite:
    return PromptSite(
        id="site-1",
        name=name,
        provider=Provider.OPENAI,
        location=CodeLocation(file=file, line_start=line, line_end=line),
        messages=[Message(role=Role.USER, template_text="Summarise {body}")],
        purpose=purpose,
        confidence=Confidence.HIGH,
    )


def _expanded_json(n: int) -> str:
    return json.dumps(
        [{"inputs": {"body": f"v{i}"}, "tags": ["boundary"], "notes": f"v{i}"} for i in range(n)]
    )


async def test_seed_mode_reads_from_disk(tmp_path: Path) -> None:
    datasets = tmp_path / "datasets"
    datasets.mkdir()
    site = _site()
    save_seeds(
        datasets,
        site.name,
        [{"inputs": {"body": "first"}}, {"inputs": {"body": "second"}}],
        prompt_site_id=site.id,
    )
    out = await generate_cases(site, mode="seed", n=10, datasets_dir=datasets)
    assert {c.inputs["body"] for c in out} == {"first", "second"}


async def test_seed_mode_caps_to_n(tmp_path: Path) -> None:
    datasets = tmp_path / "datasets"
    datasets.mkdir()
    site = _site()
    save_seeds(
        datasets,
        site.name,
        [{"inputs": {"body": f"v{i}"}} for i in range(5)],
        prompt_site_id=site.id,
    )
    out = await generate_cases(site, mode="seed", n=2, datasets_dir=datasets)
    assert len(out) == 2


async def test_seed_mode_requires_datasets_dir() -> None:
    with pytest.raises(ValueError, match="datasets_dir"):
        await generate_cases(_site(), mode="seed")


async def test_fixtures_mode_finds_candidates() -> None:
    """Pointed at the tests/ root so the default ``fixtures/`` search dir
    resolves to ``tests/fixtures/`` and the openai_basic sample is in scope."""
    site = _site()
    out = await generate_cases(site, mode="fixtures", n=20, project_root=TESTS_ROOT)
    assert len(out) >= 1
    assert all(c.source == "fixture" for c in out)


async def test_fixtures_mode_requires_project_root() -> None:
    with pytest.raises(ValueError, match="project_root"):
        await generate_cases(_site(), mode="fixtures")


async def test_expand_mode_uses_explicit_seeds() -> None:
    client = MockLLMClient(scripted=[_expanded_json(3)])
    out = await generate_cases(
        _site(),
        mode="expand",
        n=3,
        client=client,
        seeds=[{"inputs": {"body": "Hello"}}],
    )
    assert len(out) == 3
    assert all(c.source == "expand" for c in out)
    assert all(c.prompt_site_id == "site-1" for c in out)
    # The user prompt should carry the LLM-readable purpose for grounding.
    user_msg = client.calls[0].messages[1].content
    assert "Summarises customer support emails." in user_msg


async def test_expand_mode_falls_back_to_disk_seeds(tmp_path: Path) -> None:
    datasets = tmp_path / "datasets"
    datasets.mkdir()
    site = _site()
    save_seeds(
        datasets,
        site.name,
        [{"inputs": {"body": "Hello from disk"}}],
        prompt_site_id=site.id,
    )
    client = MockLLMClient(scripted=[_expanded_json(2)])
    out = await generate_cases(site, mode="expand", n=2, client=client, datasets_dir=datasets)
    assert len(out) == 2
    # The on-disk seed must reach the LLM as grounding.
    user_msg = client.calls[0].messages[1].content
    assert "Hello from disk" in user_msg


async def test_expand_mode_errors_without_any_seed_source(tmp_path: Path) -> None:
    client = MockLLMClient(scripted=[_expanded_json(2)])
    with pytest.raises(ValueError, match="at least one seed"):
        await generate_cases(
            _site(),
            mode="expand",
            n=2,
            client=client,
            datasets_dir=tmp_path,
        )


async def test_expand_mode_requires_client() -> None:
    with pytest.raises(ValueError, match="LLMClient"):
        await generate_cases(_site(), mode="expand", n=2, seeds=[{"inputs": {"body": "x"}}])


async def test_context_mode_includes_input_shape() -> None:
    client = MockLLMClient(scripted=[_expanded_json(2)])
    out = await generate_cases(
        _site(),
        mode="context",
        n=2,
        client=client,
        project_root=FIXTURES_ROOT,
        seeds=[{"inputs": {"body": "Hello"}}],
    )
    assert len(out) == 2
    assert all(c.source == "context" for c in out)
    user_msg = client.calls[0].messages[1].content
    # The function signature ``body: str`` should have been inferred and
    # surfaced as an input_shape grounding line.
    assert "summarize_email" in user_msg
    assert "input shape" in user_msg.lower()


async def test_context_mode_requires_project_root() -> None:
    client = MockLLMClient(scripted=[_expanded_json(2)])
    with pytest.raises(ValueError, match="project_root"):
        await generate_cases(
            _site(),
            mode="context",
            n=2,
            client=client,
            seeds=[{"inputs": {"body": "x"}}],
        )
