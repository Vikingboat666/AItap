"""Unit tests for :mod:`aitap.dataset.seed`."""

from __future__ import annotations

from pathlib import Path

import pytest

from aitap.dataset.seed import load_seeds, normalize_seeds, save_seeds
from aitap.dataset.types import Case, case_id


@pytest.fixture()
def datasets_dir(tmp_path: Path) -> Path:
    d = tmp_path / "datasets"
    d.mkdir()
    return d


def test_normalize_fills_id_and_site_id() -> None:
    out = normalize_seeds([{"inputs": {"q": "hi"}}], prompt_site_id="site-1")
    assert len(out) == 1
    assert out[0].prompt_site_id == "site-1"
    assert out[0].id == case_id("site-1", {"q": "hi"})
    assert out[0].source == "seed"


def test_normalize_accepts_case_objects() -> None:
    seed = Case(id="", prompt_site_id="", inputs={"q": "hi"})
    out = normalize_seeds([seed], prompt_site_id="site-1")
    assert out[0].prompt_site_id == "site-1"
    assert out[0].id == case_id("site-1", {"q": "hi"})


def test_normalize_rejects_non_dict_inputs() -> None:
    with pytest.raises(TypeError):
        normalize_seeds([{"inputs": "not-a-dict"}], prompt_site_id="site-1")


def test_save_and_load_round_trip(datasets_dir: Path) -> None:
    saved = save_seeds(
        datasets_dir,
        "summarize",
        [{"inputs": {"body": "Hello"}}, {"inputs": {"body": "World"}}],
        prompt_site_id="site-1",
    )
    assert len(saved) == 2

    loaded = load_seeds(datasets_dir, "summarize")
    assert len(loaded) == 2
    assert {c.inputs["body"] for c in loaded} == {"Hello", "World"}
    assert all(c.prompt_site_id == "site-1" for c in loaded)


def test_save_dedupes_against_existing_rows(datasets_dir: Path) -> None:
    save_seeds(
        datasets_dir,
        "summarize",
        [{"inputs": {"body": "Hello"}}],
        prompt_site_id="site-1",
    )
    second = save_seeds(
        datasets_dir,
        "summarize",
        [
            {"inputs": {"body": "Hello"}},  # duplicate
            {"inputs": {"body": "Different"}},
        ],
        prompt_site_id="site-1",
    )
    assert len(second) == 1
    assert second[0].inputs["body"] == "Different"
    assert len(load_seeds(datasets_dir, "summarize")) == 2


def test_load_returns_empty_when_missing(datasets_dir: Path) -> None:
    assert load_seeds(datasets_dir, "nope") == []


def test_load_skips_malformed_rows(datasets_dir: Path, tmp_path: Path) -> None:
    """A pre-existing JSONL file with a row that doesn't match the Case
    schema should not lock the user out — we skip it and keep going."""
    target = datasets_dir / "broken.cases.jsonl"
    target.write_text(
        '{"oops": "no required fields"}\n'
        '{"id": "x", "prompt_site_id": "s", "inputs": {"q": "ok"}}\n',
        encoding="utf-8",
    )
    cases = load_seeds(datasets_dir, "broken")
    assert len(cases) == 1
    assert cases[0].inputs == {"q": "ok"}
