"""Unit tests for the prompt-template-definition rules.

The cc-project eval surfaced that production projects keep their prompts in
a dedicated module (``app/llm/prompt_templates.py``) — 9 ``build_<task>_messages``
helpers + various ``HEAVEN_WORLD_RULES`` constants — and the SDK-call scanner
never sees them. These tests pin the two rules that close that gap:

- :func:`detect_builder_function` for ``def build_personality_messages(...) ->
  list[dict]``-style helpers.
- :func:`detect_prompt_constant` for ``SYSTEM_PROMPT = "..."``-style module
  constants.

Both rules degrade gracefully: a name match with a body too dynamic to parse
still emits a single UNRESOLVED message so the UI can surface the
definition's existence.
"""

from __future__ import annotations

import ast
import textwrap

import pytest

from aitap.scanner.models import Provider, Role, TemplateKind
from aitap.scanner.rules.template_definitions import (
    detect_builder_function,
    detect_prompt_constant,
    is_builder_name,
    is_prompt_constant_name,
)

# --------------------------------------------------------------------------- #
# Name-pattern matchers                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name",
    [
        "build_personality_messages",
        "build_plan_messages",
        "build_interaction_messages",
        "build_reflection_messages",
        "build_digest_messages",
        "build_first_meet_story_messages",
        "build_reunion_story_messages",
        "build_location_discovery_messages",
        "build_importance_scoring_messages",
        "make_chat_prompt",
        "compose_dialog_chat",
        "render_grading_template",
        "format_critic_messages",
        "get_system_messages",
        "_build_internal_messages",
    ],
)
def test_is_builder_name_accepts_known_idioms(name: str) -> None:
    """Every entry above mirrors a real Pet Heaven / LangChain naming."""
    assert is_builder_name(name) is True


@pytest.mark.parametrize(
    "name",
    [
        # Real LLM-call methods on SDK clients — must not collide.
        "create",
        "send",
        "complete",
        "invoke",
        # Generic builder names we shouldn't claim.
        "build_response",
        "build_user",
        "make_db_session",
        "format_log_line",
        # Plain test fixtures.
        "test_something",
        "setup",
        "tearDown",
        # Snake_case but no prompt suffix.
        "build_chain_summary",
    ],
)
def test_is_builder_name_rejects_unrelated_names(name: str) -> None:
    assert is_builder_name(name) is False


@pytest.mark.parametrize(
    "name",
    [
        "SYSTEM_PROMPT",
        "USER_PROMPT",
        "PROMPT_TEMPLATE",
        "REUNION_TEMPLATE",
        "COMPANION_PROMPT",
        "HEAVEN_WORLD_RULES",
        "RUBRIC_TEMPLATE",
        "CRITIC_PROMPT",
        "JUDGE_INSTRUCTIONS",
        "TEMPLATE_INTRO",
        "INSTRUCTION_FRAME",
        "PERSONA_TEMPLATE",
    ],
)
def test_is_prompt_constant_name_accepts_known_idioms(name: str) -> None:
    assert is_prompt_constant_name(name) is True


@pytest.mark.parametrize(
    "name",
    [
        # Lowercase identifiers are caller-locals, not module constants.
        "system_prompt",
        "prompt",
        # Generic uppercase constants that aren't prompts.
        "MAX_RETRIES",
        "DEFAULT_TIMEOUT",
        "DEBUG_MODE",
        # CamelCase doesn't match (we only accept ALL_CAPS_SNAKE).
        "PromptTemplate",
    ],
)
def test_is_prompt_constant_name_rejects_unrelated_names(name: str) -> None:
    assert is_prompt_constant_name(name) is False


# --------------------------------------------------------------------------- #
# detect_builder_function — happy path                                        #
# --------------------------------------------------------------------------- #


def _parse_function(source: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """Helper: parse *source* and return its single top-level function."""
    tree = ast.parse(textwrap.dedent(source))
    func = tree.body[0]
    assert isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef))
    return func


def test_detect_builder_function_returns_two_role_messages() -> None:
    source = """
        def build_personality_messages(pet):
            return [
                {"role": "system", "content": "You are a helpful pet assistant."},
                {"role": "user", "content": "Describe a pet."},
            ]
    """
    func = _parse_function(source)
    result = detect_builder_function(func, file_imports=frozenset())
    assert result is not None
    assert result.kind == "builder"
    assert result.name == "build_personality_messages"
    assert len(result.messages) == 2
    assert result.messages[0].role is Role.SYSTEM
    assert "helpful pet assistant" in result.messages[0].template_text
    assert result.messages[1].role is Role.USER
    assert "Describe a pet" in result.messages[1].template_text
    assert "template-definition" in result.tags
    assert "builder-function" in result.tags


def test_detect_builder_function_handles_named_assignment_then_return() -> None:
    """Real builders often build into a local variable then return it.

    Mirrors the Pet Heaven ``build_plan_messages`` style:

        def build_plan_messages(ctx):
            messages = [
                {"role": "system", "content": "..."},
                {"role": "user", "content": "..."},
            ]
            return messages
    """
    source = """
        def build_plan_messages(ctx):
            messages = [
                {"role": "system", "content": "Plan a day."},
                {"role": "user", "content": "Today's context."},
            ]
            return messages
    """
    func = _parse_function(source)
    result = detect_builder_function(func, file_imports=frozenset())
    assert result is not None
    assert len(result.messages) == 2
    assert result.messages[0].template_text == "Plan a day."


def test_detect_builder_function_accepts_async_def() -> None:
    source = """
        async def build_reflection_messages(memories):
            return [{"role": "system", "content": "Reflect."}]
    """
    func = _parse_function(source)
    result = detect_builder_function(func, file_imports=frozenset())
    assert result is not None
    assert result.name == "build_reflection_messages"
    assert result.messages[0].role is Role.SYSTEM


def test_detect_builder_function_rejects_unrelated_function() -> None:
    source = """
        def build_response(data):
            return {"ok": True}
    """
    func = _parse_function(source)
    result = detect_builder_function(func, file_imports=frozenset())
    assert result is None


def test_detect_builder_function_degrades_to_unresolved_on_dynamic_body() -> None:
    """A name-matched builder with an opaque body still emits a definition
    so the UI can surface the file as worth a manual look.
    """
    source = """
        def build_digest_messages(rows):
            # Returns whatever the helper builds; the literal escapes us.
            return _compose_from_rows(rows)
    """
    func = _parse_function(source)
    result = detect_builder_function(func, file_imports=frozenset())
    assert result is not None
    assert len(result.messages) == 1
    assert result.messages[0].template_kind is TemplateKind.UNRESOLVED


def test_detect_builder_function_infers_provider_from_imports() -> None:
    source = """
        def build_chat_messages():
            return [{"role": "user", "content": "hi"}]
    """
    func = _parse_function(source)

    anthropic_result = detect_builder_function(func, file_imports=frozenset({"anthropic"}))
    openai_result = detect_builder_function(func, file_imports=frozenset({"openai"}))
    none_result = detect_builder_function(func, file_imports=frozenset())

    assert anthropic_result is not None
    assert openai_result is not None
    assert none_result is not None
    assert anthropic_result.provider is Provider.ANTHROPIC
    assert openai_result.provider is Provider.OPENAI
    assert none_result.provider is Provider.UNKNOWN


# --------------------------------------------------------------------------- #
# detect_prompt_constant — happy path                                         #
# --------------------------------------------------------------------------- #


def _parse_assign(source: str) -> ast.Assign:
    """Helper: parse *source* and return its single top-level assignment."""
    tree = ast.parse(textwrap.dedent(source))
    stmt = tree.body[0]
    assert isinstance(stmt, ast.Assign)
    return stmt


def test_detect_prompt_constant_for_plain_string() -> None:
    source = 'SYSTEM_PROMPT = "You are a helpful storytelling assistant."'
    stmt = _parse_assign(source)
    result = detect_prompt_constant(stmt, file_imports=frozenset())
    assert result is not None
    assert result.kind == "constant"
    assert result.name == "SYSTEM_PROMPT"
    assert result.messages[0].role is Role.SYSTEM
    assert "helpful storytelling assistant" in result.messages[0].template_text
    assert result.messages[0].template_kind is TemplateKind.LITERAL


def test_detect_prompt_constant_for_triple_quoted_string() -> None:
    source = '''
        HEAVEN_WORLD_RULES = """
            You are a storytelling assistant.
            Death does not exist in Pet Heaven.
        """
    '''
    stmt = _parse_assign(source)
    result = detect_prompt_constant(stmt, file_imports=frozenset())
    assert result is not None
    assert result.name == "HEAVEN_WORLD_RULES"
    assert "storytelling" in result.messages[0].template_text
    assert "Pet Heaven" in result.messages[0].template_text


def test_detect_prompt_constant_for_fstring_template() -> None:
    source = """
        USER_PROMPT = f"Describe {pet_name} in {style} style."
    """
    stmt = _parse_assign(source)
    result = detect_prompt_constant(stmt, file_imports=frozenset())
    assert result is not None
    assert result.messages[0].template_kind is TemplateKind.FSTRING
    assert result.messages[0].role is Role.USER
    # Variables are surfaced by the upstream extractor.
    assert {v.name for v in result.messages[0].variables} == {"pet_name", "style"}


def test_detect_prompt_constant_role_inference_from_name_prefix() -> None:
    cases = [
        ("SYSTEM_PROMPT", Role.SYSTEM),
        ("USER_PROMPT", Role.USER),
        ("ASSISTANT_RESPONSE_TEMPLATE", Role.ASSISTANT),
        ("TOOL_INSTRUCTIONS", Role.TOOL),
        # Names without a known prefix default to USER (safest for a
        # free-floating prompt body).
        ("REUNION_TEMPLATE", Role.USER),
        ("HEAVEN_WORLD_RULES", Role.USER),
    ]
    for const_name, expected_role in cases:
        stmt = _parse_assign(f'{const_name} = "body"')
        result = detect_prompt_constant(stmt, file_imports=frozenset())
        assert result is not None
        assert result.messages[0].role is expected_role, const_name


def test_detect_prompt_constant_ignores_unrelated_names() -> None:
    source = "MAX_RETRIES = 3"
    stmt = _parse_assign(source)
    result = detect_prompt_constant(stmt, file_imports=frozenset())
    assert result is None


def test_detect_prompt_constant_degrades_when_rhs_is_function_call() -> None:
    """``MY_PROMPT = compose_prompt(...)`` — name matches, RHS is opaque.

    We still emit the definition so the operator can spot the file, but
    the message is UNRESOLVED.
    """
    source = 'PERSONA_TEMPLATE = compose_persona(template_id="default")'
    stmt = _parse_assign(source)
    result = detect_prompt_constant(stmt, file_imports=frozenset())
    assert result is not None
    assert result.messages[0].template_kind is TemplateKind.UNRESOLVED


def test_detect_prompt_constant_infers_provider() -> None:
    stmt = _parse_assign('SYSTEM_PROMPT = "hi"')
    anth = detect_prompt_constant(stmt, file_imports=frozenset({"anthropic"}))
    opai = detect_prompt_constant(stmt, file_imports=frozenset({"openai"}))
    none = detect_prompt_constant(stmt, file_imports=frozenset())
    assert anth is not None and anth.provider is Provider.ANTHROPIC
    assert opai is not None and opai.provider is Provider.OPENAI
    assert none is not None and none.provider is Provider.UNKNOWN
