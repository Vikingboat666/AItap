"""Known SDK call signatures for L1 prompt-site detection.

Each :class:`KnownCall` describes one canonical attribute path that resolves to
an LLM call (e.g., ``chat.completions.create`` for the modern OpenAI client).
The scanner walks Python ASTs, extracts the dotted attribute chain on every
:class:`ast.Call` node, and consults :func:`match_call` to decide whether the
node is a known LLM entry point.

We deliberately match on the **suffix** of the attribute chain rather than the
fully-qualified name. Real codebases use myriad client constructions (module
re-exports, dependency injection, factory wrappers) and we still want to catch
``some_client.chat.completions.create(...)`` even when ``some_client`` is not
syntactically a known SDK constructor. A loose suffix match keeps recall high;
:class:`Confidence` is degraded for indirect call shapes by upstream callers.

Adding a new provider:

    KNOWN_SDK_CALLS.append(
        KnownCall(
            provider=Provider.DASHSCOPE,
            attribute_path=("Generation", "call"),
            messages_kw="messages",
        )
    )
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from aitap.scanner.models import Provider


@dataclass(frozen=True)
class KnownCall:
    """One known LLM-call signature.

    Attributes:
        provider: which provider emits this call.
        attribute_path: dotted attribute suffix, expressed as a tuple from the
            outermost receiver to the called method. ``("chat", "completions",
            "create")`` matches ``client.chat.completions.create(...)`` as well
            as ``openai.chat.completions.create(...)``.
        messages_kw: name of the kwarg that carries the message list (or the
            single prompt). ``None`` if the call doesn't take messages
            (e.g., legacy completions).
        prompt_kw: name of the kwarg that carries a single prompt string,
            for completion-style endpoints. ``None`` for chat endpoints.
        system_kw: optional separate system-prompt kwarg (Anthropic).
        notes: human-readable description, surfaced in warnings.
    """

    provider: Provider
    attribute_path: tuple[str, ...]
    messages_kw: str | None = "messages"
    prompt_kw: str | None = None
    system_kw: str | None = None
    notes: str = ""
    aliases: tuple[tuple[str, ...], ...] = field(default_factory=tuple)


# ---- OpenAI ----------------------------------------------------------------

OPENAI_CHAT = KnownCall(
    provider=Provider.OPENAI,
    attribute_path=("chat", "completions", "create"),
    messages_kw="messages",
    notes="openai>=1.0 chat completion",
)

OPENAI_RESPONSES = KnownCall(
    provider=Provider.OPENAI,
    attribute_path=("responses", "create"),
    messages_kw="input",
    notes="openai responses API",
)

OPENAI_LEGACY_COMPLETION = KnownCall(
    provider=Provider.OPENAI,
    attribute_path=("completions", "create"),
    messages_kw=None,
    prompt_kw="prompt",
    notes="openai legacy text completion",
)


# ---- Anthropic -------------------------------------------------------------

ANTHROPIC_MESSAGES_CREATE = KnownCall(
    provider=Provider.ANTHROPIC,
    attribute_path=("messages", "create"),
    messages_kw="messages",
    system_kw="system",
    notes="anthropic messages.create",
)

ANTHROPIC_MESSAGES_STREAM = KnownCall(
    provider=Provider.ANTHROPIC,
    attribute_path=("messages", "stream"),
    messages_kw="messages",
    system_kw="system",
    notes="anthropic messages.stream",
)


KNOWN_SDK_CALLS: list[KnownCall] = [
    OPENAI_CHAT,
    OPENAI_RESPONSES,
    OPENAI_LEGACY_COMPLETION,
    ANTHROPIC_MESSAGES_CREATE,
    ANTHROPIC_MESSAGES_STREAM,
]


def attribute_chain(node: ast.AST) -> tuple[str, ...]:
    """Return the dotted attribute chain of *node* as a tuple of identifiers.

    For ``client.chat.completions.create`` returns ``("client", "chat",
    "completions", "create")``. For ``mod.foo()`` (a call result whose
    receiver is itself a call), returns the chain of the call's func — i.e.
    only attribute / name nodes are unwound, calls/subscripts/etc. terminate
    the walk by yielding ``"<expr>"`` as a sentinel.
    """
    parts: list[str] = []
    current: ast.AST | None = node
    while current is not None:
        if isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        elif isinstance(current, ast.Name):
            parts.append(current.id)
            current = None
        elif isinstance(current, ast.Call):
            # e.g. OpenAI().chat.completions.create — the receiver itself is a
            # call; record a sentinel so the chain length stays meaningful but
            # we don't try to encode arguments.
            parts.append("<call>")
            current = None
        else:
            parts.append("<expr>")
            current = None
    parts.reverse()
    return tuple(parts)


def match_call(call: ast.Call) -> KnownCall | None:
    """Return the :class:`KnownCall` whose attribute path is a suffix of *call*'s
    dotted func chain, or ``None`` if no rule matches.

    Suffix matching means ``some_client.chat.completions.create(...)`` matches
    :data:`OPENAI_CHAT` regardless of how ``some_client`` was constructed.
    """
    chain = attribute_chain(call.func)
    if not chain:
        return None
    for rule in KNOWN_SDK_CALLS:
        if _is_suffix(chain, rule.attribute_path):
            return rule
        for alias in rule.aliases:
            if _is_suffix(chain, alias):
                return rule
    return None


def _is_suffix(chain: tuple[str, ...], suffix: tuple[str, ...]) -> bool:
    if len(suffix) > len(chain):
        return False
    return chain[-len(suffix) :] == suffix
