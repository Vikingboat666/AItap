"""Guard the "only LLM-client construction may import the raw-key getters" rule.

Two functions return raw secrets out of the vault:

- :func:`aitap.secrets.get_key` — the legacy provider-keyed getter.
- :func:`aitap.secrets.get_key_for_profile` — the new profile-keyed
  getter added in the multi-provider redesign.

Both are restricted to the same small allow-list of LLM-client
construction modules — anything else must go through the metadata
status helpers (which never return the raw key) or take the key as a
constructor arg.

This test walks every ``.py`` file under ``src/aitap/`` with the stdlib
:mod:`ast` module, finds every reference to either getter that
originates from ``aitap.secrets``, and asserts the *file* it lives in
is on the allow-list. New call sites must be added here on purpose —
the failure message tells the maintainer exactly which file needs
review.

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

# Files allowed to import :func:`aitap.secrets.get_key_for_profile`.
# The multi-provider redesign deliberately keeps this list shorter than
# the legacy provider-keyed equivalent: the LLM client classes
# (OpenAICompatClient / AnthropicClient) take the key as a constructor
# argument and do NOT call into ``aitap.secrets`` themselves. Only the
# route layer touches the vault — see ``docs/profiles-design.md``
# §"Backend architecture / LLM client construction".
_ALLOWED_FILES_PROFILE: frozenset[str] = frozenset(
    {
        # Defining module is trivially allowed.
        "secrets.py",
        # The /api/profiles/{id}/test handler resolves the key from the
        # vault and hands it to ``deep.factory.get_client_for_profile``
        # — this is the single place in the request path where the
        # raw key leaves ``aitap.secrets`` for the multi-provider
        # client family. Added in wt/profile-client (PR #40).
        "server/routes/profiles.py",
        # ``aitap scan --deep [--profile <id>]`` resolves the per-profile
        # key inside ``_build_profile_client`` and hands it to
        # ``deep.factory.get_client_for_profile_config``. Added in
        # wt/deep-profile-dispatch (PR #61).
        #
        # The "single seam" contract from PR #40 is *per entry point*:
        # ``server/routes/profiles.py`` is the single seam for the HTTP
        # request path; ``scanner/__init__.py`` is the single seam for
        # the offline CLI path. Each path has exactly one seam — not
        # two unbounded seams across the codebase.
        "scanner/__init__.py",
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


def _files_importing(symbol: str) -> set[str]:
    """Return the relative paths (under ``src/aitap/``) that touch ``secrets.<symbol>``.

    We treat any of these as a "touch":

    - ``from aitap.secrets import <symbol>`` (any alias)
    - ``from aitap import secrets`` + a later ``secrets.<symbol>`` attribute
    - ``import aitap.secrets`` + a later ``aitap.secrets.<symbol>`` attribute

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
        direct_import = False

        for node in ast.walk(tree):
            # ``from aitap.secrets import <symbol> [as alias]``
            if isinstance(node, ast.ImportFrom) and node.module == "aitap.secrets":
                for alias in node.names:
                    if alias.name == symbol:
                        direct_import = True
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

        if direct_import:
            offenders.add(_relpath(src))
            continue

        # If the module was aliased, look for ``<alias>.<symbol>`` access.
        if module_aliases:
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == symbol:
                    value = node.value
                    # Resolve the longest dotted prefix on the LHS so we
                    # match ``aitap.secrets.<symbol>`` even though it's a
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


def _files_importing_get_key() -> set[str]:
    """Backward-compatible name; defers to :func:`_files_importing`."""
    return _files_importing("get_key")


def _relpath(path: Path) -> str:
    return path.relative_to(SRC_ROOT).as_posix()


def test_get_key_callers_are_on_the_allow_list() -> None:
    touched = _files_importing("get_key")
    forbidden = touched - _ALLOWED_FILES
    assert not forbidden, (
        "aitap.secrets.get_key was imported by files outside the allow-list "
        f"({sorted(forbidden)}). Either route the call through key_status() "
        "or add the file to _ALLOWED_FILES in this test with a justifying comment."
    )


def test_get_key_for_profile_callers_are_on_the_allow_list() -> None:
    """Same discipline as :func:`test_get_key_callers_are_on_the_allow_list`,
    applied to the new profile-keyed getter. The allow-list is intentionally
    empty in this worktree — wt/profile-client is the first one to add a
    legitimate caller (the LLM client factory). Any unexpected hit here
    means a downstream worktree is wiring through the secrets module
    earlier than the staged plan permits."""
    touched = _files_importing("get_key_for_profile")
    forbidden = touched - _ALLOWED_FILES_PROFILE
    assert not forbidden, (
        "aitap.secrets.get_key_for_profile was imported by files outside "
        f"the allow-list ({sorted(forbidden)}). The profile-keyed getter "
        "is reserved for the LLM client construction path — route the "
        "call through key_status_for_profile() or add the file to "
        "_ALLOWED_FILES_PROFILE with a justifying comment."
    )


def test_allow_list_entries_exist() -> None:
    """A stale allow-list is a maintenance liability — fail loudly if a file
    we trust no longer exists, so the next reviewer notices."""
    missing = [rel for rel in _ALLOWED_FILES if not (SRC_ROOT / rel).is_file()]
    assert not missing, (
        f"_ALLOWED_FILES references files that no longer exist: {missing}. "
        "Update the allow-list to match the current layout."
    )


def test_profile_allow_list_entries_exist() -> None:
    """Same stale-entry guard for the profile-keyed allow-list."""
    missing = [rel for rel in _ALLOWED_FILES_PROFILE if not (SRC_ROOT / rel).is_file()]
    assert not missing, (
        f"_ALLOWED_FILES_PROFILE references files that no longer exist: {missing}. "
        "Update the allow-list to match the current layout."
    )
