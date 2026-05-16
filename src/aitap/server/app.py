"""FastAPI application factory + uvicorn launcher.

The ``app`` module-level binding is the canonical ASGI application —
tests import it directly via ``from aitap.server.app import app`` to
exercise routes through :class:`httpx.AsyncClient` without spinning up
uvicorn.

Three concerns live here:

1. :data:`app` — the :class:`FastAPI` instance with every router
   (prompts, pipelines, history, plus future routers contributed by
   sibling worktrees) mounted under ``/api``.
2. :func:`serve` — the small uvicorn wrapper invoked by
   ``aitap.cli.ui_command`` when the user runs ``aitap ui``. The
   playground bundle and SPA hosting is wt/runner's domain; we keep
   this stub minimal so it boots a working API even before runner
   ships the static-files mount.
3. :func:`create_app` — a factory that callers (mostly tests, mostly
   wt/runner's own bootstrap code) can use to construct an isolated
   :class:`FastAPI` instance, e.g., to attach extra routers in tests
   without mutating the module-level singleton.

Future router files (``runs.py``, ``feedback.py``, ``settings.py``)
plug in here via :func:`FastAPI.include_router` — sibling worktrees
will add their own import + include_router pair in this module.
"""

from __future__ import annotations

import webbrowser

from fastapi import FastAPI

from aitap import __version__
from aitap.server.routes.history import router as history_router
from aitap.server.routes.pipelines import router as pipelines_router
from aitap.server.routes.prompts import router as prompts_router


def create_app() -> FastAPI:
    """Build a fresh :class:`FastAPI` instance with all routers attached.

    Used by tests that need a clean app per fixture so dependency
    overrides on one test don't bleed into another. The module-level
    :data:`app` is built by calling this once at import time.
    """
    application = FastAPI(
        title="aitap",
        version=__version__,
        description="Local playground API for prompts, pipelines, runs, and iteration.",
    )
    application.include_router(prompts_router, prefix="/api")
    application.include_router(pipelines_router, prefix="/api")
    application.include_router(history_router, prefix="/api")
    return application


app: FastAPI = create_app()


def serve(*, host: str = "127.0.0.1", port: int = 7860, open_browser: bool = True) -> None:
    """Boot the API via uvicorn on ``host:port``.

    Intentionally minimal: wt/runner owns the richer surface
    (static-file mount for the React bundle, lifecycle hooks, signal
    handling). Calling this is what ``aitap ui`` does — when uvicorn
    is unavailable we surface a clear ImportError rather than failing
    silently.
    """
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover — uvicorn is a hard dep
        raise ImportError(
            "uvicorn is required to run `aitap ui`; install with `pip install aitap[all]`."
        ) from exc

    if open_browser:
        webbrowser.open(f"http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")
