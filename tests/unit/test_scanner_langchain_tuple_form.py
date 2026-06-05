"""LangChain tuple-form ``messages`` extraction tests.

LangChain's ``ChatPromptTemplate.from_messages(...)`` accepts a list of
tuples ``(role, content)`` instead of the OpenAI / Anthropic canonical
``{"role": ..., "content": ...}`` dict shape::

    ChatPromptTemplate.from_messages([
        ("system", "You are a grader."),
        ("user", "Grade {answer}."),
    ])

Before PR #49, ``extract_messages`` only recognised the dict shape; every
tuple-form list collapsed to a list of UNRESOLVED messages even when the
tuples were fully literal. These tests pin the tuple-shape parser and
the LangChain role-alias normalisation (``human`` → ``user``, ``ai`` →
``assistant``).
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from aitap.scanner.languages.python import scan_python_file
from aitap.scanner.models import Role, TemplateKind
from aitap.scanner.rules.prompt_extractor import extract_messages


def _parse_messages_arg(source: str) -> ast.AST:
    """Helper: parse ``messages=[...]`` and return the list node."""
    tree = ast.parse(textwrap.dedent(source))
    expr = tree.body[0]
    if isinstance(expr, ast.Expr):
        value = expr.value
        if isinstance(value, ast.List):
            return value
    raise AssertionError(f"expected a top-level list in: {source!r}")


# --------------------------------------------------------------------------- #
# extract_messages — pure tuple list                                          #
# --------------------------------------------------------------------------- #


def test_extract_messages_handles_pure_tuple_list() -> None:
    """LangChain idiom: every item is ``(role, content)``."""
    node = _parse_messages_arg(
        """
        [
            ("system", "You are a grader."),
            ("user", "Grade the answer."),
        ]
        """
    )
    messages = extract_messages(node)
    assert len(messages) == 2
    assert messages[0].role is Role.SYSTEM
    assert messages[0].template_text == "You are a grader."
    assert messages[0].template_kind is TemplateKind.LITERAL
    assert messages[1].role is Role.USER
    assert messages[1].template_text == "Grade the answer."


def test_extract_messages_handles_mixed_dict_and_tuple() -> None:
    """A user that ports from OpenAI to LangChain mid-file keeps both
    shapes alive. extract_messages must accept the mix.
    """
    node = _parse_messages_arg(
        """
        [
            {"role": "system", "content": "From dict."},
            ("user", "From tuple."),
        ]
        """
    )
    messages = extract_messages(node)
    assert len(messages) == 2
    assert messages[0].template_text == "From dict."
    assert messages[1].template_text == "From tuple."
    assert messages[0].role is Role.SYSTEM
    assert messages[1].role is Role.USER


# --------------------------------------------------------------------------- #
# Role aliases — LangChain uses ``human`` / ``ai``                            #
# --------------------------------------------------------------------------- #


def test_extract_messages_maps_human_alias_to_user() -> None:
    node = _parse_messages_arg('[("human", "Hi there.")]')
    messages = extract_messages(node)
    assert len(messages) == 1
    assert messages[0].role is Role.USER
    assert messages[0].template_text == "Hi there."


def test_extract_messages_maps_ai_alias_to_assistant() -> None:
    node = _parse_messages_arg('[("ai", "Hello, human.")]')
    messages = extract_messages(node)
    assert len(messages) == 1
    assert messages[0].role is Role.ASSISTANT


def test_extract_messages_maps_function_alias_to_tool() -> None:
    node = _parse_messages_arg('[("function", "Function call response.")]')
    messages = extract_messages(node)
    assert len(messages) == 1
    assert messages[0].role is Role.TOOL


def test_extract_messages_handles_role_case_insensitively() -> None:
    """LangChain documentation alternates between ``"System"`` /
    ``"system"`` / ``"SYSTEM"``. Normalising on ingestion keeps the
    enum predictable downstream.
    """
    node = _parse_messages_arg(
        """
        [
            ("System", "Cap S."),
            ("USER", "All caps."),
        ]
        """
    )
    messages = extract_messages(node)
    assert messages[0].role is Role.SYSTEM
    assert messages[1].role is Role.USER


# --------------------------------------------------------------------------- #
# Content templates inside the tuple are extracted                            #
# --------------------------------------------------------------------------- #


def test_extract_messages_resolves_fstring_inside_tuple_content() -> None:
    """``("user", f"Tell me about {topic}.")`` — content is an f-string;
    we surface it as FSTRING with the variable name captured.
    """
    node = _parse_messages_arg('[("user", f"Tell me about {topic}.")]')
    messages = extract_messages(node)
    assert len(messages) == 1
    assert messages[0].template_kind is TemplateKind.FSTRING
    assert {v.name for v in messages[0].variables} == {"topic"}


# --------------------------------------------------------------------------- #
# Guards — degenerate tuple shapes don't claim the slot                       #
# --------------------------------------------------------------------------- #


def test_one_element_tuple_is_skipped() -> None:
    """``("system",)`` (only the role, no content) isn't a valid
    message — extract_messages emits the UNRESOLVED fallback so the
    caller knows the item exists but is unparseable.
    """
    node = _parse_messages_arg('[("system",)]')
    messages = extract_messages(node)
    assert len(messages) == 1
    assert messages[0].template_kind is TemplateKind.UNRESOLVED


def test_three_element_tuple_is_skipped() -> None:
    """``("system", "...", "extra")`` — not the canonical 2-element
    shape; emit UNRESOLVED rather than guessing.
    """
    node = _parse_messages_arg('[("system", "hi", "extra")]')
    messages = extract_messages(node)
    assert messages[0].template_kind is TemplateKind.UNRESOLVED


def test_unknown_role_string_is_skipped() -> None:
    """An unrecognised role like ``"manager"`` doesn't fit the alias
    table; we degrade to UNRESOLVED instead of guessing Role.USER.
    """
    node = _parse_messages_arg('[("manager", "Approve.")]')
    messages = extract_messages(node)
    assert messages[0].template_kind is TemplateKind.UNRESOLVED


def test_non_string_first_element_is_skipped() -> None:
    """A tuple whose first element isn't a string literal can't be
    interpreted statically — UNRESOLVED is the safe call.
    """
    node = _parse_messages_arg('[(some_role, "content")]')
    messages = extract_messages(node)
    assert messages[0].template_kind is TemplateKind.UNRESOLVED


# --------------------------------------------------------------------------- #
# Integration — full scan against a LangChain-shaped fixture file             #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    return tmp_path


def _write(project_root: Path, relpath: str, source: str) -> Path:
    file_path = project_root / relpath
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(textwrap.dedent(source), encoding="utf-8")
    return file_path


def test_langchain_tuple_form_surfaces_through_builder_function(
    project_root: Path,
) -> None:
    """End-to-end: a LangChain-style ``def build_<task>_messages``
    returning a tuple-form list is recognised as a template definition
    and the role / content of every tuple item is resolved.
    """
    file_path = _write(
        project_root,
        "app/templates.py",
        """
        def build_grading_messages(question, answer):
            return [
                ("system", "You are a grader."),
                ("user", f"Question: {question}\\nAnswer: {answer}"),
                ("ai", "Score: 8/10"),
            ]
        """,
    )
    sites, _ = scan_python_file(file_path, project_root)
    assert len(sites) == 1
    site = sites[0]
    assert site.name == "build_grading_messages"
    assert len(site.messages) == 3
    assert site.messages[0].role is Role.SYSTEM
    assert site.messages[0].template_text == "You are a grader."
    assert site.messages[1].role is Role.USER
    assert site.messages[1].template_kind is TemplateKind.FSTRING
    assert site.messages[2].role is Role.ASSISTANT
    assert site.messages[2].template_text == "Score: 8/10"


def test_langchain_tuple_form_surfaces_through_wrapper_call(
    project_root: Path,
) -> None:
    """End-to-end: ``chain.invoke(messages=[("system", "..."), ...])``
    flows through the wrapper-call rule and the tuple shape is resolved.
    """
    file_path = _write(
        project_root,
        "app/chain.py",
        """
        def run_chain(chain, question):
            return chain.invoke(
                messages=[
                    ("system", "You are helpful."),
                    ("user", "{question}"),
                ],
            )
        """,
    )
    sites, _ = scan_python_file(file_path, project_root)
    assert len(sites) == 1
    site = sites[0]
    assert "wrapper-call" in site.tags
    assert len(site.messages) == 2
    assert site.messages[0].template_text == "You are helpful."
    assert site.messages[1].role is Role.USER


def test_dict_form_extraction_is_byte_for_byte_unchanged(
    project_root: Path,
) -> None:
    """Regression guard — the dict-shape path is the canonical OpenAI /
    Anthropic form and must keep emitting identical Message objects so
    the 700+ pre-existing tests stay green.
    """
    file_path = _write(
        project_root,
        "app/cookbook.py",
        """
        def build_chat_messages():
            return [
                {"role": "system", "content": "Dict shape."},
                {"role": "user", "content": "Still dict shape."},
            ]
        """,
    )
    sites, _ = scan_python_file(file_path, project_root)
    assert len(sites) == 1
    site = sites[0]
    assert site.messages[0].template_text == "Dict shape."
    assert site.messages[0].role is Role.SYSTEM
    assert site.messages[1].template_text == "Still dict shape."
