"""Python source adapter — extract :class:`PromptSite` from a single .py file.

The primary path is :mod:`ast`. If parsing fails (e.g., the file uses
syntax beyond our :data:`MIN_PY_VERSION`, or the file is corrupt) we fall
back to tree-sitter, which is error-tolerant. Tree-sitter findings are
emitted at :class:`Confidence.LOW` — they tell the user "something looks
LLM-ish here, please confirm" without claiming the same fidelity as the AST
path.

Usage::

    sites, warnings = scan_python_file(Path("foo.py"), project_root=Path("."))
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from aitap.scanner.models import (
    CallParameters,
    CodeLocation,
    Confidence,
    Message,
    PromptSite,
    Provider,
    Role,
    ScanWarning,
    TemplateKind,
)
from aitap.scanner.rules import sdk_calls
from aitap.scanner.rules.prompt_extractor import (
    extract_call_parameters,
    extract_messages,
    extract_template,
)
from aitap.scanner.rules.template_definitions import (
    TemplateDefinition,
    detect_builder_function,
    detect_prompt_constant,
)


@dataclass
class _ScanContext:
    project_root: Path
    file_path: Path
    file_relpath: str
    source: str


def scan_python_file(
    file_path: Path, project_root: Path
) -> tuple[list[PromptSite], list[ScanWarning]]:
    """Return all prompt sites detected in *file_path*.

    Never raises for a single malformed file — instead returns one or more
    :class:`ScanWarning` entries.
    """
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return (
            [],
            [
                ScanWarning(
                    code="W003-read-error",
                    message=f"could not read file: {e}",
                    location=CodeLocation(
                        file=_project_relative(file_path, project_root),
                        line_start=1,
                        line_end=1,
                    ),
                )
            ],
        )

    relpath = _project_relative(file_path, project_root)
    ctx = _ScanContext(
        project_root=project_root,
        file_path=file_path,
        file_relpath=relpath,
        source=source,
    )

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError as e:
        sites, ts_warning = _tree_sitter_fallback(ctx)
        warnings: list[ScanWarning] = [
            ScanWarning(
                code="W001-unparseable",
                message=f"ast parse failed: {e.msg}",
                location=CodeLocation(
                    file=relpath,
                    line_start=max(1, e.lineno or 1),
                    line_end=max(1, e.lineno or 1),
                ),
            )
        ]
        if ts_warning is not None:
            warnings.append(ts_warning)
        return sites, warnings

    file_imports = _collect_imports(tree)
    visitor = _PromptSiteVisitor(ctx, file_imports=file_imports)
    visitor.visit(tree)
    return visitor.sites, visitor.warnings


def _collect_imports(tree: ast.Module) -> frozenset[str]:
    """Walk *tree* once and return the set of top-level package names imported.

    Used by the call-site matcher to anchor short-suffix SDK rules: a file
    whose ``messages.create`` call lives without ``import anthropic`` (or any
    transitive ``from anthropic import ...``) is almost certainly not the
    Anthropic SDK and should not be matched.
    """
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module.split(".")[0])
    return frozenset(out)


# ---------------------------------------------------------------------------
# AST visitor
# ---------------------------------------------------------------------------


class _PromptSiteVisitor(ast.NodeVisitor):
    """Walks the AST, records the enclosing function/class context, and emits
    one :class:`PromptSite` for every ``ast.Call`` matched by
    :mod:`aitap.scanner.rules.sdk_calls`."""

    def __init__(self, ctx: _ScanContext, *, file_imports: frozenset[str]) -> None:
        self._ctx = ctx
        self._scope_stack: list[str] = []
        self._file_imports = file_imports
        self.sites: list[PromptSite] = []
        self.warnings: list[ScanWarning] = []

    # NOTE: ast.NodeVisitor's visitor methods are intentionally untyped in the
    # stdlib stubs — the override signatures match.

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Only top-level functions count as template builders — a nested
        # ``def build_xxx_messages`` inside another function is almost
        # certainly a helper to the enclosing call site, which is already
        # surfaced by ``visit_Call``.
        if not self._scope_stack:
            definition = detect_builder_function(node, file_imports=self._file_imports)
            if definition is not None:
                self.sites.append(self._build_site_from_definition(node, definition))
        self._scope_stack.append(node.name)
        self.generic_visit(node)
        self._scope_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if not self._scope_stack:
            definition = detect_builder_function(node, file_imports=self._file_imports)
            if definition is not None:
                self.sites.append(self._build_site_from_definition(node, definition))
        self._scope_stack.append(node.name)
        self.generic_visit(node)
        self._scope_stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope_stack.append(node.name)
        self.generic_visit(node)
        self._scope_stack.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        # Only module-level assignments count as prompt constants — a
        # function-local ``SYSTEM_PROMPT = ...`` is almost always a
        # caller-local scratch variable, not a real template definition.
        if not self._scope_stack:
            definition = detect_prompt_constant(node, file_imports=self._file_imports)
            if definition is not None:
                self.sites.append(self._build_site_from_definition(node, definition))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        rule = sdk_calls.match_call(node, file_imports=self._file_imports)
        if rule is not None:
            site = self._build_site(node, rule)
            if site is not None:
                self.sites.append(site)
        self.generic_visit(node)

    # ---- internal helpers ----------------------------------------------------

    def _build_site(self, call: ast.Call, rule: sdk_calls.KnownCall) -> PromptSite | None:
        messages_kw = _kwarg(call, rule.messages_kw) if rule.messages_kw else None
        prompt_kw = _kwarg(call, rule.prompt_kw) if rule.prompt_kw else None
        system_kw = _kwarg(call, rule.system_kw) if rule.system_kw else None

        if messages_kw is None and prompt_kw is None and system_kw is None:
            # Even known-shape calls need at least one of these to be a real
            # prompt site. A bare `client.messages.create()` with no kwargs
            # is almost certainly some other API on a like-named object.
            return None

        if prompt_kw is not None and messages_kw is None:
            text, kind, variables = extract_template(prompt_kw)
            messages: list[Message] = [
                Message(
                    role=Role.USER,
                    template_text=text,
                    template_kind=kind,
                    variables=variables,
                )
            ]
        else:
            messages = extract_messages(messages_kw, system_node=system_kw)

        params = extract_call_parameters(call)
        location = CodeLocation(
            file=self._ctx.file_relpath,
            line_start=getattr(call, "lineno", 1),
            line_end=getattr(call, "end_lineno", call.lineno) or call.lineno,
            col_start=getattr(call, "col_offset", None),
            col_end=getattr(call, "end_col_offset", None),
        )
        confidence = _confidence_for(call, messages)
        name = self._derive_name(rule)
        site_id = _stable_site_id(
            self._ctx.file_relpath,
            location.line_start,
            location.col_start,
            messages,
        )

        return PromptSite(
            id=site_id,
            name=name,
            provider=rule.provider,
            location=location,
            messages=messages,
            parameters=params,
            confidence=confidence,
            tags=[rule.notes] if rule.notes else [],
        )

    def _derive_name(self, rule: sdk_calls.KnownCall) -> str:
        if self._scope_stack:
            return _slugify(self._scope_stack[-1])
        # Fallback to file stem if we're at module level.
        return _slugify(self._ctx.file_path.stem) + "_call"

    def _build_site_from_definition(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Assign,
        definition: TemplateDefinition,
    ) -> PromptSite:
        """Wrap a :class:`TemplateDefinition` into a :class:`PromptSite`.

        The location points at the ``def`` / ``=`` line so an editor jump
        lands the user on the definition, not the first message line.
        Confidence mirrors the SDK-call path: HIGH if at least one message
        carries a non-UNRESOLVED template, MEDIUM otherwise.
        """
        line_start = getattr(node, "lineno", 1)
        line_end = getattr(node, "end_lineno", line_start) or line_start
        location = CodeLocation(
            file=self._ctx.file_relpath,
            line_start=line_start,
            line_end=line_end,
            col_start=getattr(node, "col_offset", None),
            col_end=getattr(node, "end_col_offset", None),
        )

        resolved_any = any(
            m.template_kind is not TemplateKind.UNRESOLVED for m in definition.messages
        )
        confidence = Confidence.HIGH if resolved_any else Confidence.MEDIUM

        site_id = _stable_site_id(
            self._ctx.file_relpath,
            location.line_start,
            location.col_start,
            definition.messages,
        )

        return PromptSite(
            id=site_id,
            name=_slugify(definition.name),
            provider=definition.provider,
            location=location,
            messages=definition.messages,
            parameters=CallParameters(),
            confidence=confidence,
            tags=list(definition.tags),
        )


def _kwarg(call: ast.Call, name: str | None) -> ast.AST | None:
    if name is None:
        return None
    for kw in call.keywords:
        if kw.arg == name:
            return cast(ast.AST, kw.value)
    return None


def _confidence_for(call: ast.Call, messages: list[Message]) -> Confidence:
    """If we couldn't resolve any message text, downgrade confidence so L2
    knows to revisit. Calls with at least one resolved message keep HIGH —
    the suffix-match in :func:`sdk_calls.match_call` already ensured the call
    *shape* is known."""
    del call  # unused, kept for signature symmetry / future heuristics
    if any(m.template_kind is not TemplateKind.UNRESOLVED for m in messages):
        return Confidence.HIGH
    return Confidence.MEDIUM


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", text).strip("_")
    return cleaned or "anonymous"


def _stable_site_id(relpath: str, line: int, col: int | None, messages: list[Message]) -> str:
    """Hash file/line/col + message texts into a 12-char id.

    Including ``col`` keeps two prompt sites that share a starting line
    distinct (e.g. ``client.messages.create(messages=client.messages.create(...))``,
    or two ``client.chat.completions.create(...)`` calls inlined into a
    list comprehension on the same line).
    """
    fingerprint = "\n".join(m.template_text for m in messages)
    col_part = "?" if col is None else str(col)
    digest = hashlib.sha1(f"{relpath}:{line}:{col_part}:{fingerprint}".encode())
    return digest.hexdigest()[:12]


def _project_relative(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


# ---------------------------------------------------------------------------
# Tree-sitter fallback
# ---------------------------------------------------------------------------


_TS_SDK_PATTERNS: list[tuple[Provider, tuple[str, ...]]] = [
    (Provider.OPENAI, ("chat", "completions", "create")),
    (Provider.OPENAI, ("responses", "create")),
    (Provider.OPENAI, ("completions", "create")),
    (Provider.ANTHROPIC, ("messages", "create")),
    (Provider.ANTHROPIC, ("messages", "stream")),
]


def _tree_sitter_fallback(
    ctx: _ScanContext,
) -> tuple[list[PromptSite], ScanWarning | None]:
    """Use tree-sitter to surface candidate call sites in a file ast couldn't
    parse. Returns LOW-confidence sites with empty message lists — enough to
    show "look here" in the report."""
    try:
        import tree_sitter_python as tsp  # type: ignore[import-untyped]
        from tree_sitter import Language, Parser  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover — declared dep, but be defensive
        return (
            [],
            ScanWarning(
                code="W004-tree-sitter-unavailable",
                message="tree-sitter unavailable; skipped fallback parsing",
                location=CodeLocation(file=ctx.file_relpath, line_start=1, line_end=1),
            ),
        )

    try:
        language, parser = _build_ts_parser(tsp, Language, Parser)
        tree = parser.parse(ctx.source.encode("utf-8", errors="replace"))
    except Exception as e:  # pragma: no cover — defensive against ts errors
        return (
            [],
            ScanWarning(
                code="W005-tree-sitter-error",
                message=f"tree-sitter parse failed: {e}",
                location=CodeLocation(file=ctx.file_relpath, line_start=1, line_end=1),
            ),
        )
    del language  # only the parser is needed beyond this point

    sites: list[PromptSite] = []
    root: Any = tree.root_node
    for node in _iter_call_nodes(root):
        chain = _ts_attribute_chain(node)
        if not chain:
            continue
        match = _match_ts_chain(chain)
        if match is None:
            continue
        provider, _ = match
        start_point = cast("tuple[int, int]", node.start_point)
        end_point = cast("tuple[int, int]", node.end_point)
        line_start: int = int(start_point[0]) + 1
        line_end: int = int(end_point[0]) + 1
        location = CodeLocation(
            file=ctx.file_relpath,
            line_start=line_start,
            line_end=line_end,
            col_start=int(start_point[1]),
            col_end=int(end_point[1]),
        )
        message = Message(
            role=Role.USER,
            template_text="",
            template_kind=TemplateKind.UNRESOLVED,
        )
        site_id = _stable_site_id(ctx.file_relpath, line_start, int(start_point[1]), [message])
        sites.append(
            PromptSite(
                id=site_id,
                name=_slugify(ctx.file_path.stem) + "_fallback",
                provider=provider,
                location=location,
                messages=[message],
                confidence=Confidence.LOW,
                tags=["tree-sitter-fallback"],
            )
        )
    return sites, None


def _build_ts_parser(tsp: Any, language_cls: Any, parser_cls: Any) -> tuple[Any, Any]:
    """Construct a tree-sitter Parser bound to the Python grammar.

    The tree-sitter Python bindings have shifted constructor shapes across
    minor versions:

    * 0.21.x — ``Language(capsule, name)`` two-arg, ``Parser()`` then
      ``parser.set_language(language)``.
    * 0.22.x — ``Language(capsule)`` one-arg, ``Parser(language)`` direct;
      ``set_language`` removed.

    On 0.21.x the bindings *also accept* ``Parser(language)`` at construction
    time but return an unbound parser whose later ``parse()`` call raises
    ``ValueError: Parsing failed`` — so we cannot rely on TypeError as the
    branch signal. Instead we try the 0.21 path first (most likely given our
    pin) and fall back to 0.22 only when ``set_language`` doesn't exist.

    Today's pin in pyproject.toml is ``tree-sitter>=0.22,<0.26`` (0.22 is
    the first release to ship a macOS arm64 wheel). The 0.21 branch is kept
    here as a safety net in case future bumps re-expand the lower bound.
    """
    capsule = tsp.language()
    try:
        language = language_cls(capsule, "python")
    except TypeError:
        # 0.22+ removed the name argument.
        language = language_cls(capsule)
    parser = parser_cls()
    set_language = getattr(parser, "set_language", None)
    if callable(set_language):
        set_language(language)  # 0.21 path
        return language, parser
    return language, parser_cls(language)  # 0.22 path


def _iter_call_nodes(root: Any) -> list[Any]:
    out: list[Any] = []
    stack: list[Any] = [root]
    while stack:
        node = stack.pop()
        node_type: object = getattr(node, "type", None)
        if node_type == "call":
            out.append(node)
        children: Any = getattr(node, "children", None)
        if isinstance(children, list):
            stack.extend(reversed(cast("list[Any]", children)))
    return out


def _ts_attribute_chain(call_node: Any) -> tuple[str, ...]:
    """Reconstruct the dotted attribute chain on a tree-sitter ``call`` node.

    The grammar for ``a.b.c(...)`` looks like::

        (call
          function: (attribute
            object: (attribute object: (identifier "a") attribute: (identifier "b"))
            attribute: (identifier "c")))
    """
    func: Any = _ts_field(call_node, "function")
    if func is None:
        return ()
    parts: list[str] = []
    current: Any = func
    while current is not None:
        node_type: object = getattr(current, "type", None)
        if node_type == "attribute":
            attr = _ts_field(current, "attribute")
            if attr is not None:
                parts.append(_ts_text(attr))
            current = _ts_field(current, "object")
        elif node_type == "identifier":
            parts.append(_ts_text(current))
            current = None
        elif node_type == "call":
            parts.append("<call>")
            current = None
        else:
            parts.append("<expr>")
            current = None
    parts.reverse()
    return tuple(parts)


def _ts_field(node: Any, name: str) -> Any:
    fn = getattr(node, "child_by_field_name", None)
    if fn is None:
        return None
    return fn(name)


def _ts_text(node: Any) -> str:
    text: object = getattr(node, "text", None)
    if isinstance(text, (bytes, bytearray)):
        try:
            return text.decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover
            return "<expr>"
    if isinstance(text, str):
        return text
    return "<expr>"


def _match_ts_chain(chain: tuple[str, ...]) -> tuple[Provider, tuple[str, ...]] | None:
    for provider, suffix in _TS_SDK_PATTERNS:
        if len(suffix) <= len(chain) and chain[-len(suffix) :] == suffix:
            return (provider, suffix)
    return None
