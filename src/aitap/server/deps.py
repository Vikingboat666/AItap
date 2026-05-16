"""Shared FastAPI dependencies for the route modules.

Two responsibilities:

1. Provide a process-wide :class:`~aitap.config.Settings` that route handlers
   inject via ``Annotated[Settings, Depends(get_settings)]``. Tests can
   override this with ``app.dependency_overrides[get_settings] = ...``
   without touching env vars.
2. Provide a connection context manager bound to ``settings.db_path`` that
   initialises the schema on first use. This keeps every route handler from
   re-emitting the same boilerplate ``connect → init_db → close``.

The connection helper is **not** a FastAPI dependency on purpose — sqlite
connections are cheap to open per request and per-handler ``with`` blocks
make it crystal-clear when they close. A request-scoped dependency would
require ``yield``-style teardown that's harder to read at a callsite.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import Depends

from aitap.config import Settings
from aitap.store import db


def get_settings() -> Settings:
    """Default :class:`Settings` factory.

    Reads env / .aitap/config.yaml on every call which is fine for the
    short-lived API process — Pydantic's ``BaseSettings`` is cheap. Tests
    override this with ``app.dependency_overrides[get_settings] = ...``.
    """
    return Settings()


SettingsDep = Annotated[Settings, Depends(get_settings)]
"""Reusable type alias so route signatures stay terse.

Usage::

    def my_endpoint(settings: SettingsDep) -> SomeResponse: ...
"""


@contextmanager
def get_conn(settings: Settings) -> Iterator[sqlite3.Connection]:
    """Yield an initialised sqlite connection scoped to a single request.

    Why a context manager rather than a generator-style ``Depends``?

    - Route handlers benefit from explicit ``with`` blocks — you can see at
      a glance where the connection closes.
    - sqlite connections are not safe to share across threads; opening per
      request avoids the global-singleton hazard.

    The init_db call is idempotent — calling it on every request adds a
    handful of microseconds while keeping the first-run-creates-tables
    behaviour the rest of the codebase relies on.
    """
    conn = db.connect(settings.db_path)
    try:
        db.init_db(conn)
        yield conn
    finally:
        conn.close()


__all__ = ["SettingsDep", "get_conn", "get_settings"]
