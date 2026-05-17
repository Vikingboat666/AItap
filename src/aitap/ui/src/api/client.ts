/**
 * API client wiring for the aitap web UI.
 *
 * The HTTP surface itself lives in `./generated/services/*` (output of
 * `pnpm gen:api` against the FastAPI OpenAPI schema). This file is
 * intentionally small:
 *
 * - `apiClient` — a typed re-export of the generated service classes
 *   plus the shared `OpenAPI` runtime config, so consumers have one
 *   import path for "everything API". Endpoint-specific helpers live in
 *   the generated services (e.g. `apiClient.prompts.listPromptsApiPromptsGet()`).
 * - `queryClient` — the singleton `@tanstack/react-query` client used
 *   by `<QueryClientProvider>` in `main.tsx`. Centralising the defaults
 *   here keeps page-level `useQuery` calls boilerplate-free.
 *
 * Why keep this file thin: a sibling worktree (`wt/ui-playground`)
 * generates the same client and needs to import from here as well. The
 * less surface this file exposes, the smaller the merge conflict
 * window when both branches land.
 *
 * In dev, Vite proxies `/api` to localhost:7860 (see vite.config.ts).
 * In production, the SPA is served from the same FastAPI process, so
 * `/api` is same-origin. Either way `OpenAPI.BASE` stays empty — the
 * generated services prepend `/api/...` themselves.
 */

import { QueryClient } from "@tanstack/react-query";

import { HistoryService } from "./generated/services/HistoryService";
import { MetaService } from "./generated/services/MetaService";
import { PipelinesService } from "./generated/services/PipelinesService";
import { PromptsService } from "./generated/services/PromptsService";
import { RunsService } from "./generated/services/RunsService";
import { SettingsService } from "./generated/services/SettingsService";
import { OpenAPI } from "./generated/core/OpenAPI";

export { ApiError } from "./generated/core/ApiError";

/**
 * Compatibility re-export of the pre-Wave-3 mock-backed `api` shim.
 *
 * Pages owned by `wt/ui-playground` (Playground, History, HistoryLanding)
 * and the orphan Audit page still import `{ api } from "../api/client"`.
 * Re-exporting here keeps their build green without touching files outside
 * this worktree's scope. Once those pages migrate to `apiClient`, both
 * this line and `./legacy-mock-api.ts` can be deleted.
 */
export { api } from "./legacy-mock-api";

/**
 * Grouped re-export of every generated service. Prefer
 * `apiClient.prompts.listPromptsApiPromptsGet()` over importing
 * `PromptsService` directly so the import surface from "../api/client"
 * stays predictable.
 */
export const apiClient = {
  config: OpenAPI,
  prompts: PromptsService,
  pipelines: PipelinesService,
  runs: RunsService,
  history: HistoryService,
  settings: SettingsService,
  meta: MetaService,
} as const;

/**
 * The singleton react-query client. Defaults match what the previous
 * `main.tsx` inlined; keeping them here lets tests reuse the same
 * configuration without diverging.
 *
 * - `staleTime: 30s` — list pages refresh on navigation, not on every
 *   tab focus, so prompt rows don't flash while a user toggles windows.
 * - `retry: 1` — one silent retry catches transient dev-server hiccups
 *   without burying genuine 4xx/5xx errors behind a retry storm.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});
