"""Route tests for ``/api/profile-presets``.

Covers the seed-on-first-launch behaviour, the replace-in-full PUT,
and the reset DELETE — the three operations the Manage presets editor
on the Settings page needs.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aitap.config import Settings
from aitap.server.app import create_app
from aitap.server.routes._deps import get_settings


@pytest.fixture()
def settings_in_tmp(tmp_path: Path) -> Settings:
    aitap_dir = tmp_path / ".aitap"
    aitap_dir.mkdir(parents=True, exist_ok=True)
    return Settings(project_root=tmp_path)


@pytest.fixture()
def client(
    settings_in_tmp: Settings,
) -> Iterator[tuple[TestClient, Settings]]:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings_in_tmp
    with TestClient(app) as test_client:
        yield test_client, settings_in_tmp


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------


def test_get_seeds_on_first_launch(
    client: tuple[TestClient, Settings],
) -> None:
    """First GET on a fresh install returns the 11-row starter set."""
    c, settings = client
    path = settings.project_root / ".aitap" / "profile-presets.json"
    assert not path.exists()

    res = c.get("/api/profile-presets")
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body) == 11
    # The seed got persisted so a second GET reads the same file.
    assert path.is_file()


def test_get_returns_user_edits_after_persistence(
    client: tuple[TestClient, Settings],
) -> None:
    """Once the user saves a custom list, GET reads that — no re-seed."""
    c, _ = client
    # Save a one-row custom list.
    res = c.put(
        "/api/profile-presets",
        json={
            "presets": [
                {
                    "name": "Internal gateway",
                    "base_url": "https://gateway.corp/v1",
                    "protocol": "openai-compat",
                    "model_id": "internal-llama",
                }
            ]
        },
    )
    assert res.status_code == 200, res.text

    res = c.get("/api/profile-presets")
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["name"] == "Internal gateway"


def test_response_shape_matches_profile_preset_contract(
    client: tuple[TestClient, Settings],
) -> None:
    """Each row carries exactly the four documented fields — drift here
    means the chip-click pre-fill on the frontend stops mapping."""
    c, _ = client
    res = c.get("/api/profile-presets")
    body = res.json()
    for entry in body:
        assert set(entry.keys()) == {"name", "base_url", "protocol", "model_id"}
        assert entry["protocol"] in {"openai-compat", "anthropic"}


# ---------------------------------------------------------------------------
# PUT
# ---------------------------------------------------------------------------


def test_put_replaces_in_full(client: tuple[TestClient, Settings]) -> None:
    """PUT semantics: the response body is the new on-disk list, period.

    The Manage presets editor sees a single round-trip — its add /
    edit / delete operations happen client-side and Save flushes the
    final list. Make sure the response reflects exactly what we sent
    so the editor's optimistic UI stays honest.
    """
    c, _ = client
    payload = {
        "presets": [
            {
                "name": "Alpha",
                "base_url": "https://a/v1",
                "protocol": "openai-compat",
                "model_id": "alpha-m",
            },
            {
                "name": "Beta",
                "base_url": "https://b",
                "protocol": "anthropic",
                "model_id": "beta-m",
            },
        ]
    }
    res = c.put("/api/profile-presets", json=payload)
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body) == 2
    assert [p["name"] for p in body] == ["Alpha", "Beta"]


def test_put_accepts_empty_list_as_explicit_state(
    client: tuple[TestClient, Settings],
) -> None:
    """An empty list is a valid persisted state — distinct from "reset to seed".

    The Reset button is a separate operation (DELETE). PUTting an
    empty array means the user explicitly cleared every preset and
    expects the chip row to render empty until they add one back.
    """
    c, _ = client
    res = c.put("/api/profile-presets", json={"presets": []})
    assert res.status_code == 200
    assert res.json() == []

    # GET reads the same explicit-empty state.
    res = c.get("/api/profile-presets")
    assert res.json() == []


def test_put_validates_required_fields(
    client: tuple[TestClient, Settings],
) -> None:
    """A preset row missing ``model_id`` 422s rather than persisting half-data."""
    c, _ = client
    res = c.put(
        "/api/profile-presets",
        json={
            "presets": [{"name": "Bad", "base_url": "https://b/v1", "protocol": "openai-compat"}]
        },
    )
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# DELETE (reset to defaults)
# ---------------------------------------------------------------------------


def test_delete_resets_to_seeded_set(client: tuple[TestClient, Settings]) -> None:
    """User clears every preset → DELETE restores the seed."""
    c, _ = client
    # Clear out the list first.
    c.put("/api/profile-presets", json={"presets": []})
    res = c.get("/api/profile-presets")
    assert res.json() == []

    # Now reset.
    res = c.delete("/api/profile-presets")
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body) == 11

    # GET reflects the reset.
    res = c.get("/api/profile-presets")
    assert len(res.json()) == 11


def test_delete_is_idempotent(client: tuple[TestClient, Settings]) -> None:
    """Calling DELETE twice produces the same final list (the seed)."""
    c, _ = client
    first = c.delete("/api/profile-presets").json()
    second = c.delete("/api/profile-presets").json()
    assert first == second
