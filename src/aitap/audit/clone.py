"""Remote-repo audit: clone, scan, report, cleanup.

Audit mode is *read-only* by design:

- We clone into a temp directory (or ``.aitap/audit-cache/<repo>`` when
  ``--keep-clone``) and never write anything to the user's ``.aitap/``.
- L2 is forbidden — even if the user passes ``--rules-only=False``, audit
  refuses to spend money against arbitrary third-party code.
- We always reuse the existing scanner + report renderer; we never
  re-implement scanning here.

URL shorthand:

- ``gh:owner/repo`` → ``https://github.com/owner/repo.git``
- Anything else is passed through to gitpython unchanged (so
  ``https://``, ``git@github.com:...``, ``ssh://...`` all work).
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from aitap.scanner.models import ScanResult


_GH_PREFIX = "gh:"


def resolve_repo_url(repo: str) -> str:
    """Expand the ``gh:`` shorthand; pass everything else through.

    Raises :class:`typer.BadParameter` for obviously malformed inputs so
    the CLI surfaces a friendly error instead of letting gitpython spit a
    cryptic ``GitCommandError`` at the user.
    """
    repo = repo.strip()
    if not repo:
        raise typer.BadParameter("repo argument cannot be empty")

    if repo.startswith(_GH_PREFIX):
        body = repo[len(_GH_PREFIX) :]
        if "/" not in body or body.count("/") != 1 or body.startswith("/") or body.endswith("/"):
            raise typer.BadParameter(f"invalid gh: shorthand {repo!r}; expected 'gh:owner/repo'")
        return f"https://github.com/{body}.git"

    # Pass through; let gitpython handle protocol-specific failures.
    return repo


def audit_repo(
    repo: str,
    *,
    rules_only: bool = True,
    keep_clone: bool = False,
    cache_root: Path | None = None,
) -> int:
    """Clone *repo*, run an L1 scan, render the report, optionally cleanup.

    Returns an exit-code-shaped int (0 = success, 1+ = failure) so callers
    in the CLI can ``raise typer.Exit(code=audit_repo(...))`` if they want.

    *cache_root* is honoured only when ``keep_clone=True`` — that's where
    the persistent clone lives. Defaults to ``./.aitap/audit-cache``.
    """
    if not rules_only:
        # Hard gate: audit must never invoke L2 against unknown code, period.
        # This mirrors the doc in WORKTREES.md ("audit 模式严格拒绝 L2").
        raise typer.BadParameter(
            "audit mode does not support L2 (remove --no-rules-only). "
            "Use `aitap scan --deep` against your own code instead."
        )

    url = resolve_repo_url(repo)

    if keep_clone:
        cache_root = cache_root or (Path.cwd() / ".aitap" / "audit-cache")
        cache_root.mkdir(parents=True, exist_ok=True)
        # Slug the URL to a directory name so re-running against the same
        # repo updates rather than collides.
        slug = _slug_for(url)
        clone_dir = cache_root / slug
        if clone_dir.exists():
            # Don't auto-pull: the audit promise is "I show you what's at
            # this snapshot." If the user wants fresh, they delete and rerun.
            typer.secho(
                f"using existing clone at {clone_dir} (delete it to refetch)",
                fg=typer.colors.YELLOW,
                err=True,
            )
            return _scan_and_report(clone_dir)
        return _clone_and_scan(url, clone_dir, cleanup=False)

    # Default: ephemeral temp dir, removed after report.
    tmp_dir = Path(tempfile.mkdtemp(prefix="aitap-audit-"))
    try:
        return _clone_and_scan(url, tmp_dir, cleanup=False)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _clone_and_scan(url: str, dest: Path, *, cleanup: bool) -> int:
    """Clone *url* into *dest* and run the scan. Returns CLI exit code."""
    # Lazy import: gitpython startup cost is non-trivial and we shouldn't
    # pay it on every `aitap --help`.
    try:
        from git import GitCommandError, Repo
    except ImportError as exc:  # pragma: no cover — gitpython is in runtime deps
        typer.secho(f"error: gitpython not available: {exc}", fg=typer.colors.RED, err=True)
        return 2

    typer.secho(f"cloning {url} → {dest}", fg=typer.colors.CYAN, err=True)
    try:
        Repo.clone_from(url, dest, depth=1, no_single_branch=False)
    except GitCommandError as exc:
        typer.secho(
            f"error: failed to clone {url}: {exc.stderr or exc}", fg=typer.colors.RED, err=True
        )
        if cleanup:
            shutil.rmtree(dest, ignore_errors=True)
        return 1

    return _scan_and_report(dest)


def _scan_and_report(project_root: Path) -> int:
    """Scan *project_root* and render the Markdown report."""
    # Imports are deferred so loading aitap.audit.clone is cheap (cli.py
    # uses find_spec to detect us).
    from aitap.scanner.engine import scan_project
    from aitap.scanner.report import render_terminal_report

    result: ScanResult = scan_project(project_root)
    typer.secho(
        f"scanned {result.files_scanned} files; "
        f"found {len(result.prompts)} prompt(s), {len(result.pipelines)} pipeline(s)",
        fg=typer.colors.GREEN,
        err=True,
    )
    render_terminal_report(result)
    return 0


_UNSAFE_SLUG_CHARS = str.maketrans({c: "-" for c in '/\\:?*"<>|'})


def _slug_for(url: str) -> str:
    """Make *url* into a filesystem-safe directory name.

    Used only for ``--keep-clone`` cache directories; not security-critical
    (the URL is the user's own input) but does need to round-trip on
    Windows where ``:`` and ``\\`` are illegal in filenames.
    """
    # Strip protocol noise so cache dirs read well in `ls`.
    stripped = url
    for prefix in ("https://", "http://", "ssh://", "git@"):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix) :]
            break
    if stripped.endswith(".git"):
        stripped = stripped[: -len(".git")]
    return stripped.translate(_UNSAFE_SLUG_CHARS).strip("-") or "repo"
