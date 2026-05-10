/**
 * Thin fetch wrapper around the aitap HTTP API.
 *
 * In dev, Vite proxies `/api` to localhost:7860 (see vite.config.ts).
 * In production, the SPA is served from the same FastAPI process, so
 * `/api` is same-origin.
 *
 * While the backend is not implemented (M3), `USE_MOCKS` short-circuits
 * every call into `./mock.ts`. Flip the env var (or import.meta.env) to
 * point at a real server later.
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

const API_BASE = "/api";

const USE_MOCKS =
  (import.meta.env.VITE_USE_MOCKS ?? "true").toLowerCase() !== "false";

class ApiError extends Error {
  constructor(
    public status: number,
    public statusText: string,
    public body: unknown,
  ) {
    super(`${status} ${statusText}`);
    this.name = "ApiError";
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
    ...init,
  });
  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      // ignore
    }
    throw new ApiError(res.status, res.statusText, body);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  listPrompts(): Promise<PromptListResponse> {
    if (USE_MOCKS) return Promise.resolve(mock.listPrompts());
    return request("/prompts");
  },
  getPrompt(promptId: string): Promise<PromptDetailResponse> {
    if (USE_MOCKS) return Promise.resolve(mock.getPrompt(promptId));
    return request(`/prompts/${encodeURIComponent(promptId)}`);
  },
  listPipelines(): Promise<PipelineListResponse> {
    if (USE_MOCKS) return Promise.resolve(mock.listPipelines());
    return request("/pipelines");
  },
  getPipeline(pipelineId: string): Promise<PipelineDetailResponse> {
    if (USE_MOCKS) return Promise.resolve(mock.getPipeline(pipelineId));
    return request(`/pipelines/${encodeURIComponent(pipelineId)}`);
  },
  createRun(body: RunCreate): Promise<RunResponse> {
    if (USE_MOCKS) return Promise.resolve(mock.createRun(body));
    return request("/runs", { method: "POST", body: JSON.stringify(body) });
  },
  getRun(runId: string): Promise<RunDetailResponse> {
    if (USE_MOCKS) return Promise.resolve(mock.getRun(runId));
    return request(`/runs/${encodeURIComponent(runId)}`);
  },
  getHistory(promptId: string): Promise<HistoryResponse> {
    if (USE_MOCKS) return Promise.resolve(mock.getHistory(promptId));
    return request(`/history/${encodeURIComponent(promptId)}`);
  },
  getSettings(): Promise<SettingsResponse> {
    if (USE_MOCKS) return Promise.resolve(mock.getSettings());
    return request("/settings");
  },
  triggerScan(body: ScanRequest): Promise<ScanResponse> {
    if (USE_MOCKS) return Promise.resolve(mock.triggerScan(body));
    return request("/scan", { method: "POST", body: JSON.stringify(body) });
  },
};

export { ApiError, USE_MOCKS };
