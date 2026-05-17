/**
 * Legacy mock-backed `api` shim.
 *
 * Pre-Wave-3 the UI exposed a single hand-rolled `api` object from
 * `./client`. Wave 3 swaps in a generated client (see `./client.ts`)
 * for the Inventory / PromptDetail / PipelineDetail pages.
 *
 * The pages owned by `wt/ui-playground` (Playground, History,
 * HistoryLanding) and the orphan Audit page still reference the old
 * `api` shape and will be rewritten by their respective owners. Until
 * that happens, those pages import `api` from *here* so the build
 * stays green and the merge surface on `client.ts` stays minimal.
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

export const api = {
  listPrompts(): Promise<PromptListResponse> {
    return Promise.resolve(mock.listPrompts());
  },
  getPrompt(promptId: string): Promise<PromptDetailResponse> {
    return Promise.resolve(mock.getPrompt(promptId));
  },
  listPipelines(): Promise<PipelineListResponse> {
    return Promise.resolve(mock.listPipelines());
  },
  getPipeline(pipelineId: string): Promise<PipelineDetailResponse> {
    return Promise.resolve(mock.getPipeline(pipelineId));
  },
  createRun(body: RunCreate): Promise<RunResponse> {
    return Promise.resolve(mock.createRun(body));
  },
  getRun(runId: string): Promise<RunDetailResponse> {
    return Promise.resolve(mock.getRun(runId));
  },
  getHistory(promptId: string): Promise<HistoryResponse> {
    return Promise.resolve(mock.getHistory(promptId));
  },
  getSettings(): Promise<SettingsResponse> {
    return Promise.resolve(mock.getSettings());
  },
  triggerScan(body: ScanRequest): Promise<ScanResponse> {
    return Promise.resolve(mock.triggerScan(body));
  },
};
