"""YAML/JSONL file I/O tests for ``store/files.py``."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from aitap.scanner.models import (
    CallParameters,
    CodeLocation,
    Confidence,
    EdgeKind,
    Message,
    Pipeline,
    PipelineEdge,
    PipelineNode,
    PromptSite,
    Provider,
    Role,
)
from aitap.store import files


@pytest.fixture()
def prompts_dir(tmp_path: Path) -> Path:
    d = tmp_path / "prompts"
    d.mkdir()
    return d


@pytest.fixture()
def pipelines_dir(tmp_path: Path) -> Path:
    d = tmp_path / "pipelines"
    d.mkdir()
    return d


@pytest.fixture()
def datasets_dir(tmp_path: Path) -> Path:
    d = tmp_path / "datasets"
    d.mkdir()
    return d


def _site(name: str = "summarize_email") -> PromptSite:
    return PromptSite(
        id="abc123",
        name=name,
        provider=Provider.OPENAI,
        location=CodeLocation(file="x.py", line_start=10, line_end=12),
        messages=[Message(role=Role.USER, template_text="hello {who}")],
        parameters=CallParameters(model="gpt-4o", temperature=0.2, max_tokens=100),
        confidence=Confidence.HIGH,
    )


def _pipe() -> Pipeline:
    return Pipeline(
        id="pipe1",
        name="workflow",
        nodes=[PipelineNode(prompt_id="a"), PipelineNode(prompt_id="b")],
        edges=[PipelineEdge(source="a", target="b", kind=EdgeKind.LANGCHAIN_PIPE)],
        entry_points=["a"],
        exit_points=["b"],
    )


def test_write_prompt_round_trip(prompts_dir: Path) -> None:
    site = _site()
    path = files.write_prompt(prompts_dir, site)
    assert path.exists()
    # Filename = <safe_name>.<id_short>.prompt.yaml — the id suffix is what
    # disambiguates same-named prompts (see Bug #1 regression).
    assert path.name == "summarize_email.abc123.prompt.yaml"

    loaded = files.read_prompt(path)
    assert loaded == site


def test_write_prompt_unsafe_name_is_sanitised(prompts_dir: Path) -> None:
    """Prompt names from user code may contain spaces, slashes, or unicode —
    the filename must be safely flattened."""
    site = _site(name="../etc/passwd")
    path = files.write_prompt(prompts_dir, site)
    assert path.parent == prompts_dir  # NEVER escape the target dir
    assert path.name == "etc-passwd.abc123.prompt.yaml"


def test_write_prompt_disambiguates_same_named_sites(prompts_dir: Path) -> None:
    """Regression: integration testing showed two PromptSites that derive
    the same name (multiple LLM calls inside one wrapper function — very
    common) used to overwrite each other on disk because the YAML filename
    was just the name. Now the id-suffix keeps them distinct.
    """
    site_a = PromptSite(
        id="aaaaaaaaaaaa",
        name="workflow",
        provider=Provider.OPENAI,
        location=CodeLocation(file="x.py", line_start=10, line_end=12),
        messages=[Message(role=Role.USER, template_text="first")],
        parameters=CallParameters(),
        confidence=Confidence.HIGH,
    )
    site_b = site_a.model_copy(update={"id": "bbbbbbbbbbbb"})
    path_a = files.write_prompt(prompts_dir, site_a)
    path_b = files.write_prompt(prompts_dir, site_b)

    assert path_a != path_b, "same-named sites must land in distinct files"
    assert path_a.exists() and path_b.exists()
    assert files.read_prompt(path_a) == site_a
    assert files.read_prompt(path_b) == site_b
    assert len(files.list_prompts(prompts_dir)) == 2


def test_write_prompt_is_byte_stable(prompts_dir: Path) -> None:
    """Two writes of the same input must produce byte-identical files so
    re-running ``aitap scan`` doesn't churn git history."""
    site = _site()
    path1 = files.write_prompt(prompts_dir, site)
    bytes1 = path1.read_bytes()
    path2 = files.write_prompt(prompts_dir, site)
    bytes2 = path2.read_bytes()
    assert bytes1 == bytes2


def test_write_prompt_yaml_preserves_field_order(prompts_dir: Path) -> None:
    """The YAML must list keys in pydantic field order, not alphabetical —
    that's what makes prompts/*.yaml diffable."""
    site = _site()
    path = files.write_prompt(prompts_dir, site)
    text = path.read_text(encoding="utf-8")
    # First key is `id`, second `name` (matches PromptSite field order).
    first_key_line = text.splitlines()[0]
    assert first_key_line.startswith("id:")


def test_list_prompts_finds_only_prompt_yaml(prompts_dir: Path) -> None:
    files.write_prompt(prompts_dir, _site("alpha"))
    files.write_prompt(prompts_dir, _site("beta"))
    (prompts_dir / "garbage.txt").write_text("noise")
    listed = files.list_prompts(prompts_dir)
    assert len(listed) == 2
    assert all(p.name.endswith(".prompt.yaml") for p in listed)


def test_write_pipeline_round_trip(pipelines_dir: Path) -> None:
    pipe = _pipe()
    path = files.write_pipeline(pipelines_dir, pipe)
    # Same id-suffix discipline as prompts (see Bug #1 regression).
    assert path.name == "workflow.pipe1.pipeline.yaml"
    loaded = files.read_pipeline(path)
    assert loaded == pipe


def test_append_cases_writes_jsonl(datasets_dir: Path) -> None:
    files.append_cases(datasets_dir, "summarize", [{"input": "first"}, {"input": "second"}])
    files.append_cases(datasets_dir, "summarize", [{"input": "third"}])
    cases = files.read_cases(datasets_dir / "summarize.cases.jsonl")
    assert cases == [{"input": "first"}, {"input": "second"}, {"input": "third"}]


def test_append_cases_sorts_keys_for_byte_stability(datasets_dir: Path) -> None:
    """A re-emission of the same dict (in different key order) must produce
    the same line."""
    files.append_cases(datasets_dir, "x", [{"b": 2, "a": 1}])
    line = (datasets_dir / "x.cases.jsonl").read_text(encoding="utf-8").strip()
    # sort_keys=True so this is fully deterministic
    assert line == '{"a": 1, "b": 2}'


def test_read_cases_handles_missing_file(datasets_dir: Path) -> None:
    assert files.read_cases(datasets_dir / "nope.cases.jsonl") == []


def test_yaml_can_be_parsed_by_pyyaml(prompts_dir: Path) -> None:
    """Sanity check: our output is valid YAML, not just well-formed text."""
    path = files.write_prompt(prompts_dir, _site())
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert parsed["name"] == "summarize_email"
