/**
 * API helpers for the multi-provider profile + preset endpoints.
 *
 * The OpenAPI codegen hasn't been re-run with the new ``/api/profiles*``
 * + ``/api/profile-presets`` routes yet — ``wt/profile-cleanup`` ships
 * the regen pass. Until then this file shadows the contract by hand so
 * the Settings page can already speak the new endpoints. The shape
 * matches ``src/aitap/server/routes/__init__.py``:
 *
 * - Profile / ProfileUpsertRequest / ProfileTestResponse — per-row CRUD.
 * - ProfilePreset / ProfilePresetsUpdate — chip-row templates on the
 *   Add Profile form (Decision 4 in docs/profiles-design.md).
 *
 * Security discipline (carried from PR #35):
 *
 * - The raw key never appears on any response we render. ``Profile``
 *   carries only ``key_configured`` + ``key_source`` + ``key_masked``.
 * - We never log mutation responses to ``console.*`` from this file.
 * - The ``api_key`` field on ``ProfileUpsertRequest`` is write-only —
 *   the React component clears it from state the instant a save returns.
 *
 * When the codegen catches up (``pnpm gen:api`` after wt/profile-cleanup
 * regenerates ``openapi.json``), this file's types collapse into
 * ``./generated/models/Profile.ts`` + friends; the wrapper functions
 * stay because the page should not import the generated service
 * classes directly (lets us swap clients without touching the page).
 */

/** Wire-shape for one configured profile (one row on the Settings page). */
export interface Profile {
  id: string;
  label: string;
  base_url: string;
  protocol: "openai-compat" | "anthropic";
  model_id: string;
  notes: string;
  key_configured: boolean;
  /** Profile-id keys live in the keyring or the opt-in fallback file. */
  key_source: "keyring" | "fallback" | "none";
  /** ``"sk-...XXXX"`` preview; ``null`` when no key is set. */
  key_masked: string | null;
}

/** Body for ``POST /api/profiles`` and ``PUT /api/profiles/{id}``. */
export interface ProfileUpsertRequest {
  label: string;
  base_url: string;
  protocol: "openai-compat" | "anthropic";
  model_id: string;
  notes?: string;
  /** Write-only — never echoed on the response. */
  api_key?: string;
  /** Opt-in fallback to ``~/.aitap/secrets.yaml`` when the keyring is down. */
  use_fallback?: boolean;
}

/** Result of ``POST /api/profiles/{id}/test`` — plain-language detail. */
export interface ProfileTestResponse {
  ok: boolean;
  reason: "auth" | "rate_limit" | "network" | "other" | null;
  detail: string | null;
}

/** One chip-row template on the Add Profile form. */
export interface ProfilePreset {
  name: string;
  base_url: string;
  protocol: "openai-compat" | "anthropic";
  model_id: string;
}

/** Body for ``PUT /api/profile-presets`` — replace-in-full. */
export interface ProfilePresetsUpdate {
  presets: ProfilePreset[];
}

// ---------------------------------------------------------------------------
// Fetch helpers
// ---------------------------------------------------------------------------
//
// Hand-rolled fetch wrappers rather than `apiClient.settings.xxx()` so this
// file works before the codegen pass. We mimic the generated client's
// error-on-non-2xx behaviour (ApiError-like throw) so consumers can rely
// on the promise rejecting for HTTP failures — the Settings page is the
// only caller and it already knows how to surface a plain-language error.

class HttpError extends Error {
  status: number;
  detail: string | null;
  constructor(status: number, detail: string | null) {
    super(detail ?? `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
    this.name = "HttpError";
  }
}

async function readDetail(res: Response): Promise<string | null> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    return null;
  } catch {
    return null;
  }
}

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await readDetail(res);
    throw new HttpError(res.status, detail);
  }
  return (await res.json()) as T;
}

// ---------------------------------------------------------------------------
// Profiles
// ---------------------------------------------------------------------------

export async function listProfiles(): Promise<Profile[]> {
  const res = await fetch("/api/profiles");
  return jsonOrThrow<Profile[]>(res);
}

export async function createProfile(
  payload: ProfileUpsertRequest,
): Promise<Profile> {
  const res = await fetch("/api/profiles", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return jsonOrThrow<Profile>(res);
}

export async function updateProfile(
  profileId: string,
  payload: ProfileUpsertRequest,
): Promise<Profile> {
  const res = await fetch(`/api/profiles/${encodeURIComponent(profileId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return jsonOrThrow<Profile>(res);
}

export async function deleteProfile(profileId: string): Promise<Profile> {
  const res = await fetch(`/api/profiles/${encodeURIComponent(profileId)}`, {
    method: "DELETE",
  });
  return jsonOrThrow<Profile>(res);
}

export async function testProfile(
  profileId: string,
): Promise<ProfileTestResponse> {
  const res = await fetch(
    `/api/profiles/${encodeURIComponent(profileId)}/test`,
    { method: "POST" },
  );
  return jsonOrThrow<ProfileTestResponse>(res);
}

/** Body for ``PUT /api/settings/defaults`` — both fields nullable. */
export interface DefaultsUpdate {
  model_profile_id: string | null;
  judge_profile_id: string | null;
}

export async function putDefaults(
  payload: DefaultsUpdate,
): Promise<unknown /* SettingsResponse — we only need the side-effect */> {
  const res = await fetch("/api/settings/defaults", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return jsonOrThrow<unknown>(res);
}

// ---------------------------------------------------------------------------
// Profile presets
// ---------------------------------------------------------------------------

export async function listPresets(): Promise<ProfilePreset[]> {
  const res = await fetch("/api/profile-presets");
  return jsonOrThrow<ProfilePreset[]>(res);
}

export async function replacePresets(
  presets: ProfilePreset[],
): Promise<ProfilePreset[]> {
  const res = await fetch("/api/profile-presets", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ presets } satisfies ProfilePresetsUpdate),
  });
  return jsonOrThrow<ProfilePreset[]>(res);
}

export async function resetPresets(): Promise<ProfilePreset[]> {
  const res = await fetch("/api/profile-presets", { method: "DELETE" });
  return jsonOrThrow<ProfilePreset[]>(res);
}

export { HttpError };
