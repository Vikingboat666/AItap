"""Infer the input shape of a prompt by reading the call site's enclosing
function in source.

We re-use the standard library :mod:`ast` (the same parser the scanner's
Python adapter uses — see :mod:`aitap.scanner.languages.python`) rather
than inventing new AST tooling. The output, :class:`InputShape`, is
grounding for :func:`aitap.dataset.llm_expander.expand`: a hint to the LLM
about what slots the prompt actually expects.

Best-effort throughout: a file that doesn't parse, or a call site that
isn't inside any function, just returns an empty :class:`InputShape`.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING

from aitap.dataset.types import InputShape

if TYPE_CHECKING:
    from aitap.scanner.models import PromptSite


def infer_input_shape(site: PromptSite, project_root: Path) -> InputShape:
    """Return a best-effort :class:`InputShape` for *site*.

    Steps:

    1. Read the file at ``project_root / site.location.file``. Return an
       empty shape if the file is missing or unreadable — we never raise.
    2. Parse it with :mod:`ast`. On :class:`SyntaxError`, return empty.
    3. Find the smallest function/method/class enclosing
       ``site.location.line_start``.
    4. Extract argument names and their annotations, plus the docstring.

    Template variables on the prompt site are *not* automatically
    promoted to ``InputShape.fields`` — they live on
    ``PromptSite.messages[*].variables`` and the LLM expander already
    receives them separately. Keeping the two separate avoids ambiguity
    between "name the prompt uses" and "name the enclosing function takes".
    """
    file_path = (project_root / site.location.file).resolve()
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return InputShape()

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return InputShape()

    enclosing = _find_enclosing(tree, site.location.line_start)
    if enclosing is None:
        return InputShape()

    function_name: str | None = None
    fields: dict[str, str] = {}
    docstring: str | None = None

    if isinstance(enclosing, (ast.FunctionDef, ast.AsyncFunctionDef)):
        function_name = enclosing.name
        fields = _collect_args(enclosing)
        docstring = _first_paragraph(ast.get_docstring(enclosing))
    elif isinstance(enclosing, ast.ClassDef):
        function_name = enclosing.name
        # For class-level call sites, surface __init__ args if available —
        # they're the natural "inputs" the caller would supply.
        init = _find_init(enclosing)
        if init is not None:
            fields = _collect_args(init)
        docstring = _first_paragraph(ast.get_docstring(enclosing))

    return InputShape(
        fields=fields,
        function_name=function_name,
        docstring=docstring,
    )


def _find_enclosing(tree: ast.Module, line: int) -> ast.AST | None:
    """Return the smallest function/class node containing *line*.

    "Smallest" so that nested functions win over the outer module-level
    function. Falls back to ``None`` for module-level call sites.
    """
    best: ast.AST | None = None
    best_span = float("inf")
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        start = node.lineno
        end = getattr(node, "end_lineno", None) or start
        if start <= line <= end:
            span = end - start
            if span < best_span:
                best = node
                best_span = span
    return best


def _collect_args(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, str]:
    """Return ``{arg_name: type_annotation_or_"any"}`` for *func*.

    Skips ``self`` and ``cls`` (they're never user-supplied inputs). Keeps
    positional, positional-or-keyword, and keyword-only args. ``*args`` and
    ``**kwargs`` are surfaced as ``"*args"`` / ``"**kwargs"`` so the LLM
    can still see they exist (rare for prompt-input functions but possible).
    """
    out: dict[str, str] = {}
    args = func.args
    positional = list(args.posonlyargs) + list(args.args)
    for a in positional:
        if a.arg in {"self", "cls"}:
            continue
        out[a.arg] = _annotation_str(a.annotation)
    for a in args.kwonlyargs:
        out[a.arg] = _annotation_str(a.annotation)
    if args.vararg is not None:
        out[f"*{args.vararg.arg}"] = _annotation_str(args.vararg.annotation)
    if args.kwarg is not None:
        out[f"**{args.kwarg.arg}"] = _annotation_str(args.kwarg.annotation)
    return out


def _annotation_str(node: ast.expr | None) -> str:
    """Render an annotation AST as a short type string, defaulting to ``any``.

    We use :func:`ast.unparse` (available on the project's minimum Python
    target, 3.10) so complex annotations like ``list[dict[str, int]]`` come
    out readable. ``None`` (no annotation) becomes ``"any"`` — that's a
    truthful description and lets the expander treat untyped slots as free
    text.
    """
    if node is None:
        return "any"
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover — ast.unparse is very forgiving
        return "any"


def _find_init(cls: ast.ClassDef) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for item in cls.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "__init__":
            return item
    return None


def _first_paragraph(text: str | None) -> str | None:
    """Return the first non-empty paragraph of *text*, or ``None``.

    Docstrings can be long; the LLM expander only needs a hint, and very
    long docstrings would dominate the prompt token budget.
    """
    if not text:
        return None
    stripped = text.strip()
    if not stripped:
        return None
    paragraph, _, _ = stripped.partition("\n\n")
    return paragraph.strip() or None


__all__ = ["infer_input_shape"]
