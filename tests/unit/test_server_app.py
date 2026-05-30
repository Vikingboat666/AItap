"""Smoke tests for the FastAPI bootstrap.

Covers:
    * ``GET /api/health`` returns ``{"status": "ok"}`` — the contract
      every consumer (the CLI smoke test, monitoring, the React dev
      server's readiness probe) depends on.
    * ``GET /`` returns a 200 with informative content when no UI bundle
      is present (the dev-install state).
    * ``create_app()`` is a true factory — successive calls return
      independent app instances so a test in one suite can't poison the
      state of another.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from aitap.server.app import app, create_app


def test_health_endpoint_returns_ok() -> None:
    """The single hard contract this module owes the rest of the stack."""
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_landing_page_served_when_no_bundle(tmp_path_factory: object) -> None:
    """When ``src/aitap/server/static/`` is absent we fall back to a
    short HTML landing page instead of a bare 404."""
    fresh_app = create_app()
    client = TestClient(fresh_app)
    response = client.get("/")
    # Either we have a built bundle (response 200, HTML, "aitap" in body)
    # or we fall back to the inline landing page (also 200, HTML, also
    # contains "aitap"). Either way the user sees a non-broken page.
    assert response.status_code == 200
    body = response.text.lower()
    assert "aitap" in body


def test_create_app_returns_fresh_instances() -> None:
    """Independent calls must produce independent FastAPI instances —
    otherwise tests that mount fixture routers on one would bleed into
    the next."""
    a = create_app()
    b = create_app()
    assert a is not b


def test_health_router_does_not_appear_in_docs_unless_meta_tag() -> None:
    """A small sanity check that we registered /api/health under the
    ``meta`` tag rather than letting it land in the default bucket."""
    schema = app.openapi()
    health_path = schema["paths"].get("/api/health")
    assert health_path is not None
    assert "meta" in health_path["get"]["tags"]


def test_spa_fallback_serves_index_html_for_client_routes() -> None:
    """Refreshing on a React Router path must serve the SPA shell, not a
    ``{"detail":"Not Found"}`` JSON.

    React Router owns ``/settings``, ``/playground/...``, ``/pipelines/<id>``,
    ``/history/<id>`` etc. Without the SPA fallback the user gets the
    backend's default 404 JSON when they refresh — which is exactly what
    bit cc-project the first time someone clicked Settings.
    """
    import shutil

    from aitap.server.app import _static_dir

    # If a real built bundle exists we use it; otherwise we synthesise
    # the minimum the test needs (a non-empty static dir + index.html).
    # Either way we restore on the way out so we don't side-effect dev
    # installs that hadn't built the UI yet.
    static = _static_dir()
    pre_existed = static.is_dir() and any(static.iterdir())
    if not pre_existed:
        static.mkdir(parents=True, exist_ok=True)
        (static / "index.html").write_text(
            '<!doctype html><title>aitap · prompt playground</title><div id="root"></div>',
            encoding="utf-8",
        )

    try:
        fresh_app = create_app()
        client = TestClient(fresh_app)

        for spa_path in ("/settings", "/playground", "/pipelines/abc", "/history/p1"):
            response = client.get(spa_path)
            assert response.status_code == 200, f"{spa_path} -> {response.status_code}"
            assert "text/html" in response.headers.get("content-type", "")
            body = response.text.lower()
            # The body is the SPA index.html, not the JSON 404.
            assert "not found" not in body[:200]
            assert '<div id="root"' in body or "aitap" in body

        # /api/* still 404s for unknown endpoints — the fallback must NOT
        # swallow API mistakes into a 200 HTML page.
        api_404 = client.get("/api/this-endpoint-doesnt-exist")
        assert api_404.status_code == 404
        assert api_404.json() == {"detail": "Not Found"}

        # /api/health still works normally.
        api_ok = client.get("/api/health")
        assert api_ok.status_code == 200
        assert api_ok.json() == {"status": "ok"}
    finally:
        if not pre_existed:
            shutil.rmtree(static, ignore_errors=True)
