"""Pull prompt strings, message lists and call parameters out of an :mod:`ast`.

Three concerns live here:

1. :func:`extract_template` — turn a single AST node into a ``(text,
   :class:`TemplateKind`, variables)`` triple. Handles literals, f-strings,
   string concatenation (``"a" + "b"``), arithmetic-style multiplication-by-int
   is intentionally not handled (rare in prompts).
2. :func:`extract_messages` — turn the value of the ``messages=`` kwarg into a
   list of :class:`Message`. Recognises the canonical
   ``[{"role": ..., "content": ...}, ...]`` literal shape; anything else
   degrades to a single :class:`TemplateKind.UNRESOLVED` message so downstream
   tools know L2 is needed.
3. :func:`extract_call_parameters` — turn known kwargs (``model``,
   ``temperature``, ``max_tokens``, ``top_p``, ``response_format``) into a
   :class:`CallParameters`. Unknown kwargs land in :attr:`CallParameters.extra`
   stringified so the contract's ``dict[str, str]`` typing holds.

All extractors are pure: no I/O, no parsing, just AST walking. Everything is
defensive — if an AST shape is unexpected we degrade to ``UNRESOLVED`` rather
than raising.
"""

from __future__ import annotations

import ast
import re

from aitap.scanner.models import (
    CallParameters,
    Message,
    Role,
    TemplateKind,
    TemplateVariable,
)

_JINJA_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\}\}")
_JINJA_BLOCK_RE = re.compile(r"\{%\s*[a-zA-Z_]+")
_BRACE_VAR_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_.]*)\}")  # str.format-style

ExtractedTemplate = tuple[str, TemplateKind, list[TemplateVariable]]


def extract_template(node: ast.AST | None) -> ExtractedTemplate:
    """Return ``(template_text, kind, variables)`` for *node*.

    ``node`` may be ``None`` (e.g. when a kwarg is absent); we return an empty
    UNRESOLVED tuple in that case so callers can treat absence and failure
    uniformly.
    """
    if node is None:
        return ("", TemplateKind.UNRESOLVED, [])

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return _classify_literal(node.value)

    if isinstance(node, ast.JoinedStr):
        text, variables = _stringify_fstring(node)
        return (text, TemplateKind.FSTRING, variables)

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _stringify_concat(node)

    # Pattern: "...".format(x=...) — treat as fstring-equivalent template.
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
        and isinstance(node.func.value, ast.Constant)
        and isinstance(node.func.value.value, str)
    ):
        text = node.func.value.value
        variables = [TemplateVariable(name=m) for m in _BRACE_VAR_RE.findall(text)]
        return (text, TemplateKind.FSTRING, variables)

    return ("", TemplateKind.UNRESOLVED, [])


def _classify_literal(text: str) -> ExtractedTemplate:
    """Plain string literal — but it might still be a jinja2 template."""
    if _JINJA_VAR_RE.search(text) or _JINJA_BLOCK_RE.search(text):
        variables = [TemplateVariable(name=m) for m in _JINJA_VAR_RE.findall(text)]
        return (text, TemplateKind.JINJA2, variables)
    return (text, TemplateKind.LITERAL, [])


def _stringify_fstring(node: ast.JoinedStr) -> tuple[str, list[TemplateVariable]]:
    """Reconstruct an f-string into a placeholder-substituted template."""
    pieces: list[str] = []
    variables: list[TemplateVariable] = []
    seen: set[str] = set()
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            pieces.append(value.value)
        elif isinstance(value, ast.FormattedValue):
            name = _format_value_name(value.value)
            pieces.append("{" + name + "}")
            if name not in seen and not name.startswith("<"):
                variables.append(TemplateVariable(name=name))
                seen.add(name)
        else:
            pieces.append("{<expr>}")
    return ("".join(pieces), variables)


def _format_value_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        # foo.bar.baz → "foo.bar.baz"
        parts: list[str] = []
        current: ast.AST = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
            return ".".join(reversed(parts))
    return "<expr>"


def _stringify_concat(node: ast.BinOp) -> ExtractedTemplate:
    """Flatten ``"a" + "b" + var`` into a CONCAT template if at least one
    side is a literal string. If literal-only on both sides we still mark it
    CONCAT (so reviewers see it was assembled, not authored as one block)."""
    parts: list[str] = []
    variables: list[TemplateVariable] = []
    seen: set[str] = set()

    def walk(expr: ast.AST) -> None:
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
            walk(expr.left)
            walk(expr.right)
            return
        text, _, sub_vars = extract_template(expr)
        if text:
            parts.append(text)
        elif isinstance(expr, ast.Name):
            parts.append("{" + expr.id + "}")
            if expr.id not in seen:
                variables.append(TemplateVariable(name=expr.id))
                seen.add(expr.id)
        else:
            parts.append("{<expr>}")
        for v in sub_vars:
            if v.name not in seen:
                variables.append(v)
                seen.add(v.name)

    walk(node)
    if not parts:
        return ("", TemplateKind.UNRESOLVED, [])
    return ("".join(parts), TemplateKind.CONCAT, variables)


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


def extract_messages(
    messages_node: ast.AST | None,
    system_node: ast.AST | None = None,
) -> list[Message]:
    """Return the message list at the ``messages=`` arg.

    Recognised shapes (in order of preference):

    * ``[{"role": "system", "content": "..."}, ...]`` literal list of dict
      literals — the canonical form for both OpenAI and Anthropic.
    * ``"plain string"`` — treated as a single user message (some callers pass
      a bare string when the SDK accepts it, or use a wrapper).
    * Anything else — emit one UNRESOLVED user message with empty text so the
      site is still recorded for L2 to revisit.

    *system_node* (when supplied — Anthropic carries system prompt as its own
    kwarg) is prepended as a system message if it resolves to a string.
    """
    out: list[Message] = []

    if system_node is not None:
        sys_msg = _system_message_from(system_node)
        if sys_msg is not None:
            out.append(sys_msg)

    if messages_node is None and not out:
        return [Message(role=Role.USER, template_text="", template_kind=TemplateKind.UNRESOLVED)]

    if isinstance(messages_node, ast.List):
        for item in messages_node.elts:
            msg = _message_from_dict(item)
            if msg is not None:
                out.append(msg)
            else:
                out.append(
                    Message(
                        role=Role.USER,
                        template_text="",
                        template_kind=TemplateKind.UNRESOLVED,
                    )
                )
        return out or [
            Message(role=Role.USER, template_text="", template_kind=TemplateKind.UNRESOLVED)
        ]

    if isinstance(messages_node, ast.Constant) and isinstance(messages_node.value, str):
        text, kind, variables = _classify_literal(messages_node.value)
        out.append(
            Message(
                role=Role.USER,
                template_text=text,
                template_kind=kind,
                variables=variables,
            )
        )
        return out

    if messages_node is not None and out:
        # We had a system but couldn't read the messages list.
        out.append(Message(role=Role.USER, template_text="", template_kind=TemplateKind.UNRESOLVED))
        return out

    return [Message(role=Role.USER, template_text="", template_kind=TemplateKind.UNRESOLVED)]


def _system_message_from(node: ast.AST) -> Message | None:
    """Build a SYSTEM :class:`Message` from an Anthropic ``system=`` value.

    Anthropic accepts both shapes:

    * ``system="You are concise."`` — a single string.
    * ``system=[{"type": "text", "text": "You are…"}, …]`` — a list of
      content blocks (used when callers want cache_control on parts of
      the system prompt).

    We concatenate text-block contents with ``\\n\\n`` so the extracted
    template still reflects the prompt the user wrote. Non-text blocks
    (image, document) leave a placeholder so reviewers can see them.
    """
    if isinstance(node, ast.List):
        pieces: list[str] = []
        all_variables: list[TemplateVariable] = []
        seen_var_names: set[str] = set()
        any_resolved = False
        for item in node.elts:
            if not isinstance(item, ast.Dict):
                pieces.append("{<expr>}")
                continue
            text_value: ast.AST | None = None
            block_type: str | None = None
            for key, value in zip(item.keys, item.values, strict=False):
                if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                    continue
                if key.value == "type" and isinstance(value, ast.Constant):
                    block_type = str(value.value)
                elif key.value == "text":
                    text_value = value
            if block_type and block_type != "text":
                pieces.append(f"<{block_type}>")
                continue
            text, kind, variables = extract_template(text_value)
            if kind is not TemplateKind.UNRESOLVED:
                any_resolved = True
            pieces.append(text or "{<expr>}")
            for v in variables:
                if v.name not in seen_var_names:
                    all_variables.append(v)
                    seen_var_names.add(v.name)
        if not pieces:
            return None
        joined = "\n\n".join(pieces)
        kind = (
            TemplateKind.LITERAL
            if any_resolved and not all_variables
            else (TemplateKind.FSTRING if all_variables else TemplateKind.UNRESOLVED)
        )
        return Message(
            role=Role.SYSTEM,
            template_text=joined,
            template_kind=kind,
            variables=all_variables,
        )

    text, kind, variables = extract_template(node)
    if not text and kind is TemplateKind.UNRESOLVED:
        return None
    return Message(
        role=Role.SYSTEM,
        template_text=text,
        template_kind=kind,
        variables=variables,
    )


def _message_from_dict(node: ast.AST) -> Message | None:
    if not isinstance(node, ast.Dict):
        return None
    role: Role = Role.USER
    text = ""
    kind = TemplateKind.UNRESOLVED
    variables: list[TemplateVariable] = []
    for key, value in zip(node.keys, node.values, strict=False):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            continue
        if key.value == "role" and isinstance(value, ast.Constant) and isinstance(value.value, str):
            try:
                role = Role(value.value)
            except ValueError:
                role = Role.USER
        elif key.value == "content":
            text, kind, variables = extract_template(value)
    return Message(role=role, template_text=text, template_kind=kind, variables=variables)


# ---------------------------------------------------------------------------
# Call parameters
# ---------------------------------------------------------------------------

_KNOWN_PARAM_KWARGS = {"model", "temperature", "max_tokens", "top_p", "response_format"}
_SKIP_FROM_EXTRA = {"messages", "system", "input", "prompt"}


def extract_call_parameters(call: ast.Call) -> CallParameters:
    """Read OpenAI/Anthropic-style call kwargs into a :class:`CallParameters`.

    Anything we can't statically resolve is rendered as a string in
    :attr:`CallParameters.extra` so the user can still see it in the report.
    """
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    response_format: str | None = None
    extra: dict[str, str] = {}

    for kw in call.keywords:
        if kw.arg is None:  # **kwargs splat
            continue
        if kw.arg == "model":
            model = _as_str(kw.value)
        elif kw.arg == "temperature":
            temperature = _as_float(kw.value)
        elif kw.arg == "max_tokens":
            max_tokens = _as_int(kw.value)
        elif kw.arg == "max_output_tokens":
            # OpenAI's responses API spelling. Map to the canonical
            # max_tokens slot but keep the raw kwarg name in `extra` so a
            # downstream that reconstructs an SDK call can pick the right
            # spelling.
            max_tokens = _as_int(kw.value)
            extra["max_output_tokens"] = _stringify_value(kw.value)
        elif kw.arg == "top_p":
            top_p = _as_float(kw.value)
        elif kw.arg == "response_format":
            response_format = _response_format_str(kw.value)
        elif kw.arg in _SKIP_FROM_EXTRA or kw.arg in _KNOWN_PARAM_KWARGS:
            continue
        else:
            extra[kw.arg] = _stringify_value(kw.value)

    return CallParameters(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=top_p,
        response_format=response_format,
        extra=extra,
    )


def _as_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _as_float(node: ast.AST) -> float | None:
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    ):
        return float(node.value)
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, (int, float))
        and not isinstance(node.operand.value, bool)
    ):
        return -float(node.operand.value)
    return None


def _as_int(node: ast.AST) -> int | None:
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
    ):
        return int(node.value)
    return None


def _response_format_str(node: ast.AST) -> str | None:
    """``response_format`` is a string literal in legacy code or a dict like
    ``{"type": "json_object"}`` / ``{"type": "json_schema", ...}``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values, strict=False):
            if (
                isinstance(key, ast.Constant)
                and key.value == "type"
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                return value.value
    return None


def _stringify_value(node: ast.AST) -> str:
    """Best-effort representation of a kwarg value for the ``extra`` dict."""
    if isinstance(node, ast.Constant):
        return repr(node.value)
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover — ast.unparse is broad in 3.10+
        return "<expr>"
