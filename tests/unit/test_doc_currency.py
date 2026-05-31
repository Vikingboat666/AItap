"""Mechanical guards against stale-doc drift.

Background: between 2026-05-23 and 2026-06-01 seven PRs (#32-#38)
shipped without anyone updating CHANGELOG.md, and three design docs sat
with stale "Status: approved" headers long after the work was done.
The cleanup commit `7244aea` brought things back in line; this test
module makes sure the drift can't recur.

We enforce two things at the test-gate level (and therefore at CI):

1. **CHANGELOG currency** — every squash-merged PR since the last
   released `v…` tag must be mentioned in CHANGELOG.md's
   ``[Unreleased]`` section. Opt out with ``[no-changelog]`` in the
   merge commit message for trivial PRs (typo fix, doc reflow).
2. **Design-doc Status freshness** — every ``docs/*-design.md`` must
   carry a ``Status:`` line in its first 30 lines, using one of the
   approved keywords (Draft / Approved / Implemented / Partial /
   Superseded). Forces the maintainer to categorise the doc rather
   than letting it drift in eternal-draft limbo.

The CI checkout step in `.github/workflows/*` already uses
``fetch-depth: 0`` so the `git tag` / `git log` calls below see the
full history.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(*args: str) -> str:
    """Run a git command rooted at the repo and return its stdout.

    Forces ``encoding="utf-8"`` so commit messages with em-dashes,
    Chinese characters, or fancy quotes don't trip the system default
    encoding on Windows (cp936 / gbk would refuse them and stdout would
    be silently ``None``). ``errors="replace"`` is a paranoid belt:
    we'd rather get garbled marker characters than an exception inside
    a test that's meant to keep docs honest.
    """
    return subprocess.run(
        ["git", *args],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout


def _extract_unreleased_section(changelog: str) -> str:
    """Return the body of the ``## [Unreleased]`` section, or empty."""
    match = re.search(
        r"^##\s*\[Unreleased\](.*?)(?=^##\s*\[)",
        changelog,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1) if match else ""


def _last_released_tag() -> str | None:
    """The most recent ``v…``-prefixed tag, or None if no release yet."""
    try:
        raw = _git("tag", "--list", "v*", "--sort=-creatordate")
    except subprocess.CalledProcessError:
        return None
    tags = [t for t in raw.strip().splitlines() if t]
    return tags[0] if tags else None


def test_changelog_unreleased_references_every_recent_pr() -> None:
    """Every squash-merged PR since the last released ``v…`` tag is
    expected to be mentioned in CHANGELOG.md's ``[Unreleased]`` section
    by its number (``#NNN``).

    To opt out for a genuinely trivial PR (typo fix, comment cleanup),
    include the literal ``[no-changelog]`` anywhere in the merge commit
    message.

    On a fresh worktree with no tags the test is a no-op — there's no
    baseline to diff against yet.
    """
    last_tag = _last_released_tag()
    if last_tag is None:
        pytest.skip("no released v… tag yet; nothing to compare against")

    # Each commit block is "subject\nbody\n---END---". Subjects of
    # squash-merged PRs end in ``(#NNN)`` per gh's default.
    log = _git(
        "log",
        f"{last_tag}..HEAD",
        "--pretty=%s%n%b%n---END---",
    )

    changelog = (_REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = _extract_unreleased_section(changelog)

    missing: list[tuple[str, str]] = []
    for raw_block in log.split("---END---"):
        block = raw_block.strip()
        if not block:
            continue
        if "[no-changelog]" in block:
            continue
        # First line is the squash-merge subject; PR number is the
        # trailing ``(#NNN)``.
        subject = block.split("\n", 1)[0]
        pr_match = re.search(r"\(#(\d+)\)", subject)
        if pr_match is None:
            continue
        pr_num = pr_match.group(1)
        if f"#{pr_num}" not in unreleased:
            missing.append((pr_num, subject))

    if missing:
        lines = "\n".join(f"  #{n}: {s}" for n, s in missing)
        pytest.fail(
            "CHANGELOG.md [Unreleased] is missing entries for these merged PRs:\n"
            f"{lines}\n\n"
            "Add one line per PR to CHANGELOG.md [Unreleased] under the "
            "appropriate subsection (Added / Changed / Fixed / Quality / etc.), "
            "or include [no-changelog] in the merge commit message if the PR "
            "is truly not worth a CHANGELOG entry (typo fix, internal-only "
            "comment cleanup, etc.).\n\n"
            "Background: CLAUDE.md → 'Documentation currency — non-negotiable'."
        )


# Match "Status" followed (within 150 chars on the same logical line)
# by one of the canonical keywords. The loose proximity rule means
# headers like ``**Status**: Fully implemented in PR #N`` or
# ``Status — Partial (Part A shipped)`` both qualify; what we forbid
# is a doc with no Status line at all or one that doesn't categorise.
_ALLOWED_STATUS_KEYWORDS = re.compile(
    r"\bStatus\b[^\n]{0,150}?\b(Draft|Approved|Implemented|Partial|Superseded)\b",
    re.IGNORECASE,
)


def test_every_design_doc_carries_an_explicit_status_line() -> None:
    """Every ``docs/*-design.md`` must declare its status in the first
    30 lines using one of the canonical keywords. Forces the
    maintainer to pick a category rather than letting a doc drift in
    "Status: approved" limbo months after the work shipped.

    Canonical keywords:

    - **Draft** — work-in-progress; describes a plan, not a shipped feature.
    - **Approved** — signed off + active development by a named worktree.
    - **Implemented** — shipped in PR #N (or list of PRs). Doc is now history.
    - **Partial** — some parts shipped, others still backlog (list which).
    - **Superseded** — replaced by another design doc (link it).
    """
    design_docs = sorted((_REPO_ROOT / "docs").glob("*-design.md"))
    if not design_docs:
        pytest.skip("no design docs in docs/")

    bad: list[str] = []
    for path in design_docs:
        head_lines = path.read_text(encoding="utf-8").splitlines()[:30]
        head = "\n".join(head_lines)
        if not _ALLOWED_STATUS_KEYWORDS.search(head):
            bad.append(path.name)

    if bad:
        pytest.fail(
            "These design docs are missing an explicit Status: line in their "
            f"first 30 lines: {bad}.\n\n"
            "Add one of the canonical status lines near the top of the doc, "
            "e.g.:\n"
            "  Status: Draft (in development by wt/X)\n"
            "  Status: Approved — active development, see Decision log below\n"
            "  Status: Implemented in PR #N (YYYY-MM-DD)\n"
            "  Status: Partial — Part A shipped in PR #N, Part B on the backlog\n"
            "  Status: Superseded by docs/<new-doc>.md\n\n"
            "Background: CLAUDE.md → 'Documentation currency — non-negotiable'."
        )
