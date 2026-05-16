"""Prompt-version history DAO + CLI handlers.

This module backs three different callers:

1. The HTTP routes in :mod:`aitap.server.routes.history` — they want
   typed, in-process data (raw lists/ints) that they can feed into the
   contract response models.
2. The HTTP route :mod:`aitap.server.routes.prompts` — its
   ``POST /api/prompts/{id}/versions`` endpoint records a new version.
3. The Typer subcommands ``aitap diff`` / ``aitap rollback`` in
   :mod:`aitap.cli`. These are detected via :func:`importlib.util.find_spec`
   — as soon as this module is importable the CLI activates them, so the
   public ``diff_versions(prompt, v1, v2)`` and
   ``rollback_version(prompt, version, *, skip_confirm)`` signatures are
   load-bearing and must match what ``cli.diff_command`` /
   ``cli.rollback_command`` invoke.

DAO conventions mirror :mod:`aitap.store.db`:

- Functions that take a :class:`sqlite3.Connection` as their first arg
  are *pure DAOs* — they never open a connection, never print anything,
  never enforce a transaction boundary. The caller wraps them in
  :func:`aitap.store.db.transaction` when they need atomicity.
- Functions that don't take a connection are *CLI shims* — they open a
  connection against :class:`aitap.config.Settings`'s ``db_path``, run
  their work, and write to stdout. These are the entry points the
  ``aitap`` CLI calls.

``record_version`` is the only writer; ``perform_rollback`` is a
convenience composition that resolves the target version's payload and
calls ``record_version`` so the new head row points at the old content
with ``parent_version`` set.
"""

from __future__ import annotations

import difflib
import json
import sqlite3
from typing import TYPE_CHECKING, Literal

from aitap.config import Settings
from aitap.scanner.models import CallParameters, Message
from aitap.store import db

if TYPE_CHECKING:
    from collections.abc import Sequence


# ---------------------------------------------------------------------------
# Pure DAO helpers — take a sqlite3.Connection, return data, never print.
# ---------------------------------------------------------------------------


CreatedBy = Literal["human", "iteration"]


def next_version_for(conn: sqlite3.Connection, prompt_id: str) -> int:
    """Return the next monotonic version number for *prompt_id*.

    Starts at 1 for prompts with no existing versions; otherwise returns
    ``max(version) + 1``. Pure read — does not insert.
    """
    cur = conn.execute(
        "SELECT MAX(version) AS v FROM prompt_versions WHERE prompt_id = ?",
        (prompt_id,),
    )
    row = cur.fetchone()
    current = 0 if row is None or row["v"] is None else int(row["v"])
    return current + 1


def record_version(
    conn: sqlite3.Connection,
    prompt_id: str,
    *,
    messages: Sequence[Message],
    parameters: CallParameters,
    note: str | None = None,
    created_by: CreatedBy = "human",
    parent_version: int | None = None,
) -> int:
    """Insert a new ``prompt_versions`` row and return the assigned version.

    The version number is allocated via :func:`next_version_for`. Caller
    is responsible for wrapping this in a transaction when it needs
    "no concurrent writer can race us" semantics — SQLite's default
    locking is enough for the single-process test/dev workflow.

    The prompt template is stored as the JSON serialisation of
    ``list[Message]`` (not a ``{"messages": [...]}`` envelope) so the
    column round-trips ``Message.model_validate_json``-style.
    """
    version = next_version_for(conn, prompt_id)
    template_json = json.dumps([m.model_dump(mode="json") for m in messages])
    parameters_json = parameters.model_dump_json()
    conn.execute(
        """
        INSERT INTO prompt_versions
            (prompt_id, version, template_json, parameters_json, note,
             created_by, parent_version)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            prompt_id,
            version,
            template_json,
            parameters_json,
            note,
            created_by,
            parent_version,
        ),
    )
    return version


def read_versions(conn: sqlite3.Connection, prompt_id: str) -> list[sqlite3.Row]:
    """Return all ``prompt_versions`` rows for *prompt_id*, oldest first.

    The result preserves the raw row shape so callers can decide whether
    to deserialise ``template_json`` or just inspect metadata columns.
    """
    cur = conn.execute(
        """
        SELECT version, note, created_at, created_by, parent_version,
               template_json, parameters_json
        FROM prompt_versions
        WHERE prompt_id = ?
        ORDER BY version
        """,
        (prompt_id,),
    )
    return cur.fetchall()


def read_version(conn: sqlite3.Connection, prompt_id: str, version: int) -> sqlite3.Row | None:
    """Return a single prompt-version row or ``None`` if missing."""
    cur = conn.execute(
        """
        SELECT version, note, created_at, created_by, parent_version,
               template_json, parameters_json
        FROM prompt_versions
        WHERE prompt_id = ? AND version = ?
        """,
        (prompt_id, version),
    )
    return cur.fetchone()


def avg_score_for_version(conn: sqlite3.Connection, prompt_id: str, version: int) -> float | None:
    """Return the mean :class:`scores.score` across runs of (prompt, version).

    Returns ``None`` when no scored runs exist yet — the contract treats
    that as "no data" rather than "zero".
    """
    cur = conn.execute(
        """
        SELECT AVG(s.score) AS avg_score
        FROM scores s
        JOIN runs r ON r.id = s.run_id
        WHERE r.target_kind = 'prompt'
          AND r.target_id = ?
          AND r.target_version = ?
          AND s.score IS NOT NULL
        """,
        (prompt_id, version),
    )
    row = cur.fetchone()
    if row is None or row["avg_score"] is None:
        return None
    return float(row["avg_score"])


# ---------------------------------------------------------------------------
# Compositions — pure functions that call into the DAO helpers above.
# ---------------------------------------------------------------------------


def compute_diff(conn: sqlite3.Connection, prompt_id: str, v1: int, v2: int) -> str:
    """Return a unified-diff string between two stored prompt versions.

    Both versions must already exist; missing versions raise
    :class:`ValueError` so HTTP callers can map to 404 and the CLI can
    print a friendly error. The diff compares the JSON-serialised
    template (messages list) so changes to role/content/kind all surface.
    """
    a = read_version(conn, prompt_id, v1)
    b = read_version(conn, prompt_id, v2)
    if a is None:
        raise ValueError(f"prompt {prompt_id!r} has no version {v1}")
    if b is None:
        raise ValueError(f"prompt {prompt_id!r} has no version {v2}")

    a_text = json.dumps(json.loads(a["template_json"]), indent=2, ensure_ascii=False)
    b_text = json.dumps(json.loads(b["template_json"]), indent=2, ensure_ascii=False)
    diff_lines = difflib.unified_diff(
        a_text.splitlines(keepends=True),
        b_text.splitlines(keepends=True),
        fromfile=f"{prompt_id}@v{v1}",
        tofile=f"{prompt_id}@v{v2}",
        n=3,
    )
    return "".join(diff_lines)


def perform_rollback(
    conn: sqlite3.Connection,
    prompt_id: str,
    target_version: int,
    *,
    created_by: CreatedBy = "human",
    note: str | None = None,
) -> int:
    """Create a new head version whose content matches *target_version*.

    Returns the new version number. Raises :class:`ValueError` when
    *target_version* doesn't exist. ``parent_version`` on the new row is
    set to *target_version* so the lineage is explicit (the head row
    isn't a sibling of v1 — it was *derived from* it).
    """
    src = read_version(conn, prompt_id, target_version)
    if src is None:
        raise ValueError(f"prompt {prompt_id!r} has no version {target_version}")

    messages_payload = json.loads(src["template_json"])
    messages = [Message.model_validate(item) for item in messages_payload]
    parameters = CallParameters.model_validate_json(src["parameters_json"])
    rollback_note = note if note is not None else f"rollback to v{target_version}"
    return record_version(
        conn,
        prompt_id,
        messages=messages,
        parameters=parameters,
        note=rollback_note,
        created_by=created_by,
        parent_version=target_version,
    )


# ---------------------------------------------------------------------------
# CLI shims — what aitap.cli's stub-activation detects via find_spec.
# ---------------------------------------------------------------------------


def _open_conn(settings: Settings | None = None) -> sqlite3.Connection:
    """Open a connection against the user's project DB.

    The CLI shims always use the ambient :class:`Settings` (so
    ``AITAP_PROJECT_ROOT`` and ``.aitap/config.yaml`` keep working).
    Tests that need to point at a different DB construct the settings
    themselves and use the pure-DAO helpers instead.
    """
    s = settings or Settings()
    conn = db.connect(s.db_path)
    db.init_db(conn)
    return conn


def diff_versions(prompt: str, v1: int, v2: int) -> None:
    """Print a unified diff between two versions of *prompt* to stdout.

    *prompt* is matched first by exact ``prompts.id``, then by
    ``prompts.name`` (so users can type the slug they see in
    ``aitap scan``). The function is best-effort — if nothing matches
    or a version is missing, we print a one-line error to stderr and
    return without raising so the CLI command exits 0 (matching the
    stub's prior behaviour).
    """
    # Local imports avoid pulling rich/typer into pure-DAO consumers.
    import sys

    from rich.console import Console
    from rich.syntax import Syntax

    conn = _open_conn()
    try:
        prompt_id = _resolve_prompt_id(conn, prompt)
        if prompt_id is None:
            print(f"aitap diff: no prompt matches {prompt!r}", file=sys.stderr)
            return
        try:
            text = compute_diff(conn, prompt_id, v1, v2)
        except ValueError as exc:
            print(f"aitap diff: {exc}", file=sys.stderr)
            return
    finally:
        conn.close()

    if not text:
        Console().print(f"[dim]No changes between {prompt_id}@v{v1} and v{v2}.[/dim]")
        return
    Console().print(Syntax(text, "diff", theme="ansi_dark", word_wrap=True))


def rollback_version(prompt: str, version: int, *, skip_confirm: bool = False) -> None:
    """Roll *prompt* back to *version* — creates a new head version.

    The new version's content is copied verbatim from the target version
    so the rollback is recorded as a normal forward step (no destructive
    delete). Without ``skip_confirm`` the user is prompted via typer.
    """
    import sys

    import typer
    from rich.console import Console

    conn = _open_conn()
    try:
        prompt_id = _resolve_prompt_id(conn, prompt)
        if prompt_id is None:
            print(f"aitap rollback: no prompt matches {prompt!r}", file=sys.stderr)
            return

        if read_version(conn, prompt_id, version) is None:
            print(
                f"aitap rollback: prompt {prompt_id!r} has no version {version}",
                file=sys.stderr,
            )
            return

        if not skip_confirm:
            confirmed = typer.confirm(
                f"Rollback {prompt_id} to v{version}? "
                "(creates a new head version pointing at v"
                f"{version}'s content)",
                default=False,
            )
            if not confirmed:
                Console().print("[yellow]Aborted.[/yellow]")
                return

        with db.transaction(conn):
            new_version = perform_rollback(conn, prompt_id, version)
    finally:
        conn.close()

    Console().print(
        f"[green]Rolled back[/green] {prompt_id} -> v{new_version} "
        f"(content copied from v{version})."
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _resolve_prompt_id(conn: sqlite3.Connection, prompt: str) -> str | None:
    """Resolve *prompt* (id or name) to a canonical ``prompts.id``.

    PromptSite ids are content hashes — long and unfriendly to type — so
    the CLI lets users supply the human-readable ``name`` too. We try id
    first (cheap PK lookup), then fall back to a name match. When the
    name is ambiguous (multiple prompts share a slug) we return the
    first one ordered by ``first_seen_at`` so behaviour is deterministic;
    callers can disambiguate by passing the full id.
    """
    cur = conn.execute("SELECT id FROM prompts WHERE id = ?", (prompt,))
    row = cur.fetchone()
    if row is not None:
        return str(row["id"])

    cur = conn.execute(
        "SELECT id FROM prompts WHERE name = ? ORDER BY first_seen_at LIMIT 1",
        (prompt,),
    )
    row = cur.fetchone()
    if row is not None:
        return str(row["id"])
    return None
