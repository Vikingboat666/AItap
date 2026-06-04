"""Detect *wrapper-style LLM calls* — invocations on project-owned client
classes that ultimately dispatch to a real SDK but don't themselves match
:mod:`aitap.scanner.rules.sdk_calls`.

Why this exists
---------------

Production projects of any size wrap their LLM client behind a thin
project-owned abstraction so a single class handles retries, logging,
cost-gates, multiple providers, prompt-versioning, and so on. The
cc-project (Pet Heaven) eval surfaced eight such call sites in
``backend/app/agents/*.py``:

    raw = await self._llm.complete(messages, task_type="digest")
    raw = await self._llm.complete(messages, task_type="plan")
    raw = await self._llm.complete(messages, task_type="default")
    …

The :mod:`aitap.scanner.rules.sdk_calls` family only matches direct SDK
attribute paths (``chat.completions.create``, ``messages.create``), so
the eight wrapper sites were silently missed even though their
``messages`` argument is already a parseable list literal coming from
the builder helpers ``wt/scanner-templates`` (PR #46) now catches.

What this catches
-----------------

A call ``<receiver>.<method>(...)`` is treated as a wrapper LLM call
when **all** of the following hold:

1. ``<method>`` is on the wrapper allow-list
   (:data:`_WRAPPER_METHODS`). The list intentionally mirrors method
   names that are common in LangChain-style abstractions (``invoke``,
   ``ainvoke``, ``run``, ``arun``) plus the Pet-Heaven idiom
   (``complete`` / ``acomplete``).

2. ``<receiver>`` either *looks like* an LLM client by name
   (:func:`_receiver_looks_llm_ish`) — substrings like ``llm``,
   ``client``, ``chat``, ``model``, ``gpt``, ``claude``, ``chain``,
   ``agent`` — or the call carries a strong LLM-specific signal
   (``messages=`` keyword, ``prompt=`` keyword, ``system=`` keyword,
   or a first positional argument that resolves to a list literal).

3. The call is **not** already matched by the SDK-call rule. The
   visitor wires :func:`is_wrapper_call` after the SDK check returns
   ``None``, so there's no double-reporting.

The receiver heuristic plus the signal-kwarg requirement keeps the
false-positive rate low. Generic ``self.db.complete()``-style calls on
unrelated objects don't match because the receiver name isn't LLM-ish
and the call carries no ``messages=`` / ``prompt=`` signal.

What this does **not** catch
----------------------------

- Wrappers that reach the SDK through ``__call__`` on a callable
  instance (``response = await llm(messages)``). The AST node here is
  a :class:`ast.Call` whose ``func`` is a Name, so there's no method
  name to dispatch on. A future rule could match Name receivers when
  the local-scope assignment shows ``llm = get_llm_client()``.
- LangChain ``Runnable`` chains composed via ``|`` (``chain.invoke``
  is already covered; mid-chain ``BaseRunnable.invoke`` indirections
  are not). Out of scope for this layer.
- Calls whose receiver name is short and uninformative
  (``c.complete(...)``). False-positive risk is too high without
  symbolic execution.

Output shape
------------

A detected wrapper site flows back into the visitor as a
:class:`WrapperCall` value. The visitor stamps it onto a
:class:`PromptSite` with:

- ``confidence``: HIGH when the call carried at least one resolvable
  message, MEDIUM otherwise (mirrors the SDK-call path).
- ``tags``: always includes ``"wrapper-call"`` so the inventory UI can
  render these distinctly from SDK calls and template definitions.
- ``provider``: inferred from file imports — ``anthropic`` / ``openai``
  → matching enum, otherwise ``UNKNOWN``. The wrapper itself dispatches
  to a real SDK; the file-level import set is the static best signal we
  have without resolving the wrapper class.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from aitap.scanner.models import (
    CallParameters,
    Message,
    Provider,
    Role,
    TemplateKind,
)
from aitap.scanner.rules.prompt_extractor import (
    extract_call_parameters,
    extract_messages,
    extract_template,
)

# ---------------------------------------------------------------------------
# Allow-lists
# ---------------------------------------------------------------------------
#
# The wrapper method allow-list is the set of method names a project-owned
# LLM wrapper is conventionally named. They are deliberately specific
# enough that ``self.db.invoke()`` style calls on a generic ORM object
# don't sneak in. The async-prefix variants (``acomplete``, ``ainvoke``)
# are explicit; ``a``-prefixed names are a LangChain convention.

_WRAPPER_METHODS: frozenset[str] = frozenset(
    {
        "complete",
        "acomplete",
        "completion",
        "completions",
        "chat_complete",
        "chat_completion",
        "invoke",
        "ainvoke",
        "generate",
        "agenerate",
        "generate_response",
        "ageneerate_response",  # typo guard — keep in case
        "send",
        "asend",
        "send_messages",
        "chat",
        "achat",
        "run",
        "arun",
        "predict",
        "apredict",
        "call",
        "acall",
        "__call__",
    }
)

# Substrings on the receiver attribute name (case-insensitive). A receiver
# named ``llm``, ``_llm``, ``client``, ``chat_client``, ``chain``,
# ``agent``, ``model``, ``gpt4``, ``claude`` is LLM-ish enough that the
# wrapper-method allow-list above plus this naming hint is sufficient to
# claim the site. The substrings are case-insensitive and conservative.

_LLM_RECEIVER_HINTS: tuple[str, ...] = (
    "llm",
    "client",
    "chat",
    "model",
    "gpt",
    "claude",
    "chain",
    "agent",
    "completion",
    "responder",
    "messenger",
    "anthropic",
    "openai",
)


# Tags applied to every wrapper-call site. The visitor merges these with
# the rule-specific extras (e.g. ``"first-positional-messages"``) so the
# inventory can group / filter on the broader ``wrapper-call`` family
# without inspecting the more specific tag.

WRAPPER_TAG = "wrapper-call"


# ---------------------------------------------------------------------------
# Detection results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WrapperCall:
    """One detected wrapper-style LLM call."""

    receiver_name: str  # "self._llm", "self.client", "chain", …
    method_name: str  # the wrapper method (e.g. ``"complete"``)
    messages: list[Message]
    parameters: CallParameters
    provider: Provider
    tags: tuple[str, ...]


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def is_wrapper_call(node: ast.Call) -> bool:
    """Cheap shape check: ``node`` is ``<something>.<method>(...)`` and
    ``<method>`` is on the wrapper allow-list. Used by the visitor as a
    pre-filter — the more expensive content check runs only if this is
    true.
    """
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    return func.attr in _WRAPPER_METHODS


def detect_wrapper_call(
    node: ast.Call,
    *,
    file_imports: frozenset[str],
) -> WrapperCall | None:
    """If *node* is a wrapper-style LLM call, return its parsed shape.

    Returns ``None`` when the cheap shape check fails or when no LLM-ish
    signal (receiver name + signal kwargs) is present.

    The visitor is responsible for *not* calling this on nodes the
    SDK-call rule already claimed.
    """
    if not is_wrapper_call(node):
        return None

    func = node.func
    assert isinstance(func, ast.Attribute)  # is_wrapper_call guard

    receiver_text = _stringify_receiver(func.value)
    method_name = func.attr

    # A non-LLM-ish receiver is fine when the call shape itself carries a
    # strong LLM signal (``messages=`` kwarg, ``prompt=`` kwarg, etc.).
    # Both falsy and the call has no signal → bail to keep precision.
    if not _receiver_looks_llm_ish(receiver_text) and not _call_has_llm_signal(node):
        return None

    messages = _messages_from_wrapper_call(node)
    if not messages:
        # No messages and no prompt signal — almost certainly not the
        # site we're looking for. Bail to keep recall honest with
        # precision.
        return None

    parameters = extract_call_parameters(node)
    provider = _infer_provider_from_imports(file_imports)
    extra_tags = _extra_tags_for_node(node)

    return WrapperCall(
        receiver_name=receiver_text,
        method_name=method_name,
        messages=messages,
        parameters=parameters,
        provider=provider,
        tags=(WRAPPER_TAG, *extra_tags),
    )


# ---------------------------------------------------------------------------
# Receiver introspection
# ---------------------------------------------------------------------------


def _stringify_receiver(node: ast.AST) -> str:
    """Render *node* as a dotted path for display + heuristics.

    ``self._llm`` → ``"self._llm"``, ``client.runnable`` →
    ``"client.runnable"``. Anything we don't recognise renders as ``"?"``
    so the heuristics fall through cleanly.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _stringify_receiver(node.value)
        return f"{inner}.{node.attr}"
    if isinstance(node, ast.Subscript):
        inner = _stringify_receiver(node.value)
        return f"{inner}[…]"
    if isinstance(node, ast.Call):
        # ``factory().invoke(...)`` — render the inner call as ``"factory()"``
        # so the heuristic still has something to inspect.
        inner = _stringify_receiver(node.func)
        return f"{inner}()"
    return "?"


def _receiver_looks_llm_ish(receiver_text: str) -> bool:
    """Substring match against :data:`_LLM_RECEIVER_HINTS`, case-insensitive.

    A receiver string like ``"self._llm"`` matches ``"llm"`` and is
    considered LLM-ish. A receiver like ``"self.db"`` doesn't match any
    hint and falls through.
    """
    lowered = receiver_text.lower()
    return any(hint in lowered for hint in _LLM_RECEIVER_HINTS)


# ---------------------------------------------------------------------------
# Signal detection (messages / prompt / system kwargs or first positional)
# ---------------------------------------------------------------------------


def _call_has_llm_signal(node: ast.Call) -> bool:
    """Returns True if the call shape looks like a chat / completion call
    even when the receiver name is uninformative.

    Signals:
    - ``messages=``, ``prompt=``, ``system=`` keyword.
    - First positional argument is a list literal or a Name reference
      whose name reads like a messages variable (``messages``, ``msgs``,
      ``prompt_messages``).
    """
    for kw in node.keywords:
        if kw.arg in ("messages", "prompt", "system"):
            return True

    if node.args:
        first = node.args[0]
        if isinstance(first, ast.List):
            return True
        if isinstance(first, ast.Name):
            return _name_looks_like_messages(first.id)

    return False


_MESSAGES_NAME_RE = ("messages", "msgs", "chat", "prompt", "history")


def _name_looks_like_messages(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in _MESSAGES_NAME_RE)


# ---------------------------------------------------------------------------
# Message extraction
# ---------------------------------------------------------------------------


def _messages_from_wrapper_call(node: ast.Call) -> list[Message]:
    """Pull a :class:`Message` list out of the call.

    Priority:
    1. ``messages=`` keyword (an explicit list literal).
    2. ``prompt=`` keyword (a single user message).
    3. First positional argument when it's a list literal.
    4. First positional argument when it's a Name reference matching the
       messages-variable heuristic → returns one UNRESOLVED message
       tagged so the operator knows the site exists but text needs
       deeper analysis.
    """
    messages_kw = _kwarg(node, "messages")
    if messages_kw is not None:
        extracted = extract_messages(messages_kw, system_node=_kwarg(node, "system"))
        if extracted:
            return extracted

    prompt_kw = _kwarg(node, "prompt")
    if prompt_kw is not None:
        text, kind, variables = extract_template(prompt_kw)
        return [
            Message(
                role=Role.USER,
                template_text=text,
                template_kind=kind,
                variables=variables,
            )
        ]

    if node.args:
        first = node.args[0]
        if isinstance(first, ast.List):
            extracted = extract_messages(first)
            if extracted:
                return extracted
        if isinstance(first, ast.Name) and _name_looks_like_messages(first.id):
            return [
                Message(
                    role=Role.USER,
                    template_text="",
                    template_kind=TemplateKind.UNRESOLVED,
                    variables=[],
                )
            ]

    return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _kwarg(call: ast.Call, name: str) -> ast.AST | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _extra_tags_for_node(node: ast.Call) -> tuple[str, ...]:
    """Inventory-grouping tags derived from the call shape."""
    tags: list[str] = []
    if any(kw.arg == "messages" for kw in node.keywords):
        tags.append("kw-messages")
    elif node.args:
        first = node.args[0]
        if isinstance(first, ast.List):
            tags.append("first-positional-list")
        elif isinstance(first, ast.Name):
            tags.append("first-positional-name")
    return tuple(tags)


def _infer_provider_from_imports(file_imports: frozenset[str]) -> Provider:
    """Same fall-back logic the template-definition rule uses.

    A wrapper file usually imports its own client module (``from app.llm.client
    import get_llm_client``), which doesn't tell us which SDK ultimately
    runs. We only mark ``ANTHROPIC`` / ``OPENAI`` if the wrapper module
    directly imports the SDK package — otherwise return ``UNKNOWN`` and
    let the SDK-call sites in the wrapper module's implementation file
    provide the ground truth.
    """
    if "anthropic" in file_imports:
        return Provider.ANTHROPIC
    if "openai" in file_imports:
        return Provider.OPENAI
    return Provider.UNKNOWN
