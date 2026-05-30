"""FastAPI bootstrap for ``aitap ui``.

This module is the single entry point for the web playground:

- ``app`` — a fully-assembled :class:`FastAPI` instance ready to be
  served by uvicorn. Importing this module triggers route registration
  so external test harnesses can ``TestClient(app)`` without extra
  ceremony.
- ``serve()`` — the CLI's hook. Spins up uvicorn on a chosen host/port
  and (unless suppressed) pops open the user's browser.

Optional routers (prompts / pipelines / runs / settings / history) live
in separate worktrees and may not be present yet. We probe for them
with :func:`importlib.util.find_spec` and only mount what's there — a
missing router degrades to "the endpoint just isn't served" rather than
crashing the bootstrap. Once those worktrees merge into ``main`` this
file picks them up automatically with no edits.

Static React assets:
    The hatch build copies the Vite ``dist/`` into
    ``src/aitap/server/static/``. If that directory exists at runtime,
    we mount it at ``/`` so visiting the root serves the SPA. If it
    doesn't (developer install, fresh clone, CI), we serve a minimal
    plaintext landing page so the user knows the API is alive.

CORS:
    Local-only dev tooling — we bind to ``127.0.0.1`` by default and the
    Vite dev server runs on a different port, so we install a permissive
    CORS policy. Anyone exposing this to the network should harden the
    policy themselves; the ``serve()`` docstring warns about this.
"""

from __future__ import annotations

import logging
import threading
import webbrowser
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path

import uvicorn
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from aitap import secrets as _secrets


def _make_health_router() -> APIRouter:
    """Always-on liveness probe.

    Defined as a tiny router (not an inline ``@app.get``) so it sits in
    the same registration loop as the optional routers below — keeps the
    bootstrap symmetric and the request-tracing breadcrumb obvious.
    """
    router = APIRouter(prefix="/api", tags=["meta"])

    @router.get("/health")
    def health() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"status": "ok"}

    return router


# Optional router modules contributed by sibling worktrees. Each tuple is
# ``(module path, attribute name)`` — the module's exported router. When a
# module is absent we skip it without raising.
#
# Keeping this as data (not code) means a future worktree can opt in by
# adding one line, and we don't smear `try/except ImportError` blocks
# across the bootstrap. The merge story stays clean: each route worktree
# ships its module, this list grows, no edits to existing route files.
_OPTIONAL_ROUTERS: tuple[tuple[str, str], ...] = (
    ("aitap.server.routes.prompts", "router"),
    ("aitap.server.routes.pipelines", "router"),
    ("aitap.server.routes.runs", "router"),
    ("aitap.server.routes.settings", "router"),
    ("aitap.server.routes.history", "router"),
    ("aitap.server.routes.iterate", "router"),
)


def _attach_optional_routers(app: FastAPI) -> list[str]:
    """Mount every available optional router; return what was mounted.

    The return value is used by :func:`create_app` only for the dev-mode
    landing page (so a curious visitor sees which routes are live);
    callers that don't need it can ignore it.
    """
    attached: list[str] = []
    for module_path, attr_name in _OPTIONAL_ROUTERS:
        if find_spec(module_path) is None:
            continue
        try:
            module = import_module(module_path)
        except Exception:
            # A present-but-broken router shouldn't take down the whole
            # UI; log-and-skip is friendlier than a hard crash during a
            # fresh `aitap ui`. The error will surface when the user
            # actually hits the endpoint (it just won't be mounted now).
            continue
        router = getattr(module, attr_name, None)
        if router is None:
            continue
        app.include_router(router, prefix="/api")
        attached.append(module_path)
    return attached


def _static_dir() -> Path:
    """Resolve the bundled static assets directory.

    The hatch wheel includes ``src/aitap/server/static/`` as an artifact;
    a developer install will only have that path if the user (or our CI)
    ran ``pnpm build`` in ``src/aitap/ui/``. Either way it's the same
    location.
    """
    return Path(__file__).resolve().parent / "static"


def _landing_html(attached: list[str]) -> str:
    """Minimal HTML shown when the React bundle is missing.

    We don't want the bare 404 on ``/`` — it's confusing and looks broken.
    A four-line page that says "yes, the API is up, here's where to go"
    is enough for a developer running ``aitap ui`` without a UI build.
    """
    if attached:
        listed = "<ul>" + "".join(f"<li><code>{m}</code></li>" for m in attached) + "</ul>"
    else:
        listed = "<p><em>No feature routers are mounted yet.</em></p>"
    return (
        "<!doctype html>"
        "<html><head><meta charset='utf-8'><title>aitap</title></head>"
        "<body style='font-family: system-ui, sans-serif; max-width: 720px; "
        "margin: 4rem auto; padding: 0 1rem; color: #1a1a1a;'>"
        "<h1>aitap is running.</h1>"
        "<p>The Vite UI bundle was not found in "
        "<code>src/aitap/server/static/</code>. The HTTP API is still serving "
        "on this port.</p>"
        "<p>Live endpoints:</p>"
        "<ul><li><a href='/api/health'>/api/health</a> (liveness probe)</li></ul>"
        "<p>Optional routers detected on import:</p>"
        f"{listed}"
        "<p>To get the React UI: <code>pnpm --dir src/aitap/ui install && "
        "pnpm --dir src/aitap/ui build</code>, then restart aitap ui.</p>"
        "</body></html>"
    )


def create_app() -> FastAPI:
    """Build a fresh FastAPI app.

    Exposed as a factory (not just the module-level ``app``) so tests can
    spin up an isolated instance without cross-test state leak.
    """
    app = FastAPI(
        title="aitap",
        version="0.1.0",
        description=(
            "Local web playground for the aitap CLI. "
            "Endpoints are stable per CONTRACTS.md (server.routes.__init__)."
        ),
    )

    # Permissive CORS for local dev. ``serve()`` binds loopback by
    # default; anyone re-binding to a routable interface should re-audit.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Install the secret-log filter on the root logger and on uvicorn's
    # access / error loggers. This catches any stray ``sk-...`` /
    # ``Bearer ...`` strings before they reach the console or log file.
    # Idempotent — repeated calls don't double-attach.
    _secrets.install_log_filter(logging.getLogger())
    for log_name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        _secrets.install_log_filter(logging.getLogger(log_name))

    app.include_router(_make_health_router())
    attached = _attach_optional_routers(app)

    static_dir = _static_dir()
    if static_dir.is_dir() and any(static_dir.iterdir()):
        # ``html=True`` makes StaticFiles serve ``index.html`` for the
        # root path (and 404 fallbacks for SPA routes — FastAPI handles
        # the rest via its own routing precedence). API routes registered
        # above take priority because they were added first.
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="ui")
    else:
        # No bundle — serve a tiny landing page. Bound *only* to the
        # exact root path so it doesn't shadow other future routes.
        landing_body = _landing_html(attached)

        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        def _landing() -> HTMLResponse:  # pyright: ignore[reportUnusedFunction]
            return HTMLResponse(content=landing_body)

    return app


# Module-level singleton — uvicorn references this by import path
# (``aitap.server.app:app``) and so do TestClient consumers.
app = create_app()


def _open_browser_when_ready(url: str, delay_seconds: float = 1.0) -> None:
    """Pop the user's default browser at ``url`` once the server is up.

    Why ``threading.Timer`` and not ``uvicorn``'s lifespan hook: we want
    the open-browser side-effect to also work for ``aitap ui`` running
    in the foreground from a shell where users expect a tab to appear,
    but we *don't* want it firing during a uvicorn reload-watch restart.
    A short-delay timer started before ``uvicorn.run`` blocks is the
    simplest mechanism that satisfies both — the timer fires after the
    bind succeeds in practice, and the user can override the delay if
    their machine is slower.
    """
    threading.Timer(delay_seconds, lambda: webbrowser.open(url)).start()


def serve(*, host: str = "127.0.0.1", port: int = 7860, open_browser: bool = True) -> None:
    """Run uvicorn against this module's ``app``.

    Args:
        host: Interface to bind. Default ``127.0.0.1`` keeps the server
            on the loopback so a stray ``aitap ui`` on a laptop doesn't
            expose your prompts to the LAN. Override at your own risk.
        port: TCP port to bind.
        open_browser: When True (default), open the user's browser to
            ``http://{host}:{port}`` shortly after the server starts.
            ``aitap ui --no-browser`` flips this to False (CI, headless
            servers, anyone with a non-interactive workflow).
    """
    if open_browser:
        # Cosmetic: prefer "localhost" in the browser when binding the
        # loopback. The actual bind is still the IP we were given.
        display_host = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
        _open_browser_when_ready(f"http://{display_host}:{port}")

    uvicorn.run(
        "aitap.server.app:app",
        host=host,
        port=port,
        log_level="info",
        reload=False,
    )
