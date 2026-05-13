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
        "pipelines_written",
        "prompts_written",
        "providers_recorded",
        "skipped_no_aitap",
    )

    def __init__(
        self,
        *,
        skipped_no_aitap: bool = False,
        prompts_written: int = 0,
        pipelines_written: int = 0,
        providers_recorded: int = 0,
        git_context: GitContext | None = None,
        db_path: str | None = None,
    ) -> None:
        self.skipped_no_aitap = skipped_no_aitap
        self.prompts_written = prompts_written
        self.pipelines_written = pipelines_written
        self.providers_recorded = providers_recorded
        self.git_context = git_context
        self.db_path = db_path

    def __repr__(self) -> str:
        if self.skipped_no_aitap:
            return "PersistReport(skipped: no .aitap/ directory)"
        return (
            "PersistReport("
            f"prompts={self.prompts_written}, "
            f"pipelines={self.pipelines_written}, "
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

    # Then YAML artifacts (the part you actually want in git).
    for site in result.prompts:
        files.write_prompt(settings.prompts_dir, site)
    for pipeline in result.pipelines:
        files.write_pipeline(settings.pipelines_dir, pipeline)

    return PersistReport(
        prompts_written=len(result.prompts),
        pipelines_written=len(result.pipelines),
        providers_recorded=len(result.providers_detected),
        git_context=ctx,
        db_path=str(settings.db_path),
    )
