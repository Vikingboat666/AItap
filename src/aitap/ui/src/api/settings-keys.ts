/**
 * API helpers for the settings/key endpoints.
 *
 * After checkpoint 4 of the secure-settings worktree ran `pnpm gen:api`,
 * the OpenAPI client knows about `ProviderKeyStatus`, `SetKeyRequest`,
 * `TestKeyResponse`, and the three SettingsService methods (set / delete
 * / test). This file re-exports the generated types and wraps the
 * service calls in a small, stable surface that the React components
 * import — so future regens of the client don't churn the page code.
 */

import { SettingsService } from "./generated/services/SettingsService";

export type { ProviderKeyStatus } from "./generated/models/ProviderKeyStatus";
export type { SetKeyRequest } from "./generated/models/SetKeyRequest";
export type { TestKeyResponse } from "./generated/models/TestKeyResponse";

import type { ProviderKeyStatus } from "./generated/models/ProviderKeyStatus";
import type { SetKeyRequest } from "./generated/models/SetKeyRequest";
import type { TestKeyResponse } from "./generated/models/TestKeyResponse";

/** Provider names aitap knows how to store keys for. */
export type ProviderName = ProviderKeyStatus["provider"];

/** Where a configured key currently lives. */
export type KeySource = ProviderKeyStatus["source"];

/** POST /api/settings/key — save a new key. Response is metadata only. */
export function saveProviderKey(
  payload: SetKeyRequest,
): Promise<ProviderKeyStatus> {
  return SettingsService.setProviderKeyApiSettingsKeyPost({
    requestBody: payload,
  });
}

/** DELETE /api/settings/key/{provider} — remove the stored key. */
export function clearProviderKey(
  provider: ProviderName,
): Promise<ProviderKeyStatus> {
  return SettingsService.deleteProviderKeyApiSettingsKeyProviderDelete({
    provider,
  });
}

/** POST /api/settings/test/{provider} — minimal probe call. */
export function testProviderKey(
  provider: ProviderName,
): Promise<TestKeyResponse> {
  return SettingsService.testProviderKeyApiSettingsTestProviderPost({
    provider,
  });
}
