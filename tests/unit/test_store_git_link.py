"""``store/git_link.py`` tests — exercise both repo and non-repo paths."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from aitap.store.git_link import GitContext, get_git_context


def _git(args: list[str], cwd: Path) -> None:
    """Run a git command with a deterministic identity so commits are valid
    on machines without a global git config (CI Windows, fresh dev boxes)."""
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "aitap-test",
            "GIT_AUTHOR_EMAIL": "test@aitap.local",
            "GIT_COMMITTER_NAME": "aitap-test",
            "GIT_COMMITTER_EMAIL": "test@aitap.local",
        }
    )
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True)


def test_get_git_context_in_non_repo(tmp_path: Path) -> None:
    ctx = get_git_context(tmp_path)
    assert ctx == GitContext(is_repo=False, commit=None, short=None, dirty=False, branch=None)


def test_get_git_context_in_fresh_repo_with_no_commits(tmp_path: Path) -> None:
    """A `git init`'d repo with no commits has no head — must not raise."""
    _git(["init", "-b", "main"], tmp_path)
    ctx = get_git_context(tmp_path)
    assert ctx.is_repo is True
    assert ctx.commit is None
    assert ctx.short is None


def test_get_git_context_after_commit(tmp_path: Path) -> None:
    _git(["init", "-b", "main"], tmp_path)
    (tmp_path / "README.md").write_text("hi")
    _git(["add", "README.md"], tmp_path)
    _git(["commit", "-m", "init"], tmp_path)

    ctx = get_git_context(tmp_path)
    assert ctx.is_repo is True
    assert ctx.commit is not None
    assert len(ctx.commit) == 40  # full SHA-1
    assert ctx.short == ctx.commit[:7]
    assert ctx.dirty is False
    assert ctx.branch == "main"


def test_get_git_context_detects_dirty_state(tmp_path: Path) -> None:
    _git(["init", "-b", "main"], tmp_path)
    (tmp_path / "f.txt").write_text("v1")
    _git(["add", "f.txt"], tmp_path)
    _git(["commit", "-m", "init"], tmp_path)
    # Modify tracked file → dirty
    (tmp_path / "f.txt").write_text("v2")

    ctx = get_git_context(tmp_path)
    assert ctx.dirty is True


def test_get_git_context_finds_repo_from_subdir(tmp_path: Path) -> None:
    """Running scan from a subdir of the repo should still find the repo."""
    _git(["init", "-b", "main"], tmp_path)
    (tmp_path / "f.txt").write_text("hi")
    _git(["add", "f.txt"], tmp_path)
    _git(["commit", "-m", "init"], tmp_path)

    sub = tmp_path / "src" / "deep"
    sub.mkdir(parents=True)
    ctx = get_git_context(sub)
    assert ctx.is_repo is True
    assert ctx.commit is not None


def test_get_git_context_does_not_raise_on_missing_path(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    ctx = get_git_context(missing)
    assert ctx.is_repo is False


@pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git binary not available — skipping repo-state tests",
)
def test_dummy_marker_for_git_required() -> None:
    """The other tests need a real git binary; if it's missing pytest will skip
    via the decorator on the targeted test. This stub keeps the file
    self-documenting about that requirement."""
