"""Guard the "only LLM-client construction may import ``secrets.get_key``" rule.

The :func:`aitap.secrets.get_key` function is the **only** way to read a
raw API key out of the vault. To keep the blast radius small, we enforce
that only a tiny allow-list of modules can import it — anything else
must go through :func:`aitap.secrets.key_status` (which never returns the
key) or take the key as a constructor arg.

This test walks every ``.py`` file under ``src/aitap/`` with the stdlib
:mod:`ast` module, finds every reference to ``get_key`` that originates
from ``aitap.secrets``, and asserts the *file* it lives in is on the
allow-list. New call sites must be added here on purpose — the failure
message tells the maintainer exactly which file needs review.

If a future refactor moves the LLM-client factories around, edit the
``_ALLOWED_FILES`` set + write a comment explaining why the new module
is on the trust boundary.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "aitap"

# Files allowed to import :func:`aitap.secrets.get_key`. Each path is
# relative to ``src/aitap/``. Keep this list as small as possible — a
# new entry should come with a comment justifying it.
_ALLOWED_FILES: frozenset[str] = frozenset(
    {
        # The secrets module defines the function; trivially allowed.
        "secrets.py",
        # The LLM client construction sites — they need the raw key to
        # hand it to the SDK constructor. These are the only production
        # surfaces that read the secret out of the vault.
        "deep/anthropic_client.py",
        "deep/openai_client.py",
        # The playground/dispatch layer constructs the client per-run
        # and passes the resolved key into ``get_client``. Treated as a
        # construction-path module by design.
        "playground/dispatch.py",
    }
)


def _iter_source_files() -> list[Path]:
    """Walk ``src/aitap/`` and yield every ``.py`` we care about.

    We deliberately skip the front-end (``ui/``) tree — it's TypeScript,
    not Python, and the pnpm/node_modules subdirectories can blow past
    the Windows 260-character path limit if a walker recurses into them.
    Using :func:`os.walk` with explicit ``dirs[:]`` pruning means we
    never even ``scandir`` the offending subtree.
    """
    out: list[Path] = []
    for root, dirs, files in os.walk(SRC_ROOT):
        # Prune unwanted subtrees in-place — os.walk re-reads ``dirs``
        # for the next descent step.
        if "ui" in dirs and Path(root) == SRC_ROOT:
            dirs.remove("ui")
        # node_modules can appear anywhere; defensive prune.
        if "node_modules" in dirs:
            dirs.remove("node_modules")
        for name in files:
            if name.endswith(".py"):
                out.append(Path(root) / name)
    return out


def _files_importing_get_key() -> set[str]:
    """Return the relative paths (under ``src/aitap/``) that touch ``secrets.get_key``.

    We treat any of these as a "touch":

    - ``from aitap.secrets import get_key`` (any alias)
    - ``from aitap import secrets`` + a later ``secrets.get_key`` attribute
    - ``import aitap.secrets`` + a later ``aitap.secrets.get_key`` attribute

    Anything more obscure (``getattr(secrets_module, "get_" + "key")``)
    is intentionally outside the scope of this test — we trust the code
    reviewer to flag such tricks on the PR.
    """
    offenders: set[str] = set()

    for src in _iter_source_files():
        try:
            tree = ast.parse(src.read_text(encoding="utf-8"))
        except SyntaxError:
            # A file with a syntax error will fail other gates before
            # this one; we don't want to mask the real failure.
            continue

        # Names by which ``aitap.secrets`` is visible inside this file.
        # The default empty set means "module not imported".
        module_aliases: set[str] = set()
        direct_get_key = False

        for node in ast.walk(tree):
            # ``from aitap.secrets import get_key [as alias]``
            if isinstance(node, ast.ImportFrom) and node.module == "aitap.secrets":
                for alias in node.names:
                    if alias.name == "get_key":
                        direct_get_key = True
            # ``from aitap import secrets [as alias]``
            if isinstance(node, ast.ImportFrom) and node.module == "aitap":
                for alias in node.names:
                    if alias.name == "secrets":
                        module_aliases.add(alias.asname or "secrets")
            # ``import aitap.secrets [as alias]``
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "aitap.secrets":
                        module_aliases.add(alias.asname or "aitap.secrets")

        if direct_get_key:
            offenders.add(_relpath(src))
            continue

        # If the module was aliased, look for ``<alias>.get_key`` access.
        if module_aliases:
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "get_key":
                    value = node.value
                    # Resolve the longest dotted prefix on the LHS so we
                    # match ``aitap.secrets.get_key`` even though it's a
                    # two-step Attribute chain.
                    chain: list[str] = []
                    cursor: ast.AST = value
                    while isinstance(cursor, ast.Attribute):
                        chain.insert(0, cursor.attr)
                        cursor = cursor.value
                    if isinstance(cursor, ast.Name):
                        chain.insert(0, cursor.id)
                    dotted = ".".join(chain)
                    if dotted in module_aliases or any(
                        dotted == alias or dotted.startswith(alias + ".")
                        for alias in module_aliases
                    ):
                        offenders.add(_relpath(src))
                        break

    return offenders


def _relpath(path: Path) -> str:
    return path.relative_to(SRC_ROOT).as_posix()


def test_get_key_callers_are_on_the_allow_list() -> None:
    touched = _files_importing_get_key()
    forbidden = touched - _ALLOWED_FILES
    assert not forbidden, (
        "aitap.secrets.get_key was imported by files outside the allow-list "
        f"({sorted(forbidden)}). Either route the call through key_status() "
        "or add the file to _ALLOWED_FILES in this test with a justifying comment."
    )


def test_allow_list_entries_exist() -> None:
    """A stale allow-list is a maintenance liability — fail loudly if a file
    we trust no longer exists, so the next reviewer notices."""
    missing = [rel for rel in _ALLOWED_FILES if not (SRC_ROOT / rel).is_file()]
    assert not missing, (
        f"_ALLOWED_FILES references files that no longer exist: {missing}. "
        "Update the allow-list to match the current layout."
    )
