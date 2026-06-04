"""Unit tests for the wrapper-style LLM-call detection rule.

The cc-project eval found 8 sites in ``backend/app/agents/*.py`` shaped
like ``await self._llm.complete(messages, task_type="digest")`` that the
SDK-call rule missed. These tests pin the rule that closes that gap.

Two pre-flight checks (allow-list dispatching + receiver naming) are
covered as parametrized cases. The full detection function is then
exercised against fabricated AST snippets shaped like Pet Heaven /
LangChain / OpenAI-cookbook idioms.
"""

from __future__ import annotations

import ast
import textwrap

import pytest

from aitap.scanner.models import Provider, TemplateKind
from aitap.scanner.rules.wrapper_calls import (
    detect_wrapper_call,
    is_wrapper_call,
)

# --------------------------------------------------------------------------- #
# is_wrapper_call — cheap shape check                                         #
# --------------------------------------------------------------------------- #


def _parse_call(source: str) -> ast.Call:
    tree = ast.parse(textwrap.dedent(source))
    expr = tree.body[0]
    if isinstance(expr, ast.Expr):
        value = expr.value
        if isinstance(value, ast.Call):
            return value
        if isinstance(value, ast.Await) and isinstance(value.value, ast.Call):
            return value.value
        if isinstance(value, ast.Attribute):
            pass
    if isinstance(expr, ast.AsyncFunctionDef):
        # Find the first await in the body.
        for stmt in expr.body:
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Await):
                inner = stmt.value.value
                if isinstance(inner, ast.Call):
                    return inner
    raise AssertionError(f"could not extract a Call from: {source!r}")


@pytest.mark.parametrize(
    "method_name",
    [
        "complete",
        "acomplete",
        "invoke",
        "ainvoke",
        "chat",
        "achat",
        "generate",
        "agenerate",
        "send",
        "asend",
        "run",
        "arun",
        "predict",
        "apredict",
        "chat_complete",
        "generate_response",
    ],
)
def test_is_wrapper_call_accepts_known_wrapper_methods(method_name: str) -> None:
    """Every entry above shows up in real wrapper APIs (LangChain idiom)."""
    source = f"self._llm.{method_name}(messages)"
    call = _parse_call(source)
    assert is_wrapper_call(call) is True


@pytest.mark.parametrize(
    "method_name",
    [
        # Real SDK methods — handled by ``sdk_calls``, not this rule.
        "create",
        # Common non-LLM method names.
        "save",
        "delete",
        "query",
        "fetch",
        "encode",
    ],
)
def test_is_wrapper_call_rejects_non_wrapper_methods(method_name: str) -> None:
    source = f"obj.{method_name}(payload)"
    call = _parse_call(source)
    assert is_wrapper_call(call) is False


def test_is_wrapper_call_rejects_bare_name_calls() -> None:
    """``run(messages)`` (no attribute access) isn't a wrapper call shape."""
    source = "run(messages)"
    call = _parse_call(source)
    assert is_wrapper_call(call) is False


# --------------------------------------------------------------------------- #
# detect_wrapper_call — happy path                                            #
# --------------------------------------------------------------------------- #


def test_detect_wrapper_call_for_pet_heaven_pattern() -> None:
    """Exact shape from ``backend/app/agents/digest_generator.py:122``:

        raw = await self._llm.complete(messages, task_type="digest")

    Receiver ``self._llm`` is LLM-ish, first positional is a Name with
    a messages-like name — degrade to one UNRESOLVED message (the
    builder helper produces the real text, which lives elsewhere).
    """
    source = 'self._llm.complete(messages, task_type="digest")'
    call = _parse_call(source)
    result = detect_wrapper_call(call, file_imports=frozenset())
    assert result is not None
    assert result.receiver_name == "self._llm"
    assert result.method_name == "complete"
    assert len(result.messages) == 1
    assert result.messages[0].template_kind is TemplateKind.UNRESOLVED
    assert "wrapper-call" in result.tags
    assert "first-positional-name" in result.tags


def test_detect_wrapper_call_with_messages_kwarg_and_literal_list() -> None:
    """LangChain-style: ``chain.invoke(messages=[...])``."""
    source = """
        chain.invoke(
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "hi"},
            ],
            temperature=0.7,
        )
    """
    call = _parse_call(source)
    result = detect_wrapper_call(call, file_imports=frozenset())
    assert result is not None
    assert result.method_name == "invoke"
    assert len(result.messages) == 2
    assert result.messages[0].template_text == "You are helpful."
    assert result.parameters.temperature == 0.7
    assert "kw-messages" in result.tags


def test_detect_wrapper_call_with_first_positional_list_literal() -> None:
    source = """
        await client.acomplete([
            {"role": "user", "content": "summarise this"},
        ])
    """
    call = _parse_call(source)
    result = detect_wrapper_call(call, file_imports=frozenset())
    assert result is not None
    assert result.method_name == "acomplete"
    assert result.messages[0].template_text == "summarise this"
    assert "first-positional-list" in result.tags


def test_detect_wrapper_call_with_prompt_keyword_only() -> None:
    """A completion-style wrapper that takes a single ``prompt`` string."""
    source = 'llm.predict(prompt="What is 2+2?")'
    call = _parse_call(source)
    result = detect_wrapper_call(call, file_imports=frozenset())
    assert result is not None
    assert len(result.messages) == 1
    assert result.messages[0].template_text == "What is 2+2?"


def test_detect_wrapper_call_provider_inferred_from_imports() -> None:
    source = "self._llm.complete(messages)"
    call = _parse_call(source)

    anth = detect_wrapper_call(call, file_imports=frozenset({"anthropic"}))
    opai = detect_wrapper_call(call, file_imports=frozenset({"openai"}))
    none = detect_wrapper_call(call, file_imports=frozenset())

    assert anth is not None and anth.provider is Provider.ANTHROPIC
    assert opai is not None and opai.provider is Provider.OPENAI
    assert none is not None and none.provider is Provider.UNKNOWN


# --------------------------------------------------------------------------- #
# detect_wrapper_call — false-positive guards                                 #
# --------------------------------------------------------------------------- #


def test_detect_wrapper_call_rejects_non_llm_receiver_without_signal() -> None:
    """``self.db.invoke(query)`` — receiver isn't LLM-ish, no signal kwargs,
    no messages-shaped positional. Must not claim the site.
    """
    source = "self.db.invoke(query)"
    call = _parse_call(source)
    result = detect_wrapper_call(call, file_imports=frozenset())
    assert result is None


def test_detect_wrapper_call_accepts_non_llm_receiver_with_strong_signal() -> None:
    """``handler.run(messages=[...])`` — receiver name isn't LLM-ish but the
    ``messages=`` kwarg is a strong enough signal on its own.
    """
    source = """
        handler.run(
            messages=[{"role": "user", "content": "hi"}],
        )
    """
    call = _parse_call(source)
    result = detect_wrapper_call(call, file_imports=frozenset())
    assert result is not None
    assert result.messages[0].template_text == "hi"


def test_detect_wrapper_call_rejects_method_not_on_allow_list() -> None:
    """Even with a perfect LLM-ish receiver, an unknown method is out
    of scope — the SDK-call rule covers ``messages.create`` already and
    we don't claim arbitrary methods on a like-named client.
    """
    source = "self._llm.fancy_new_thing(messages)"
    call = _parse_call(source)
    result = detect_wrapper_call(call, file_imports=frozenset())
    assert result is None


def test_detect_wrapper_call_requires_some_message_source() -> None:
    """``self._llm.complete()`` (no args, no kwargs) is on the method
    allow-list and the receiver is LLM-ish, but with nothing to extract
    we don't have enough to call it a real call site.
    """
    source = "self._llm.complete()"
    call = _parse_call(source)
    result = detect_wrapper_call(call, file_imports=frozenset())
    assert result is None


def test_detect_wrapper_call_handles_chained_attribute_receiver() -> None:
    """``self.executor.llm.invoke(messages)`` — receiver renders as
    ``self.executor.llm`` and the substring match still picks it up.
    """
    source = "self.executor.llm.invoke(messages)"
    call = _parse_call(source)
    result = detect_wrapper_call(call, file_imports=frozenset())
    assert result is not None
    assert result.receiver_name == "self.executor.llm"


def test_detect_wrapper_call_extracts_parameters() -> None:
    source = """
        self.client.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="claude-3-opus",
            temperature=0.2,
            max_tokens=200,
        )
    """
    call = _parse_call(source)
    result = detect_wrapper_call(call, file_imports=frozenset())
    assert result is not None
    assert result.parameters.model == "claude-3-opus"
    assert result.parameters.temperature == 0.2
    assert result.parameters.max_tokens == 200
