"""Cross-project generality tests for the scanner rules.

These tests defend a single property: the rules added by PR #46
(template-definition) and PR #47 (wrapper-call) are **framework-shaped,
not project-shaped**. They were originally vetted against cc-project
(Pet Heaven); the danger of using a single sample is that an
over-specific term sneaks into the allow-list and the rule then
silently fits that one project better than the next one.

Each test below uses fixture names drawn from a real public framework
(LangChain / LlamaIndex / OpenAI Cookbook / Anthropic Cookbook /
Microsoft Semantic Kernel) so a regression that reintroduces a
project-specific allow-list entry is caught here.

The Pet-Heaven-specific words ``HEAVEN`` and ``RULES`` were prefix-allow
entries in PR #46. PR #48 removed them: ``HEAVEN_WORLD_RULES`` is still
caught by the ``_RULES`` suffix (a domain-agnostic shape), but a stand-
alone ``HEAVEN_FOO`` no longer matches. These tests pin that intent.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from aitap.scanner.languages.python import scan_python_file
from aitap.scanner.rules.template_definitions import (
    is_prompt_constant_name,
)
from aitap.scanner.rules.wrapper_calls import _WRAPPER_METHODS

# --------------------------------------------------------------------------- #
# Allow-lists are framework-shaped, not Pet-Heaven-shaped                     #
# --------------------------------------------------------------------------- #


def test_pet_heaven_specific_prefix_words_are_not_in_the_allow_list() -> None:
    """``HEAVEN_FOO`` and ``RULES_FOO`` were prefix-allow entries in
    PR #46 that over-fit the single project used to vet the rules. They
    are not generic LLM-domain vocabulary and don't belong here.
    """
    assert is_prompt_constant_name("HEAVEN_FOO") is False
    assert is_prompt_constant_name("RULES_FOO") is False


def test_domain_specific_constants_still_match_through_suffix_form() -> None:
    """The suffix form catches conventional names like
    ``HEAVEN_WORLD_RULES`` (Pet Heaven), ``GAME_RULES`` (a generic
    game project), ``SAFETY_INSTRUCTIONS`` (an OpenAI cookbook
    convention) — without anchoring on any single project's vocabulary.
    """
    assert is_prompt_constant_name("HEAVEN_WORLD_RULES") is True
    assert is_prompt_constant_name("GAME_RULES") is True
    assert is_prompt_constant_name("SAFETY_INSTRUCTIONS") is True
    assert is_prompt_constant_name("BIRDS_RUBRIC") is True
    assert is_prompt_constant_name("SUMMARISE_TEMPLATE") is True


def test_wrapper_method_typo_was_fixed() -> None:
    """``ageneerate_response`` was a typo for ``agenerate_response``.
    PR #48 removed the typo; pin so it doesn't sneak back in.
    """
    assert "agenerate_response" in _WRAPPER_METHODS
    assert "ageneerate_response" not in _WRAPPER_METHODS


# --------------------------------------------------------------------------- #
# Rule generality — LangChain-shaped fixtures                                 #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    return tmp_path


def _write(project_root: Path, relpath: str, source: str) -> Path:
    file_path = project_root / relpath
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(dedent(source), encoding="utf-8")
    return file_path


def test_langchain_style_builder_function_is_recognised(project_root: Path) -> None:
    """LangChain idiom: ``def make_grading_messages(...)`` returning the
    canonical list-of-dict messages payload. No Pet Heaven in sight.
    """
    file_path = _write(
        project_root,
        "chains/grading.py",
        """
        def make_grading_messages(question, answer):
            return [
                {"role": "system", "content": "You are a grader."},
                {"role": "user", "content": "Grade the answer."},
            ]
        """,
    )
    sites, _ = scan_python_file(file_path, project_root)
    assert len(sites) == 1
    assert sites[0].name == "make_grading_messages"
    assert "builder-function" in sites[0].tags


def test_openai_cookbook_style_constants_are_recognised(project_root: Path) -> None:
    """OpenAI Cookbook idiom: ``SUMMARISE_PROMPT`` /
    ``SAFETY_INSTRUCTIONS`` constants at module top level.
    """
    file_path = _write(
        project_root,
        "cookbook/safety.py",
        """
        SUMMARISE_PROMPT = "Summarise the following text in 3 bullets."
        SAFETY_INSTRUCTIONS = "Refuse if the input describes self-harm."
        """,
    )
    sites, _ = scan_python_file(file_path, project_root)
    names = sorted(s.name for s in sites)
    assert names == ["SAFETY_INSTRUCTIONS", "SUMMARISE_PROMPT"]


def test_anthropic_cookbook_style_builder_is_recognised(project_root: Path) -> None:
    """Anthropic-cookbook idiom: ``def compose_dialogue_chat(turns)`` —
    ``compose_`` prefix + ``_chat`` suffix.
    """
    file_path = _write(
        project_root,
        "demos/dialogue.py",
        """
        def compose_dialogue_chat(turns):
            return [
                {"role": "user", "content": "Hello."},
                {"role": "assistant", "content": "Hi there!"},
            ]
        """,
    )
    sites, _ = scan_python_file(file_path, project_root)
    assert len(sites) == 1
    assert sites[0].name == "compose_dialogue_chat"


def test_langchain_style_wrapper_invoke_is_recognised(project_root: Path) -> None:
    """LangChain idiom: ``chain.invoke(messages=[...])``.

    No ``self._llm`` Pet-Heaven shape required.
    """
    file_path = _write(
        project_root,
        "app/runner.py",
        """
        def run_chain():
            chain = get_chain()
            return chain.invoke(
                messages=[
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "What is two plus two?"},
                ],
                temperature=0.0,
            )
        """,
    )
    sites, _ = scan_python_file(file_path, project_root)
    assert len(sites) == 1
    assert sites[0].name == "run_chain"
    assert "wrapper-call" in sites[0].tags
    assert sites[0].messages[0].template_text == "You are helpful."


def test_llamaindex_style_wrapper_chat_is_recognised(project_root: Path) -> None:
    """LlamaIndex idiom: ``llm.chat(messages=[...])`` on a top-level
    ``llm`` Name. ``llm`` matches the receiver hint.
    """
    file_path = _write(
        project_root,
        "app/llama.py",
        """
        def ask_question(llm, question):
            return llm.chat(
                messages=[
                    {"role": "user", "content": "Question."},
                ],
            )
        """,
    )
    sites, _ = scan_python_file(file_path, project_root)
    assert len(sites) == 1
    assert "wrapper-call" in sites[0].tags
    assert sites[0].messages[0].template_text == "Question."


def test_semantic_kernel_style_wrapper_with_prompt_kwarg(project_root: Path) -> None:
    """Microsoft Semantic Kernel idiom: ``kernel.invoke(prompt="...")``.

    The receiver name ``kernel`` isn't on the LLM-ish hint list, but the
    ``prompt=`` keyword is itself a strong-enough signal so the rule
    still catches it without leaning on a project-specific name.
    """
    file_path = _write(
        project_root,
        "app/sk.py",
        """
        def ask(kernel):
            return kernel.invoke(prompt="What is two plus two?")
        """,
    )
    sites, _ = scan_python_file(file_path, project_root)
    assert len(sites) == 1
    assert sites[0].messages[0].template_text == "What is two plus two?"


# --------------------------------------------------------------------------- #
# False-positive guard — the same rule does NOT claim unrelated code          #
# --------------------------------------------------------------------------- #


def test_db_session_invoke_is_not_claimed(project_root: Path) -> None:
    """A generic ``session.invoke(query)`` on an ORM session must not
    sneak in. Receiver ``session`` isn't LLM-ish; call carries no
    LLM-shape signal.
    """
    file_path = _write(
        project_root,
        "app/db.py",
        """
        def run_query(session, query):
            return session.invoke(query)
        """,
    )
    sites, _ = scan_python_file(file_path, project_root)
    assert sites == []


def test_business_constants_are_not_claimed(project_root: Path) -> None:
    """Common business-logic constants must not match the rule. None of
    these end in ``PROMPT`` / ``TEMPLATE`` / ``INSTRUCTIONS`` / ``RULES``
    / ``RUBRIC`` / ``MESSAGE`` etc.
    """
    file_path = _write(
        project_root,
        "app/config.py",
        """
        MAX_RETRIES = 3
        DEFAULT_TIMEOUT = 30
        DB_POOL_SIZE = 10
        FEATURE_FLAG_NEW_UI = True
        """,
    )
    sites, _ = scan_python_file(file_path, project_root)
    assert sites == []


def test_camelcase_class_attributes_are_not_claimed(project_root: Path) -> None:
    """A constant named ``PromptTemplate`` (CamelCase) is conventionally
    a class symbol, not a module constant. We only match SNAKE_CASE
    UPPER-form constants so the rule doesn't claim class names.
    """
    file_path = _write(
        project_root,
        "app/types.py",
        """
        class PromptTemplate:
            pass

        class ChatMessage:
            pass
        """,
    )
    sites, _ = scan_python_file(file_path, project_root)
    assert sites == []


def test_builder_must_have_prompt_shaped_suffix(project_root: Path) -> None:
    """``build_response`` and ``make_db_session`` share the verb prefix
    with ``build_xxx_messages`` but don't have a prompt-shaped suffix.
    The rule must skip both.
    """
    file_path = _write(
        project_root,
        "app/utils.py",
        """
        def build_response(data):
            return {"ok": True}

        def make_db_session():
            return Session()
        """,
    )
    sites, _ = scan_python_file(file_path, project_root)
    assert sites == []
