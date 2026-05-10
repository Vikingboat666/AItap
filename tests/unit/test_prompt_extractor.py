"""Tests for :mod:`aitap.scanner.rules.prompt_extractor`."""

from __future__ import annotations

import ast

from aitap.scanner.models import Role, TemplateKind
from aitap.scanner.rules.prompt_extractor import (
    extract_call_parameters,
    extract_messages,
    extract_template,
)


def _expr(source: str) -> ast.AST:
    return ast.parse(source, mode="eval").body


def _call(source: str) -> ast.Call:
    parsed = ast.parse(source).body[0]
    if isinstance(parsed, ast.Expr) and isinstance(parsed.value, ast.Call):
        return parsed.value
    raise AssertionError("expected ast.Call")


def test_extract_template_literal() -> None:
    text, kind, variables = extract_template(_expr("'hello world'"))
    assert text == "hello world"
    assert kind is TemplateKind.LITERAL
    assert variables == []


def test_extract_template_jinja_literal() -> None:
    text, kind, variables = extract_template(_expr("'Hello, {{ name }}!'"))
    assert kind is TemplateKind.JINJA2
    assert text == "Hello, {{ name }}!"
    assert [v.name for v in variables] == ["name"]


def test_extract_template_fstring() -> None:
    text, kind, variables = extract_template(_expr("f'hi {user.name}, age {age}'"))
    assert kind is TemplateKind.FSTRING
    assert text == "hi {user.name}, age {age}"
    assert {v.name for v in variables} == {"user.name", "age"}


def test_extract_template_concat() -> None:
    text, kind, variables = extract_template(_expr("'You are ' + role + '. Answer.'"))
    assert kind is TemplateKind.CONCAT
    assert text.startswith("You are ")
    assert "{role}" in text
    assert any(v.name == "role" for v in variables)


def test_extract_template_format_method() -> None:
    text, kind, variables = extract_template(_expr("'Hello, {name}!'.format(name=user)"))
    assert kind is TemplateKind.FSTRING
    assert text == "Hello, {name}!"
    assert [v.name for v in variables] == ["name"]


def test_extract_template_unresolved_when_unknown() -> None:
    text, kind, variables = extract_template(_expr("some_call()"))
    assert text == ""
    assert kind is TemplateKind.UNRESOLVED
    assert variables == []


def test_extract_template_handles_none() -> None:
    text, kind, variables = extract_template(None)
    assert text == ""
    assert kind is TemplateKind.UNRESOLVED
    assert variables == []


def test_extract_messages_canonical_dict_list() -> None:
    node = _expr(
        "[{'role': 'system', 'content': 'You are a bot.'}, {'role': 'user', 'content': 'Hello.'}]"
    )
    messages = extract_messages(node)
    assert [m.role for m in messages] == [Role.SYSTEM, Role.USER]
    assert messages[0].template_text == "You are a bot."
    assert messages[1].template_text == "Hello."


def test_extract_messages_with_anthropic_system() -> None:
    messages_node = _expr("[{'role': 'user', 'content': 'q'}]")
    system_node = _expr("'You are concise.'")
    messages = extract_messages(messages_node, system_node=system_node)
    assert messages[0].role is Role.SYSTEM
    assert messages[0].template_text == "You are concise."
    assert messages[1].role is Role.USER


def test_extract_messages_unresolved_for_dynamic_list() -> None:
    node = _expr("build_messages()")
    messages = extract_messages(node)
    assert len(messages) == 1
    assert messages[0].template_kind is TemplateKind.UNRESOLVED


def test_extract_call_parameters() -> None:
    call = _call("c.x(model='m', temperature=0.7, max_tokens=200, top_p=0.9, foo=42)")
    params = extract_call_parameters(call)
    assert params.model == "m"
    assert params.temperature == 0.7
    assert params.max_tokens == 200
    assert params.top_p == 0.9
    assert params.extra == {"foo": "42"}


def test_extract_call_parameters_response_format_dict() -> None:
    call = _call("c.x(model='m', response_format={'type': 'json_object'})")
    params = extract_call_parameters(call)
    assert params.response_format == "json_object"


def test_extract_call_parameters_skips_messages_in_extra() -> None:
    call = _call("c.x(model='m', messages=[], system='s', input='hi', prompt='p')")
    params = extract_call_parameters(call)
    assert params.extra == {}


def test_extract_call_parameters_max_output_tokens_preserved() -> None:
    """OpenAI responses API uses max_output_tokens; we map it to max_tokens
    for the canonical slot but keep the raw kwarg name in extra so a caller
    that rebuilds an SDK invocation knows which spelling to use."""
    call = _call("c.x(model='m', max_output_tokens=512)")
    params = extract_call_parameters(call)
    assert params.max_tokens == 512
    assert params.extra.get("max_output_tokens") == "512"


def test_extract_messages_with_anthropic_system_blocks() -> None:
    """Anthropic's system= can be a list of {type:text, text:...} content
    blocks (used when callers need cache_control on prefix portions)."""
    messages_node = _expr("[{'role': 'user', 'content': 'q'}]")
    system_node = _expr(
        "[{'type': 'text', 'text': 'You are concise.'}, {'type': 'text', 'text': 'Cite sources.'}]"
    )
    messages = extract_messages(messages_node, system_node=system_node)
    assert messages[0].role is Role.SYSTEM
    assert "You are concise." in messages[0].template_text
    assert "Cite sources." in messages[0].template_text


def test_extract_messages_anthropic_system_block_unknown_type_placeholder() -> None:
    system_node = _expr("[{'type': 'image', 'source': {}}]")
    messages = extract_messages(_expr("[]"), system_node=system_node)
    assert messages[0].role is Role.SYSTEM
    assert "<image>" in messages[0].template_text
