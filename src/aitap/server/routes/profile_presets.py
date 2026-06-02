"""HTTP routes for ``/api/profile-presets`` — chip templates on the Add Profile form.

Endpoint inventory:

- ``GET /api/profile-presets`` → current preset list. First call on a
  fresh install seeds ``.aitap/profile-presets.json`` with the 11
  documented starter rows (Decision 4 in
  ``docs/profiles-design.md``).
- ``PUT /api/profile-presets`` → replace the whole list. The Manage
  presets editor on the Settings page calls this on Save. Per-row
  add/edit/delete is client-side; the server only persists the final
  shape.
- ``DELETE /api/profile-presets`` → reset to the seeded 11 rows. The
  editor's "Reset to defaults" button drives this. Returns the freshly
  seeded list so the client can refresh without a second GET.

The presets carry no secret material — they are template hints (vendor
name, base_url, protocol, suggested model_id). The user still types a
label + key when they actually create a profile. Persistence is a flat
JSON file under ``.aitap/`` (see :mod:`aitap.profile_presets` for the
storage shape).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from aitap import profile_presets as presets_module
from aitap.config import Settings
from aitap.server.routes import ProfilePreset, ProfilePresetsUpdate
from aitap.server.routes._deps import get_settings

router = APIRouter(tags=["profile-presets"])


@router.get("/profile-presets", response_model=list[ProfilePreset])
def list_profile_presets(
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[ProfilePreset]:
    """Return the current preset list (seeding on first launch)."""
    return presets_module.load_presets(settings)


@router.put("/profile-presets", response_model=list[ProfilePreset])
def replace_profile_presets(
    payload: ProfilePresetsUpdate,
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[ProfilePreset]:
    """Persist *payload.presets* as the whole new list.

    Replace-in-full semantics mirror the editor's "Save" UX: the user
    sees the list, edits rows in place, hits Save once. The empty list
    is a legitimate persisted state — the user explicitly cleared
    every preset and the chip row will render empty. To get the
    seeded set back the user hits Reset (the DELETE endpoint below).
    """
    presets_module.save_presets(settings, payload.presets)
    # Read back rather than echoing the input so a transient I/O error
    # surfaces to the user as "your save didn't stick" rather than a
    # silent success — the client sees the actual on-disk shape.
    return presets_module.load_presets(settings)


@router.delete("/profile-presets", response_model=list[ProfilePreset])
def reset_profile_presets(
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[ProfilePreset]:
    """Restore the seeded starter set, overwriting any user edits.

    Returns the freshly-seeded list so the editor can re-render
    without a second round-trip. Idempotent.
    """
    return presets_module.reset_presets(settings)


__all__ = ["router"]
