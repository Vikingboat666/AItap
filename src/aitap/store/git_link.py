"""Detect git context for a project root.

Used to stamp every scan run with the commit SHA so experiments are
reproducible. Non-fatal when the project isn't a git repo — we just
return ``None`` and the caller stores nulls.

We intentionally use ``gitpython`` rather than shelling out to ``git``:
- More portable across Windows/macOS/Linux without worrying about
  the git binary being on PATH;
- Easier to mock in tests without monkey-patching subprocess;
- ``gitpython`` is already in the runtime deps for ``audit/clone.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitContext:
    """Snapshot of git state at scan time. Frozen so it can flow through
    pydantic models without surprises."""

    is_repo: bool
    commit: str | None  # full SHA when in a repo, else None
    short: str | None  # 7-char abbreviation, else None
    dirty: bool  # True when the working tree has uncommitted changes
    branch: str | None  # current branch name (None on detached HEAD or no repo)


def get_git_context(project_root: Path) -> GitContext:
    """Return a :class:`GitContext` for *project_root*.

    Always returns a context — never raises — so callers can treat it as
    pure data. Errors during git introspection (corrupt repo, permission
    denied, etc.) collapse to ``GitContext(is_repo=False, ...)``.
    """
    # Lazy import: gitpython is moderately heavy and imports on every scan
    # would slow CLI startup. The module is in runtime deps so the import
    # always succeeds — we just defer it.
    try:
        from git import InvalidGitRepositoryError, Repo
        from git.exc import GitError, NoSuchPathError
    except ImportError:
        return _no_repo()

    try:
        repo = Repo(project_root, search_parent_directories=True)
    except (InvalidGitRepositoryError, NoSuchPathError):
        return _no_repo()
    except GitError:
        return _no_repo()

    try:
        commit_sha: str | None = repo.head.commit.hexsha
    except (ValueError, GitError):
        # Fresh repo with no commits yet — head.commit raises ValueError.
        commit_sha = None

    short = commit_sha[:7] if commit_sha else None

    try:
        # On a detached HEAD or in a freshly-init'd repo, .active_branch raises.
        branch: str | None = repo.active_branch.name
    except (TypeError, ValueError):
        branch = None

    try:
        dirty = repo.is_dirty(untracked_files=False)
    except GitError:
        dirty = False

    return GitContext(
        is_repo=True,
        commit=commit_sha,
        short=short,
        dirty=dirty,
        branch=branch,
    )


def _no_repo() -> GitContext:
    return GitContext(is_repo=False, commit=None, short=None, dirty=False, branch=None)
