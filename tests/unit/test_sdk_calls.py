"""Tests for :mod:`aitap.scanner.rules.sdk_calls`."""

from __future__ import annotations

import ast

import pytest

from aitap.scanner.models import Provider
from aitap.scanner.rules.sdk_calls import (
    KNOWN_SDK_CALLS,
    KnownCall,
    attribute_chain,
    match_call,
)


def _first_call(source: str) -> ast.Call:
    """Parse *source* and return the first :class:`ast.Call` reached."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            return node
    raise AssertionError("source contained no ast.Call")


def test_match_modern_openai_chat() -> None:
    call = _first_call(
        "client.chat.completions.create(model='m', messages=[{'role':'user','content':'hi'}])"
    )
    rule = match_call(call)
    assert rule is not None
    assert rule.provider is Provider.OPENAI
    assert rule.attribute_path == ("chat", "completions", "create")


def test_match_module_level_openai_chat() -> None:
    call = _first_call(
        "openai.chat.completions.create(model='m', messages=[{'role':'user','content':'hi'}])"
    )
    rule = match_call(call)
    assert rule is not None
    assert rule.provider is Provider.OPENAI


def test_match_anthropic_messages_create() -> None:
    call = _first_call("c.messages.create(model='m', messages=[{'role':'user','content':'q'}])")
    rule = match_call(call)
    assert rule is not None
    assert rule.provider is Provider.ANTHROPIC
    assert rule.system_kw == "system"


def test_match_responses_api() -> None:
    call = _first_call("client.responses.create(model='m', input='hi')")
    rule = match_call(call)
    assert rule is not None
    assert rule.provider is Provider.OPENAI
    assert rule.messages_kw == "input"


def test_no_match_unrelated_call() -> None:
    call = _first_call("logger.info('hello world')")
    assert match_call(call) is None


def test_no_match_when_chain_too_short() -> None:
    # `create(...)` alone is ambiguous — we require the suffix, not just the leaf.
    call = _first_call("create(model='m', messages=[])")
    assert match_call(call) is None


@pytest.mark.parametrize(
    "source, expected",
    [
        ("a.b.c()", ("a", "b", "c")),
        ("foo()", ("foo",)),
        ("OpenAI().chat.completions.create()", ("<call>", "chat", "completions", "create")),
    ],
)
def test_attribute_chain(source: str, expected: tuple[str, ...]) -> None:
    call = _first_call(source)
    assert attribute_chain(call.func) == expected


def test_known_calls_are_unique_per_path() -> None:
    """A new contributor adding a duplicate signature should fail this test."""
    seen: set[tuple[str, ...]] = set()
    for rule in KNOWN_SDK_CALLS:
        assert isinstance(rule, KnownCall)
        assert rule.attribute_path not in seen, f"duplicate rule: {rule.attribute_path}"
        seen.add(rule.attribute_path)
