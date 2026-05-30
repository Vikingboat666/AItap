/**
 * API helpers for the settings/key endpoints.
 *
 * These types and the small fetch helpers below shadow the generated
 * client (`./generated/services/SettingsService.ts`) until checkpoint
 * 4 of the secure-settings worktree regenerates the OpenAPI bindings.
 * Why keep this file rather than wait for the regen:
 *
 *   1. Routes / Settings page can ship + be tested before the
 *      regeneration finishes (and before the OpenAPI ordering / em-dash
 *      noise gets spread across the diff).
 *   2. Treating the response as a typed object here keeps the Settings
 *      page off `any` even before the generator catches up.
 *
 * After checkpoint 4 runs `pnpm gen:api`, the body of this file should
 * shrink to a re-export of the generated types/service; the page-side
 * import stays the same.
 *
 * Wire-layer: same `/api` prefix as the rest; uses `fetch` with
 * matching headers as the generated client. We don't include the
 * OpenAPI runtime because the surface we hit is tiny.
 */

/** Provider names aitap knows how to store keys for. */
export type ProviderName = "anthropic" | "openai";

/** Where a configured key currently lives. */
export type KeySource = "keyring" | "fallback" | "env" | "none";

/** Per-provider key status as returned by GET / POST / DELETE. */
export interface ProviderKeyStatus {
  provider: ProviderName;
  configured: boolean;
  source: KeySource;
  masked: string | null;
}

/** Body of POST /api/settings/key. The raw key is request-only. */
export interface SetKeyRequest {
  provider: ProviderName;
  key: string;
}

/** Result of POST /api/settings/test/{provider}. */
export interface TestKeyResponse {
  ok: boolean;
  reason: "auth" | "rate_limit" | "network" | "other" | null;
  detail: string | null;
}

/** Minimal HTTP wrapper — surfaces server `detail` as Error.message. */
async function jsonRequest<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const res = await fetch(input, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (typeof body.detail === "string" && body.detail.length > 0) {
        detail = body.detail;
      }
    } catch {
      // Body wasn't JSON; the status text is the best we can do.
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

/** POST /api/settings/key — save a new key. Response is metadata only. */
export function saveProviderKey(
  payload: SetKeyRequest,
): Promise<ProviderKeyStatus> {
  return jsonRequest<ProviderKeyStatus>("/api/settings/key", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** DELETE /api/settings/key/{provider} — remove the stored key. */
export function clearProviderKey(
  provider: ProviderName,
): Promise<ProviderKeyStatus> {
  return jsonRequest<ProviderKeyStatus>(
    `/api/settings/key/${encodeURIComponent(provider)}`,
    { method: "DELETE" },
  );
}

/** POST /api/settings/test/{provider} — minimal probe call. */
export function testProviderKey(
  provider: ProviderName,
): Promise<TestKeyResponse> {
  return jsonRequest<TestKeyResponse>(
    `/api/settings/test/${encodeURIComponent(provider)}`,
    { method: "POST" },
  );
}
