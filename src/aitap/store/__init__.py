"""Persistence layer for ``.aitap/``.

Exposes one orchestrator (:func:`persist_scan_result`) and re-exports the
DAO + file helpers for downstream worktrees that want finer control.

The orchestrator is intentionally idempotent:

- Re-scanning the same project produces no duplicate rows or YAML files;
  primary keys are PromptSite.id / Pipeline.id which are content hashes.
- Detected providers use INSERT OR IGNORE so ``detected_at`` is preserved.
- Git commit SHA is stamped on every row so a "history" view can be
  reconstructed from the runs table alone.

It is also a no-op when ``.aitap/`` doesn't exist — we never auto-init.
That belongs to ``aitap init`` so the user explicitly opts in to
persistence on a per-project basis.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from aitap.store import db, files, git_link
from aitap.store.git_link import GitContext, get_git_context

if TYPE_CHECKING:
    from aitap.config import Settings
    from aitap.scanner.models import ScanResult

__all__ = [
    "GitContext",
    "PersistReport",
    "db",
    "files",
    "get_git_context",
    "git_link",
    "persist_scan_result",
]


class PersistReport:
    """What :func:`persist_scan_result` actually wrote.

    Used by the CLI to show a friendly summary line ("persisted 5 prompts,
    2 pipelines") and by tests to assert on side effects.
    """

    __slots__ = (
        "db_path",
        "git_context",
        "pipelines_removed",
        "pipelines_written",
        "prompts_removed",
        "prompts_written",
        "providers_recorded",
        "skipped_no_aitap",
    )

    def __init__(
        self,
        *,
        skipped_no_aitap: bool = False,
        prompts_written: int = 0,
        prompts_removed: int = 0,
        pipelines_written: int = 0,
        pipelines_removed: int = 0,
        providers_recorded: int = 0,
        git_context: GitContext | None = None,
        db_path: str | None = None,
    ) -> None:
        self.skipped_no_aitap = skipped_no_aitap
        self.prompts_written = prompts_written
        self.prompts_removed = prompts_removed
        self.pipelines_written = pipelines_written
        self.pipelines_removed = pipelines_removed
        self.providers_recorded = providers_recorded
        self.git_context = git_context
        self.db_path = db_path

    def __repr__(self) -> str:
        if self.skipped_no_aitap:
            return "PersistReport(skipped: no .aitap/ directory)"
        return (
            "PersistReport("
            f"prompts={self.prompts_written} (-{self.prompts_removed} orphans), "
            f"pipelines={self.pipelines_written} (-{self.pipelines_removed} orphans), "
            f"providers={self.providers_recorded})"
        )


def persist_scan_result(settings: Settings, result: ScanResult) -> PersistReport:
    """Persist a :class:`ScanResult` into ``settings.aitap_dir``.

    Returns a :class:`PersistReport` describing what was written.

    No-ops (and reports ``skipped_no_aitap=True``) when the .aitap directory
    doesn't exist — caller should not pre-create it.
    """
    aitap_dir = settings.project_root / settings.aitap_dir
    if not aitap_dir.exists():
        return PersistReport(skipped_no_aitap=True)

    ctx = get_git_context(settings.project_root)

    # SQLite first. If something blows up halfway through writing YAMLs
    # we still want the DB rows recorded — re-running the scan will
    # idempotently re-emit the YAMLs without needing to re-detect.
    conn = db.connect(settings.db_path)
    try:
        db.init_db(conn)
        with db.transaction(conn):
            for site in result.prompts:
                db.upsert_prompt(conn, site, last_commit=ctx.commit)
            for pipeline in result.pipelines:
                db.upsert_pipeline(conn, pipeline, last_commit=ctx.commit)
            for ev in result.providers_detected:
                db.record_provider_evidence(conn, str(settings.project_root), ev)
    finally:
        conn.close()

    # Then YAML artifacts (the part you actually want in git). Each
    # prompt is written under a name+id-suffix path; when the same
    # source-site fingerprint changes (a real-project edit, or a
    # scanner-rule upgrade that resolves text the prior pass missed),
    # the id-suffix part of the filename changes too. Without explicit
    # cleanup the prior write stays on disk forever, doubling up the
    # inventory by name. Track on-disk filenames before writing so we
    # can delete the leftover orphans the same way ``aitap init``
    # didn't have to.
    prompts_dir = settings.prompts_dir
    pipelines_dir = settings.pipelines_dir
    existing_prompt_files = {p.name for p in files.list_prompts(prompts_dir)}
    existing_pipeline_files = {p.name for p in files.list_pipelines(pipelines_dir)}

    written_prompt_files: set[str] = set()
    for site in result.prompts:
        path = files.write_prompt(prompts_dir, site)
        written_prompt_files.add(path.name)

    written_pipeline_files: set[str] = set()
    for pipeline in result.pipelines:
        path = files.write_pipeline(pipelines_dir, pipeline)
        written_pipeline_files.add(path.name)

    prompts_removed = _remove_orphans(prompts_dir, existing_prompt_files, written_prompt_files)
    pipelines_removed = _remove_orphans(
        pipelines_dir, existing_pipeline_files, written_pipeline_files
    )

    return PersistReport(
        prompts_written=len(result.prompts),
        prompts_removed=prompts_removed,
        pipelines_written=len(result.pipelines),
        pipelines_removed=pipelines_removed,
        providers_recorded=len(result.providers_detected),
        git_context=ctx,
        db_path=str(settings.db_path),
    )


def _remove_orphans(
    target_dir: Path,
    existing_files: set[str],
    written_files: set[str],
) -> int:
    """Delete every file in *existing_files* that wasn't re-written this
    pass. Returns the count removed.

    Errors removing a single file are swallowed — leaving one orphan on
    disk is preferable to aborting the persist mid-stream. Persistence
    failures never raise per the contract on
    :func:`persist_scan_result`.
    """
    orphans = existing_files - written_files
    removed = 0
    for name in orphans:
        try:
            (target_dir / name).unlink()
            removed += 1
        except OSError:
            continue
    return removed
