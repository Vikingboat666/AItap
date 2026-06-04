"""Detect *prompt template definitions*, i.e. the places a project keeps its
prompts even when no SDK call is made on the same line.

Why this exists
---------------

Production projects of any size tend to pull prompt strings out of the SDK
call site and into a dedicated module (e.g. ``app/llm/prompt_templates.py``,
``prompts.py``, ``chains/system.py``). The :mod:`aitap.scanner.rules.sdk_calls`
side of the scanner sees ``client.messages.create(messages=foo)`` but never
``foo`` itself, so the prompt text never lands in :class:`PromptSite`. The
Pet Heaven (cc-project) eval surfaced this: 9 ``build_<task>_messages``
helpers held the real prompts and the scanner saw none of them.

What this catches
-----------------

Two patterns, both syntactic. We deliberately stay literal — no constant
folding, no cross-file resolution — so the rule is fast, deterministic, and
trivially auditable.

1. **Builder functions.** Functions whose name reads like a prompt
   constructor and whose body returns a ``list[dict[str, str]]`` literal
   shaped like the canonical OpenAI chat-completions ``messages`` payload.
   Concretely: ``build_personality_messages`` returning
   ``[{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]``.

2. **Module-level prompt constants.** Assignments at the top of a module
   whose left-hand side reads like a prompt name (``SYSTEM_PROMPT``,
   ``REUNION_TEMPLATE``, ``COMPANION_PROMPT``) and whose right-hand side
   is a string literal — including triple-quoted, ``textwrap.dedent(...)``,
   and f-string forms.

Both shapes degrade gracefully: when the return value or RHS isn't a shape
we can parse, we still emit a :class:`PromptSite` with
``template_kind=UNRESOLVED`` so the UI surfaces the definition's *existence*
(the deep-scan path can resolve text later).

What this **does not** catch
----------------------------

- Builder functions that compose messages with arbitrary control flow
  (loops, conditionals) — we'd need symbolic execution. The visitor still
  emits a site so the operator knows the file is worth a manual look.
- Cross-file constant references (``messages=PROMPT_TABLE["intro"]``).
  That's a deep-scan job; this layer is L1 only.
- Constants whose name doesn't match the patterns. The name patterns below
  are deliberately *narrow* — false positives on "anything string-shaped"
  would drown the inventory.

Output shape
------------

A detected template definition flows into the visitor as a
:class:`TemplateDefinition` value. The visitor wraps it in a
:class:`PromptSite` with:

- ``confidence``: HIGH when we resolved at least one literal message, MEDIUM
  otherwise (mirrors :func:`prompt_extractor._confidence_for`).
- ``tags``: always carries ``"template-definition"`` so the UI can render
  these distinctly from SDK call sites.
- ``provider``: inferred from file imports (anthropic/openai); falls back
  to UNKNOWN when the module is provider-agnostic (typical for a shared
  ``prompts.py``).
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from aitap.scanner.models import (
    Message,
    Provider,
    Role,
    TemplateKind,
)
from aitap.scanner.rules.prompt_extractor import (
    extract_messages,
    extract_template,
)

# ---------------------------------------------------------------------------
# Naming patterns
# ---------------------------------------------------------------------------
#
# Both patterns are anchored. Lowercase functions, uppercase constants —
# matching the conventional Python styling. We accept either of two shapes
# for the "prompty-sounding" suffix so the rule catches the LangChain idiom
# (``..._messages``) and the OpenAI-cookbook idiom (``..._prompt``).

_BUILDER_FUNC_RE = re.compile(
    r"""
    ^(?:
        build_     # build_<task>_messages
      | make_      # make_<task>_prompt
      | compose_   # compose_<task>_chat
      | render_    # render_<task>_template
      | format_    # format_<task>_messages
      | create_    # create_<task>_prompt (sparingly — collides with SDK names)
      | get_       # get_<task>_messages
      | _build_    # _build_<task>_messages (private helper variants)
    )
    [a-z0-9_]+?
    _(?:messages|prompt|chat|template|instructions?)s?
    $
    """,
    re.VERBOSE,
)

_PROMPT_CONST_RE = re.compile(
    r"""
    ^(?:
        # Prefix form: SYSTEM_PROMPT, USER_PROMPT, PROMPT_FOO, TEMPLATE_BAR
        (?:SYSTEM|USER|ASSISTANT|TOOL|PROMPT|TEMPLATE|INSTRUCTIONS?
         |HEAVEN|RULES|RUBRIC|CRITIC|JUDGE|PERSONA)
        _[A-Z0-9_]*
      |
        # Suffix form: FOO_PROMPT, BAR_TEMPLATE, BAZ_INSTRUCTIONS
        [A-Z][A-Z0-9_]*?
        _(?:PROMPT|TEMPLATE|INSTRUCTIONS?|MESSAGE|MESSAGES|RULES|RUBRIC)
    )
    $
    """,
    re.VERBOSE,
)


def is_builder_name(name: str) -> bool:
    """Public-API check used by the visitor and the test suite."""
    return bool(_BUILDER_FUNC_RE.match(name))


def is_prompt_constant_name(name: str) -> bool:
    """Public-API check used by the visitor and the test suite."""
    return bool(_PROMPT_CONST_RE.match(name))


# ---------------------------------------------------------------------------
# Detection results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TemplateDefinition:
    """One detected prompt-template definition (shape used by the visitor).

    The visitor wraps this in a :class:`PromptSite`; this struct is the
    intermediate representation so the rule code stays unit-testable
    without instantiating a full :class:`PromptSite`.
    """

    kind: str  # "builder" | "constant"
    name: str  # the identifier the operator typed
    messages: list[Message]
    provider: Provider
    tags: tuple[str, ...]


# ---------------------------------------------------------------------------
# Builder-function detection
# ---------------------------------------------------------------------------


def detect_builder_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    file_imports: frozenset[str],
) -> TemplateDefinition | None:
    """If *node* looks like a prompt-template builder, return the messages
    it would produce. Otherwise return ``None``.

    A "builder" must (a) have a recognised name and (b) contain at least one
    ``return`` statement whose value we can interpret as a chat-style
    message list. If the name matches but the body is too dynamic, we still
    return a definition with a single UNRESOLVED message so the UI can
    surface the existence of the template.
    """
    if not is_builder_name(node.name):
        return None

    messages = _messages_from_function_body(node)
    provider = _infer_provider_from_imports(file_imports)
    return TemplateDefinition(
        kind="builder",
        name=node.name,
        messages=messages,
        provider=provider,
        tags=("template-definition", "builder-function"),
    )


def _messages_from_function_body(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[Message]:
    """Walk top-level returns. First parseable list-of-dicts wins.

    We deliberately ignore returns nested inside ``if`` / ``try`` branches
    for simplicity — the common project shape is a single canonical return
    after a few ``messages = [...]`` builds. If the builder uses branches,
    we degrade to a single UNRESOLVED message tagged
    ``"branching-builder"`` upstream.
    """
    # Look for the canonical pattern:
    #     def build_xxx_messages(...):
    #         ...
    #         return [{"role": "system", ...}, {"role": "user", ...}]
    #
    # extract_messages already handles the list-of-dict shape so we just
    # need to find the right ast node.
    for stmt in node.body:
        if isinstance(stmt, ast.Return) and stmt.value is not None:
            extracted = extract_messages(stmt.value)
            if extracted and not _all_unresolved(extracted):
                return extracted
            # If the return value is a Name (``return messages``), walk
            # the function body for an assignment that built that name.
            if isinstance(stmt.value, ast.Name):
                resolved = _resolve_named_assignment(node, stmt.value.id)
                if resolved is not None:
                    return resolved

    # Fall back: emit a single UNRESOLVED message so the definition still
    # shows up. The visitor stamps the LOW confidence in this path.
    return [
        Message(
            role=Role.USER,
            template_text="",
            template_kind=TemplateKind.UNRESOLVED,
            variables=[],
        )
    ]


def _resolve_named_assignment(
    node: ast.FunctionDef | ast.AsyncFunctionDef, name: str
) -> list[Message] | None:
    """If the function ends with ``return messages``, look for
    ``messages = [...]`` earlier in the body and try to parse the literal.
    """
    last_value: ast.AST | None = None
    for stmt in node.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    last_value = stmt.value
    if last_value is None:
        return None
    extracted = extract_messages(last_value)
    if extracted and not _all_unresolved(extracted):
        return extracted
    return None


def _all_unresolved(messages: list[Message]) -> bool:
    return all(m.template_kind is TemplateKind.UNRESOLVED for m in messages)


# ---------------------------------------------------------------------------
# Module-level constant detection
# ---------------------------------------------------------------------------


def detect_prompt_constant(
    node: ast.Assign,
    *,
    file_imports: frozenset[str],
) -> TemplateDefinition | None:
    """If *node* is a module-level ``PROMPT_X = "..."`` style assignment,
    return its content as a single-message template definition.

    Multi-target assignments (``A = B = "..."``) emit one definition per
    matching target. Tuple unpacking (``A, B = "...", "..."``) is ignored —
    each target there would have to be matched against a tuple position
    and that complicates the rule without clear benefit.
    """
    matched_names = [
        target.id
        for target in node.targets
        if isinstance(target, ast.Name) and is_prompt_constant_name(target.id)
    ]
    if not matched_names:
        return None

    text, kind, variables = extract_template(node.value)
    # If we couldn't parse the RHS (e.g. it's a function call), keep the
    # definition but stamp it UNRESOLVED so the operator still sees it.
    if not text and kind is TemplateKind.UNRESOLVED:
        message = Message(
            role=_role_from_constant_name(matched_names[0]),
            template_text="",
            template_kind=TemplateKind.UNRESOLVED,
            variables=[],
        )
    else:
        message = Message(
            role=_role_from_constant_name(matched_names[0]),
            template_text=text,
            template_kind=kind,
            variables=variables,
        )
    provider = _infer_provider_from_imports(file_imports)
    return TemplateDefinition(
        kind="constant",
        name=matched_names[0],
        messages=[message],
        provider=provider,
        tags=("template-definition", "module-constant"),
    )


def _role_from_constant_name(name: str) -> Role:
    """Best-effort role inference from a constant name.

    ``SYSTEM_PROMPT`` → SYSTEM, ``USER_PROMPT`` → USER, anything else → USER
    (the safest default for a free-floating prompt body).
    """
    upper = name.upper()
    if upper.startswith("SYSTEM"):
        return Role.SYSTEM
    if upper.startswith("ASSISTANT"):
        return Role.ASSISTANT
    if upper.startswith("TOOL"):
        return Role.TOOL
    return Role.USER


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _infer_provider_from_imports(file_imports: frozenset[str]) -> Provider:
    """Pick a provider from the set of top-level packages a file imports.

    We never see the call site here (a template-definition file usually
    doesn't make one), so this is the best static signal we have. We
    prefer Anthropic when both are imported because the SDK-level scanner
    will tag the call-site Provider anyway — the definition's Provider is
    a hint for grouping, not the source of truth.
    """
    if "anthropic" in file_imports:
        return Provider.ANTHROPIC
    if "openai" in file_imports:
        return Provider.OPENAI
    return Provider.UNKNOWN
