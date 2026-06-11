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
        "pipelines_removed_from_db",
        "pipelines_written",
        "prompts_removed",
        "prompts_removed_from_db",
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
        prompts_removed_from_db: int = 0,
        pipelines_written: int = 0,
        pipelines_removed: int = 0,
        pipelines_removed_from_db: int = 0,
        providers_recorded: int = 0,
        git_context: GitContext | None = None,
        db_path: str | None = None,
    ) -> None:
        self.skipped_no_aitap = skipped_no_aitap
        self.prompts_written = prompts_written
        self.prompts_removed = prompts_removed
        self.prompts_removed_from_db = prompts_removed_from_db
        self.pipelines_written = pipelines_written
        self.pipelines_removed = pipelines_removed
        self.pipelines_removed_from_db = pipelines_removed_from_db
        self.providers_recorded = providers_recorded
        self.git_context = git_context
        self.db_path = db_path

    def __repr__(self) -> str:
        if self.skipped_no_aitap:
            return "PersistReport(skipped: no .aitap/ directory)"
        # ``prompts_removed`` and ``prompts_removed_from_db`` track the
        # YAML directory cleanup vs the sqlite row cleanup separately.
        # They are *normally* equal in healthy state (every persisted
        # prompt has both a YAML file and a DB row), but the bug PR #60
        # fixes was that PR #54 only cleaned the YAML half — the DB
        # half stayed and the server kept serving stale rows. Reporting
        # them separately keeps the asymmetry honest while a normal
        # run shows the same number twice.
        return (
            "PersistReport("
            f"prompts={self.prompts_written} "
            f"(-{self.prompts_removed} yaml, -{self.prompts_removed_from_db} db orphans), "
            f"pipelines={self.pipelines_written} "
            f"(-{self.pipelines_removed} yaml, -{self.pipelines_removed_from_db} db orphans), "
            f"providers={self.providers_recorded})"
        )


def persist_scan_result(settings: Settings, result: ScanResult) -> PersistReport:
    """Persist a :class:`ScanResult` into ``settings.aitap_dir``.

    Returns a :class:`PersistReport` describing what was written.

    No-ops (and reports ``skipped_no_aitap=True``) when the .aitap directory
    doesn't exist — caller should not pre-create it.

    Atomicity boundary
    ------------------

    The function runs in two halves that are intentionally NOT in one
    transaction together:

    1. Sqlite ``upsert`` + ``DELETE orphans`` — wrapped in a single
       transaction (line below). Either all writes/deletes commit, or
       none do — including the cascaded ``prompt_versions`` /
       ``iterations`` deletes.
    2. YAML writes + orphan unlinks — best-effort filesystem ops
       that run **after** the DB transaction commits.

    If a YAML unlink fails after the DB commit (disk full, permissions),
    the inventory is still internally consistent because the server's
    source of truth is sqlite (``read_prompts`` in
    ``server/routes/prompts.py``). Disk has a stray YAML; the user-
    facing surface is correct. The alternative — rolling back the DB
    delete because a filesystem op failed — would put the server in a
    *worse* state (stale rows surface in the API while the YAML side
    appears trimmed). So DB-leads is the deliberate choice.
    """
    aitap_dir = settings.project_root / settings.aitap_dir
    if not aitap_dir.exists():
        return PersistReport(skipped_no_aitap=True)

    ctx = get_git_context(settings.project_root)

    # SQLite first. If something blows up halfway through writing YAMLs
    # we still want the DB rows recorded — re-running the scan will
    # idempotently re-emit the YAMLs without needing to re-detect.
    #
    # Orphan cleanup, both prompts and pipelines: snapshot the existing
    # ids BEFORE the upsert pass, then DELETE rows whose id isn't in the
    # scan result. PR #54 cleaned the YAML half of this; the DB half was
    # left and the server (which reads from sqlite, not from disk) kept
    # serving stale rows forever. cc-project hit this in the live eval:
    # deep scan rewrote 5 prompts with new fingerprints, the old YAMLs
    # got cleaned up, but the old DB rows stayed and the user still saw
    # 49 prompts in the Inventory (5 of them stale) when the disk only
    # had 44 YAMLs.
    #
    # The ``ON DELETE CASCADE`` foreign keys in ``prompt_versions`` and
    # ``iterations`` mean version history and in-flight iteration
    # sessions for orphan prompts disappear with their parent row. That
    # is the same fate the YAML-only cleanup already imposed (the YAML
    # was the user-visible artifact); we are merely catching the DB up.
    prompts_removed_from_db = 0
    pipelines_removed_from_db = 0
    conn = db.connect(settings.db_path)
    try:
        db.init_db(conn)
        with db.transaction(conn):
            existing_prompt_ids = db.read_prompt_ids(conn)
            existing_pipeline_ids = db.read_pipeline_ids(conn)

            for site in result.prompts:
                db.upsert_prompt(conn, site, last_commit=ctx.commit)
            for pipeline in result.pipelines:
                db.upsert_pipeline(conn, pipeline, last_commit=ctx.commit)
            for ev in result.providers_detected:
                db.record_provider_evidence(conn, str(settings.project_root), ev)

            written_prompt_ids = {site.id for site in result.prompts}
            written_pipeline_ids = {pipeline.id for pipeline in result.pipelines}
            prompt_orphan_ids = existing_prompt_ids - written_prompt_ids
            pipeline_orphan_ids = existing_pipeline_ids - written_pipeline_ids
            prompts_removed_from_db = db.delete_prompts_by_ids(conn, prompt_orphan_ids)
            pipelines_removed_from_db = db.delete_pipelines_by_ids(conn, pipeline_orphan_ids)
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
        prompts_removed_from_db=prompts_removed_from_db,
        pipelines_written=len(result.pipelines),
        pipelines_removed=pipelines_removed,
        pipelines_removed_from_db=pipelines_removed_from_db,
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
