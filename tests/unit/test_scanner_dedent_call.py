"""Tests for ``textwrap.dedent(...)``-call recognition in extract_template.

The cc-project eval surfaced ``HEAVEN_WORLD_RULES = dedent(\"\"\"...\"\"\")``-
shaped constants whose UI ``template_text`` field was empty even though
the literal body was right there in source. Before this PR
``extract_template`` only matched ``ast.Constant`` / ``ast.JoinedStr``
/ ``ast.BinOp(Add)`` / ``"...".format()`` — every dedent-wrapped string
fell through to UNRESOLVED.

This file pins the new branch:

- bare ``dedent("...")`` and module-prefixed ``textwrap.dedent("...")``
  both unwrap to the literal text.
- f-strings inside ``dedent(f"...{name}...")`` resolve through the
  recursive call so the variable surface stays correct.
- Multi-argument or kwarg dedent calls (rare but possible: a project-
  specific helper named ``dedent``) degrade to UNRESOLVED rather than
  guessing.
"""

from __future__ import annotations

import ast
import textwrap

from aitap.scanner.models import TemplateKind
from aitap.scanner.rules.prompt_extractor import extract_template


def _parse_expr(source: str) -> ast.AST:
    tree = ast.parse(textwrap.dedent(source))
    expr = tree.body[0]
    assert isinstance(expr, ast.Expr)
    return expr.value


# --------------------------------------------------------------------------- #
# Happy path                                                                  #
# --------------------------------------------------------------------------- #


def test_bare_dedent_call_resolves_to_literal_text() -> None:
    expr = _parse_expr('dedent("You are a storytelling assistant.")')
    text, kind, variables = extract_template(expr)
    assert text == "You are a storytelling assistant."
    assert kind is TemplateKind.LITERAL
    assert variables == []


def test_textwrap_dedent_call_resolves_to_literal_text() -> None:
    """The cc-project shape: ``HEAVEN_WORLD_RULES = dedent('''...''')``."""
    expr = _parse_expr(
        '''
        textwrap.dedent("""
            You are a storytelling assistant.
            === RULES ===
            1. Eternal life.
        """)
        '''
    )
    text, kind, _ = extract_template(expr)
    assert kind is TemplateKind.LITERAL
    assert "Eternal life" in text
    assert "RULES" in text


def test_dedent_around_fstring_lifts_variables() -> None:
    expr = _parse_expr('dedent(f"Tell me about {topic}.")')
    text, kind, variables = extract_template(expr)
    assert kind is TemplateKind.FSTRING
    assert "{topic}" in text
    assert {v.name for v in variables} == {"topic"}


def test_dedent_around_format_call_lifts_variables() -> None:
    expr = _parse_expr('dedent("Hello {name}.".format(name="x"))')
    text, kind, variables = extract_template(expr)
    # The inner ``"...".format(name=...)`` is the FSTRING-equivalent
    # template branch; dedent unwrapping preserves it.
    assert kind is TemplateKind.FSTRING
    assert "{name}" in text
    assert {v.name for v in variables} == {"name"}


def test_module_prefixed_dedent_alias_works() -> None:
    """``import textwrap as tw; tw.dedent(...)``. We don't verify the
    receiver name, only the attribute, so a same-named helper
    (``foo.dedent(...)``) also resolves — documented in the rule.
    """
    expr = _parse_expr('tw.dedent("body")')
    text, kind, _ = extract_template(expr)
    assert text == "body"
    assert kind is TemplateKind.LITERAL


# --------------------------------------------------------------------------- #
# Guards — non-trivial shapes degrade to UNRESOLVED                           #
# --------------------------------------------------------------------------- #


def test_dedent_with_two_positional_args_degrades_to_unresolved() -> None:
    """Not the canonical 1-arg shape; bail rather than guess which arg
    is the template."""
    expr = _parse_expr('dedent("a", "b")')
    text, kind, _ = extract_template(expr)
    assert kind is TemplateKind.UNRESOLVED
    assert text == ""


def test_dedent_with_kwarg_degrades_to_unresolved() -> None:
    """``dedent(text="...")`` — project-specific helper signature; not
    what textwrap.dedent accepts. Degrade to UNRESOLVED."""
    expr = _parse_expr('dedent(text="body")')
    text, kind, _ = extract_template(expr)
    assert kind is TemplateKind.UNRESOLVED
    assert text == ""


def test_dedent_with_no_args_degrades_to_unresolved() -> None:
    expr = _parse_expr("dedent()")
    text, kind, _ = extract_template(expr)
    assert kind is TemplateKind.UNRESOLVED
    assert text == ""


def test_dedent_wrapping_a_name_is_unresolved() -> None:
    """``dedent(body)`` — body is a Name reference; we don't try to
    resolve cross-line variable bindings here."""
    expr = _parse_expr("dedent(body)")
    text, kind, _ = extract_template(expr)
    assert kind is TemplateKind.UNRESOLVED
    assert text == ""


def test_non_dedent_call_is_unchanged() -> None:
    """Regression: arbitrary one-arg calls don't unwrap. ``mycall("x")``
    isn't dedent — must still degrade to UNRESOLVED, not pretend to
    extract the literal."""
    expr = _parse_expr('mycall("hello")')
    text, kind, _ = extract_template(expr)
    assert kind is TemplateKind.UNRESOLVED
    assert text == ""


def test_method_call_on_string_format_path_still_resolves() -> None:
    """Regression guard for the pre-existing ``"...".format(x=...)``
    branch — adding the dedent branch must not steal calls that the
    format branch already handled.
    """
    expr = _parse_expr('"Hi {who}".format(who="there")')
    text, kind, variables = extract_template(expr)
    assert kind is TemplateKind.FSTRING
    assert text == "Hi {who}"
    assert {v.name for v in variables} == {"who"}


# --------------------------------------------------------------------------- #
# ``.strip()`` / ``.lstrip()`` / ``.rstrip()`` chaining                       #
# --------------------------------------------------------------------------- #


def test_dedent_then_strip_chain_resolves_through_both_calls() -> None:
    """The actual cc-project shape:
    ``HEAVEN_WORLD_RULES = dedent('''...''').strip()``. Without the
    strip-unwrapping branch the whole prompt body collapsed to
    UNRESOLVED even though it's a static literal.
    """
    expr = _parse_expr(
        '''
        dedent("""
            === RULES ===
            1. Eternal life.
        """).strip()
        '''
    )
    text, kind, _ = extract_template(expr)
    assert kind is TemplateKind.LITERAL
    assert "Eternal life" in text
    assert "RULES" in text


def test_literal_lstrip_resolves() -> None:
    expr = _parse_expr('"  hello".lstrip()')
    text, kind, _ = extract_template(expr)
    assert kind is TemplateKind.LITERAL
    # Whitespace trim happens at render time, not in our text — we report
    # the source literal as-written and let the consumer decide.
    assert text == "  hello"


def test_literal_rstrip_resolves() -> None:
    expr = _parse_expr('"hello  ".rstrip()')
    text, kind, _ = extract_template(expr)
    assert kind is TemplateKind.LITERAL
    assert text == "hello  "


def test_strip_with_argument_still_resolves_receiver() -> None:
    """``"x,y,z".strip(",")`` — we still want the receiver's text;
    the argument only affects edges, not the body the user reads."""
    expr = _parse_expr('"x,y,z".strip(",")')
    text, kind, _ = extract_template(expr)
    assert kind is TemplateKind.LITERAL
    assert text == "x,y,z"


def test_strip_around_non_template_receiver_is_unresolved() -> None:
    """``foo().strip()`` — the receiver isn't a template-shaped node
    we can read, so even though strip matches we degrade to UNRESOLVED.
    Documents that strip-unwrapping doesn't invent text."""
    expr = _parse_expr("get_body().strip()")
    text, kind, _ = extract_template(expr)
    assert kind is TemplateKind.UNRESOLVED
    assert text == ""
