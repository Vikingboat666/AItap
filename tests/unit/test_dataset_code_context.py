"""Unit tests for :mod:`aitap.dataset.code_context`."""

from __future__ import annotations

from pathlib import Path

import pytest

from aitap.dataset.code_context import infer_input_shape
from aitap.scanner.models import (
    CodeLocation,
    Confidence,
    Message,
    PromptSite,
    Provider,
    Role,
)

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures"


def _site_at(file: str, line: int, *, name: str = "anon") -> PromptSite:
    return PromptSite(
        id="x",
        name=name,
        provider=Provider.OPENAI,
        location=CodeLocation(file=file, line_start=line, line_end=line),
        messages=[Message(role=Role.USER, template_text="hi")],
        confidence=Confidence.HIGH,
    )


def test_infer_input_shape_for_openai_basic_summarize() -> None:
    """The summarize_email call site is inside ``def summarize_email(body: str)``
    — we should pick that up as a one-field input shape with the str hint."""
    site = _site_at("openai_basic/summarize.py", line=14, name="summarize_email")
    shape = infer_input_shape(site, FIXTURES_ROOT)
    assert not shape.is_empty()
    assert shape.function_name == "summarize_email"
    assert shape.fields == {"body": "str"}
    assert shape.docstring is not None
    assert "summary" in shape.docstring.lower()


def test_infer_input_shape_for_followup_uses_two_params() -> None:
    site = _site_at("openai_basic/followup.py", line=14, name="write_followup")
    shape = infer_input_shape(site, FIXTURES_ROOT)
    assert shape.function_name == "write_followup"
    assert shape.fields == {"name": "str", "topic": "str"}


def test_infer_input_shape_returns_empty_for_missing_file(tmp_path: Path) -> None:
    site = _site_at("does/not/exist.py", line=1)
    shape = infer_input_shape(site, tmp_path)
    assert shape.is_empty()


def test_infer_input_shape_returns_empty_for_module_level_call(tmp_path: Path) -> None:
    """If the call site isn't inside any function, we have no signature to
    read; returning an empty shape is the honest answer."""
    src = tmp_path / "mod.py"
    src.write_text(
        "import x\nx.call(messages=[{'role': 'user', 'content': 'hi'}])\n",
        encoding="utf-8",
    )
    site = _site_at("mod.py", line=2)
    shape = infer_input_shape(site, tmp_path)
    assert shape.is_empty()


def test_infer_input_shape_handles_syntax_error_gracefully(tmp_path: Path) -> None:
    src = tmp_path / "broken.py"
    src.write_text("def f(:\n    pass\n", encoding="utf-8")
    site = _site_at("broken.py", line=2)
    shape = infer_input_shape(site, tmp_path)
    assert shape.is_empty()


def test_infer_input_shape_skips_self_and_cls(tmp_path: Path) -> None:
    src = tmp_path / "cls.py"
    src.write_text(
        "class A:\n"
        "    def run(self, body: str, count: int) -> None:\n"
        "        x.call(messages=[{'role': 'user', 'content': body}])\n",
        encoding="utf-8",
    )
    site = _site_at("cls.py", line=3)
    shape = infer_input_shape(site, tmp_path)
    assert shape.function_name == "run"
    assert shape.fields == {"body": "str", "count": "int"}


def test_infer_input_shape_picks_innermost_function(tmp_path: Path) -> None:
    """Nested functions: the inner def's args win because the call site lives
    inside it. Outer args would be misleading."""
    src = tmp_path / "nested.py"
    src.write_text(
        "def outer(big: str) -> str:\n"
        "    def inner(small: int) -> int:\n"
        "        x.call(messages=[{'role': 'user', 'content': str(small)}])\n"
        "        return small\n"
        "    return inner(1)\n",
        encoding="utf-8",
    )
    site = _site_at("nested.py", line=3)
    shape = infer_input_shape(site, tmp_path)
    assert shape.function_name == "inner"
    assert shape.fields == {"small": "int"}


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        ("list[str]", "list[str]"),
        ("dict[str, int]", "dict[str, int]"),
    ],
)
def test_infer_input_shape_renders_complex_annotations(
    tmp_path: Path, annotation: str, expected: str
) -> None:
    src = tmp_path / "complex.py"
    src.write_text(
        f"def f(items: {annotation}) -> None:\n"
        "    x.call(messages=[{'role': 'user', 'content': str(items)}])\n",
        encoding="utf-8",
    )
    site = _site_at("complex.py", line=2)
    shape = infer_input_shape(site, tmp_path)
    assert shape.fields == {"items": expected}
