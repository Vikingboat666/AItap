"""Unit tests for :mod:`aitap.dataset.fixture_miner`."""

from __future__ import annotations

from pathlib import Path

from aitap.dataset.fixture_miner import find_candidate_inputs
from aitap.scanner.models import (
    CodeLocation,
    Confidence,
    Message,
    PromptSite,
    Provider,
    Role,
    TemplateKind,
    TemplateVariable,
)

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures"


def _site(name: str = "summarize_email") -> PromptSite:
    return PromptSite(
        id="site-1",
        name=name,
        provider=Provider.OPENAI,
        location=CodeLocation(file="x.py", line_start=1, line_end=1),
        messages=[
            Message(
                role=Role.USER,
                template_text="Summarise: {body}",
                template_kind=TemplateKind.FSTRING,
                variables=[TemplateVariable(name="body")],
            )
        ],
        confidence=Confidence.HIGH,
    )


def test_finds_candidates_in_openai_basic_fixture() -> None:
    """The openai_basic/summarize.py file contains ``{"role": "user",
    "content": ...}`` dict literals; the miner should surface at least
    one even without any project-wide ``tests/`` directory."""
    candidates = find_candidate_inputs(FIXTURES_ROOT, _site(), search_dirs=["openai_basic"])
    assert len(candidates) >= 1
    # All candidates carry the fixture provenance.
    assert all(c.source == "fixture" for c in candidates)
    assert all("fixture" in c.tags for c in candidates)
    assert all(c.prompt_site_id == "site-1" for c in candidates)
    # The classic role/content dict must be among them.
    role_content_hits = [c for c in candidates if c.inputs.get("role") in {"user", "system"}]
    assert role_content_hits


def test_picks_up_template_variable_keys(tmp_path: Path) -> None:
    """A dict whose only "interesting" key is the prompt's own template
    variable name (here ``body``) must still be found."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "fix.py").write_text("FIXTURE = {'body': 'sample email body'}\n", encoding="utf-8")
    candidates = find_candidate_inputs(tmp_path, _site())
    assert any(c.inputs.get("body") == "sample email body" for c in candidates)


def test_extracts_from_json_files(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "inputs.json").write_text(
        '[{"body": "first"}, {"body": "second"}, {"unrelated": 1}]',
        encoding="utf-8",
    )
    candidates = find_candidate_inputs(tmp_path, _site())
    bodies = {c.inputs.get("body") for c in candidates}
    assert bodies == {"first", "second"}


def test_rejects_dicts_with_non_serialisable_values(tmp_path: Path) -> None:
    """A dict whose value is a lambda / function call won't pass
    ``ast.literal_eval`` — and even if it did, JSON serialisation would
    fail. The miner must skip silently."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "fix.py").write_text("FIXTURE = {'body': some_call()}\n", encoding="utf-8")
    candidates = find_candidate_inputs(tmp_path, _site())
    assert candidates == []


def test_respects_max_candidates_cap(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    big = "\n".join(f"FIX{i} = {{'body': '{i}'}}" for i in range(20))
    (tests_dir / "many.py").write_text(big + "\n", encoding="utf-8")

    capped = find_candidate_inputs(tmp_path, _site(), max_candidates=5)
    assert len(capped) == 5


def test_deduplicates_identical_inputs(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "a.py").write_text("A = {'body': 'same'}\n", encoding="utf-8")
    (tests_dir / "b.py").write_text("B = {'body': 'same'}\n", encoding="utf-8")
    candidates = find_candidate_inputs(tmp_path, _site())
    bodies = [c.inputs.get("body") for c in candidates]
    assert bodies.count("same") == 1


def test_returns_empty_when_no_search_dirs_exist(tmp_path: Path) -> None:
    assert find_candidate_inputs(tmp_path, _site()) == []
