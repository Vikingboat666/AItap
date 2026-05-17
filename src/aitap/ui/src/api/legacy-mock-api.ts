/**
 * Legacy `api` shim — env-var dispatched between mocks and the real backend.
 *
 * Pre-Wave-3 the UI exposed a single hand-rolled `api` object from
 * `./client`. Wave 3 swaps in a generated client (see `./client.ts`)
 * for the Inventory / PromptDetail / PipelineDetail pages.
 *
 * The pages owned by `wt/ui-playground` (Playground, History,
 * HistoryLanding) and the orphan Audit page still reference the old
 * `api` shape and will be rewritten by their respective owners. Until
 * that happens, those pages import `api` from *here* so the build stays
 * green and the merge surface on `client.ts` stays minimal.
 *
 * **Why env-var dispatch matters**: when `aitap ui` launches the real
 * FastAPI process, `VITE_USE_MOCKS` is unset (or "false") and every
 * page must hit `/api/...`. A mock-only shim would silently feed
 * unmigrated pages canned data while the rest of the app talks to the
 * real backend — that's a subtle but very wrong user experience.
 *
 * The dispatch matches what the pre-Wave-3 `api` object did: import.meta.env
 * is read once at module load so a single page reload picks up flag
 * changes. The truthy values are `"true"` and `"1"`; anything else
 * (including unset) means "hit the real backend".
 *
 * Delete this file once every consumer has migrated to `apiClient`
 * from `./client`.
 */

import * as mock from "./mock";
import type {
  HistoryResponse,
  PipelineDetailResponse,
  PipelineListResponse,
  PromptDetailResponse,
  PromptListResponse,
  RunCreate,
  RunDetailResponse,
  RunResponse,
  ScanRequest,
  ScanResponse,
  SettingsResponse,
} from "./types";

/**
 * `VITE_USE_MOCKS` is the same env flag the generated client respects.
 * `"true"`/`"1"` => mocks, anything else => real fetch. Resolved once
 * at module load so per-call branching is trivial and predictable.
 */
const USE_MOCKS = (() => {
  const raw = import.meta.env?.VITE_USE_MOCKS;
  if (typeof raw !== "string") return false;
  const v = raw.trim().toLowerCase();
  return v === "true" || v === "1";
})();

/**
 * Tiny `fetch` wrapper that mirrors what the pre-Wave-3 `api` shim did:
 * JSON in, JSON out, throw with a meaningful message on non-2xx so
 * `formatError()` can surface it. We deliberately don't reuse the
 * generated `OpenAPI` client here — the goal is to keep the legacy
 * shim self-contained so removing it later is a one-file delete.
 */
async function request<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(path, {
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    // FastAPI errors come back as `{ detail: "..." }`; surface that
    // when present, otherwise fall back to the HTTP status line.
    let detail: string | undefined;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // body wasn't JSON — keep `detail` undefined.
    }
    throw new Error(
      detail ? `${res.status} — ${detail}` : `${res.status} ${res.statusText}`,
    );
  }
  return (await res.json()) as T;
}

export const api = {
  listPrompts(): Promise<PromptListResponse> {
    if (USE_MOCKS) return Promise.resolve(mock.listPrompts());
    return request<PromptListResponse>("/api/prompts");
  },
  getPrompt(promptId: string): Promise<PromptDetailResponse> {
    if (USE_MOCKS) return Promise.resolve(mock.getPrompt(promptId));
    return request<PromptDetailResponse>(
      `/api/prompts/${encodeURIComponent(promptId)}`,
    );
  },
  listPipelines(): Promise<PipelineListResponse> {
    if (USE_MOCKS) return Promise.resolve(mock.listPipelines());
    return request<PipelineListResponse>("/api/pipelines");
  },
  getPipeline(pipelineId: string): Promise<PipelineDetailResponse> {
    if (USE_MOCKS) return Promise.resolve(mock.getPipeline(pipelineId));
    return request<PipelineDetailResponse>(
      `/api/pipelines/${encodeURIComponent(pipelineId)}`,
    );
  },
  createRun(body: RunCreate): Promise<RunResponse> {
    if (USE_MOCKS) return Promise.resolve(mock.createRun(body));
    return request<RunResponse>("/api/runs", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
  getRun(runId: string): Promise<RunDetailResponse> {
    if (USE_MOCKS) return Promise.resolve(mock.getRun(runId));
    return request<RunDetailResponse>(
      `/api/runs/${encodeURIComponent(runId)}`,
    );
  },
  getHistory(promptId: string): Promise<HistoryResponse> {
    if (USE_MOCKS) return Promise.resolve(mock.getHistory(promptId));
    return request<HistoryResponse>(
      `/api/history/${encodeURIComponent(promptId)}`,
    );
  },
  getSettings(): Promise<SettingsResponse> {
    if (USE_MOCKS) return Promise.resolve(mock.getSettings());
    return request<SettingsResponse>("/api/settings");
  },
  triggerScan(body: ScanRequest): Promise<ScanResponse> {
    if (USE_MOCKS) return Promise.resolve(mock.triggerScan(body));
    return request<ScanResponse>("/api/scan", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
};
