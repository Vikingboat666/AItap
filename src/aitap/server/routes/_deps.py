"""Shared FastAPI dependencies for the route modules.

Two things live here:

1. :func:`get_settings` — yields an :class:`aitap.config.Settings`. Tests
   override it via ``app.dependency_overrides[get_settings]`` to point
   the server at a temp project root without monkeypatching env vars.
2. :func:`get_db` — opens a per-request :class:`sqlite3.Connection`
   against the resolved settings and closes it when the request ends.
   The DB is initialised on first connect so the API can run against a
   freshly-``aitap init``'d project even if no scan has populated rows
   yet (the response shapes handle empty result sets cleanly).

This module is intentionally tiny and import-light — every route module
re-imports it, so heavy work (rich, typer, etc.) would balloon the
cold-start cost of the API process.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends

from aitap.config import Settings
from aitap.store import db


def get_settings() -> Settings:
    """Return the active :class:`Settings`.

    Pure factory so test overrides can return a different instance per
    test without having to manipulate environment variables. Tests should
    register an override via ``app.dependency_overrides[get_settings]``
    so :func:`get_db` picks up the temp project root automatically.
    """
    return Settings()


def get_db(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Iterator[sqlite3.Connection]:
    """Yield a request-scoped SQLite connection, closing it on exit.

    The connection is initialised (creating tables when missing) so
    endpoints can assume the schema is present even on a brand-new
    project. Settings come in via :func:`get_settings` so test fixtures
    can swap the project root without touching env vars.
    """
    conn = db.connect(settings.db_path)
    try:
        db.init_db(conn)
        yield conn
    finally:
        conn.close()
