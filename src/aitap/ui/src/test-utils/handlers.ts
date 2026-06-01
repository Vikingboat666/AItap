/**
 * Default MSW handlers used by the component test suite.
 *
 * These mirror the *shape* of what the FastAPI backend returns for
 * the four endpoints the Inventory / PromptDetail / PipelineDetail
 * pages exercise. They are intentionally tiny — just enough to drive
 * the success-path assertions. Individual tests override entries via
 * `server.use(http.get(..., () => HttpResponse.error()))` to drive
 * the error / retry codepaths.
 *
 * Keeping the fixtures here (rather than reusing `../api/mock.ts`)
 * lets tests reason about specific names/ids without depending on
 * whatever the mock dataset happens to contain at any given Wave.
 */
import { http, HttpResponse } from "msw";

import type { IterateSessionResponse } from "../api/generated/models/IterateSessionResponse";
import type { IterationView } from "../api/generated/models/IterationView";
import type { PipelineDetailResponse } from "../api/generated/models/PipelineDetailResponse";
import type { PipelineListResponse } from "../api/generated/models/PipelineListResponse";
import type { PromptDetailResponse } from "../api/generated/models/PromptDetailResponse";
import type { PromptListResponse } from "../api/generated/models/PromptListResponse";
import type { RunDetailResponse } from "../api/generated/models/RunDetailResponse";
import type { SettingsResponse } from "../api/generated/models/SettingsResponse";

export const promptListFixture: PromptListResponse = {
  prompts: [
    {
      id: "p_test_alpha",
      name: "alpha_prompt",
      provider: "openai",
      file: "app/alpha.py",
      line_start: 10,
      purpose: "alpha purpose",
      confidence: "high",
      latest_version: 2,
    },
    {
      id: "p_test_beta",
      name: "beta_prompt",
      provider: "anthropic",
      file: "app/beta.py",
      line_start: 22,
      purpose: null,
      confidence: "medium",
      latest_version: 1,
    },
  ],
};

export const pipelineListFixture: PipelineListResponse = {
  pipelines: [
    {
      id: "pl_test_one",
      name: "test pipeline one",
      node_count: 2,
      edge_count: 1,
      entry_count: 1,
      exit_count: 1,
    },
  ],
};

export const promptDetailFixture: PromptDetailResponse = {
  site: {
    id: "p_test_alpha",
    name: "alpha_prompt",
    provider: "openai",
    location: {
      file: "app/alpha.py",
      line_start: 10,
      line_end: 20,
    },
    messages: [
      {
        role: "system",
        template_text: "You are a helpful assistant.",
        template_kind: "literal",
      },
      {
        role: "user",
        template_text: "Hello {name}",
        template_kind: "fstring",
        variables: [{ name: "name" }],
      },
    ],
    parameters: { model: "gpt-4o-mini", temperature: 0.2 },
    purpose: "alpha purpose",
    confidence: "high",
  },
  versions: [
    {
      version: 1,
      note: "initial",
      created_at: "2026-05-01T10:00:00Z",
      created_by: "human",
      parent_version: null,
    },
    {
      version: 2,
      note: "tightened wording",
      created_at: "2026-05-02T11:00:00Z",
      created_by: "iteration",
      parent_version: 1,
    },
  ],
};

export const pipelineDetailFixture: PipelineDetailResponse = {
  pipeline: {
    id: "pl_test_one",
    name: "test pipeline one",
    nodes: [
      { prompt_id: "p_test_alpha", label: "alpha" },
      { prompt_id: "p_test_beta", label: "beta" },
    ],
    edges: [
      {
        source: "p_test_alpha",
        target: "p_test_beta",
        kind: "variable",
        via: "payload",
      },
    ],
    entry_points: ["p_test_alpha"],
    exit_points: ["p_test_beta"],
  },
  site_index: {
    p_test_alpha: {
      id: "p_test_alpha",
      name: "alpha_prompt",
      provider: "openai",
      file: "app/alpha.py",
      line_start: 10,
      purpose: "alpha purpose",
      confidence: "high",
      latest_version: 2,
    },
    p_test_beta: {
      id: "p_test_beta",
      name: "beta_prompt",
      provider: "anthropic",
      file: "app/beta.py",
      line_start: 22,
      purpose: null,
      confidence: "medium",
      latest_version: 1,
    },
  },
};

/**
 * A three-node pipeline whose edges form two disconnected fragments:
 *   alpha -> beta     (one fragment)
 *   gamma             (an island — no edges)
 * Used by the segment-mode connectivity tests: selecting {alpha, beta}
 * is contiguous (one component); selecting {alpha, gamma} is not (two
 * components) → non-blocking "not connected" warning.
 */
export const pipelineDisconnectedFixture: PipelineDetailResponse = {
  pipeline: {
    id: "pl_test_split",
    name: "split pipeline",
    nodes: [
      { prompt_id: "p_test_alpha", label: "alpha" },
      { prompt_id: "p_test_beta", label: "beta" },
      { prompt_id: "p_test_gamma", label: "gamma" },
    ],
    edges: [
      {
        source: "p_test_alpha",
        target: "p_test_beta",
        kind: "variable",
        via: "payload",
      },
    ],
    entry_points: ["p_test_alpha", "p_test_gamma"],
    exit_points: ["p_test_beta", "p_test_gamma"],
  },
  site_index: {
    p_test_alpha: {
      id: "p_test_alpha",
      name: "alpha_prompt",
      provider: "openai",
      file: "app/alpha.py",
      line_start: 10,
      purpose: "alpha purpose",
      confidence: "high",
      latest_version: 2,
    },
    p_test_beta: {
      id: "p_test_beta",
      name: "beta_prompt",
      provider: "anthropic",
      file: "app/beta.py",
      line_start: 22,
      purpose: null,
      confidence: "medium",
      latest_version: 1,
    },
    p_test_gamma: {
      id: "p_test_gamma",
      name: "gamma_prompt",
      provider: "openai",
      file: "app/gamma.py",
      line_start: 5,
      purpose: null,
      confidence: "high",
      latest_version: 1,
    },
  },
};

export const settingsFixture: SettingsResponse & { keys: unknown[] } = {
  cost_per_run_usd: 0.01,
  cost_per_session_usd: 0.05,
  judge_model: null,
  model: "gpt-4o-mini",
  provider: "openai",
  providers_available: [],
  // Additive field (CONTRACTS.md additive protocol, secure-settings
  // worktree). Kept off the generated `SettingsResponse` type until
  // checkpoint 4 runs `pnpm gen:api` — once that lands, both fixtures
  // and the live API stay byte-for-byte identical.
  //
  // We default the configured provider to ``openai`` so existing
  // Playground / Inventory tests don't trip the MissingKeyBanner /
  // MissingKeyInlineAlert by accident. Suites that exercise the
  // unconfigured path override this via `server.use(...)`.
  keys: [
    { provider: "anthropic", configured: false, source: "none", masked: null },
    {
      provider: "openai",
      configured: true,
      source: "keyring",
      masked: "sk-...xxxx",
    },
  ],
};

export const runDetailFixture: RunDetailResponse = {
  run_id: "run_test_one",
  status: "done",
  target_id: "pl_test_one",
  target_kind: "pipeline",
  target_version: 1,
  cost_usd: 0.0021,
  started_at: "2026-05-23T10:00:00Z",
  finished_at: "2026-05-23T10:00:05Z",
  outputs: [
    { case_index: 0, text: "ok", error: null, image_path: null },
  ],
};

/**
 * Iteration fixtures — used by AutoIterate / IterationProgress /
 * IterationTimeline / DownstreamImpactBanner tests. Kept here so any
 * test in the suite can `server.use(...)` to override one endpoint
 * without re-declaring the entire fixture surface.
 */
export const iterationBaselineFixture: IterationView = {
  id: "it_baseline",
  prompt_id: "p_test_alpha",
  round: 1,
  session_id: "sess_test_alpha",
  is_baseline: true,
  parent_version: 1,
  new_version: null,
  revise_mode: null,
  revise_instruction: null,
  critique_text: null,
  weighted_score: 0.62,
  per_dim_scores: { accuracy: 0.6, relevance: 0.7, safety: 0.6, format: 0.5 },
  downstream_status: null,
  converged_reason: null,
  started_at: "2026-05-20T10:00:00Z",
  finished_at: "2026-05-20T10:00:30Z",
};

export const iterationRound2Fixture: IterationView = {
  id: "it_round2",
  prompt_id: "p_test_alpha",
  round: 2,
  session_id: "sess_test_alpha",
  is_baseline: false,
  parent_version: 1,
  new_version: 2,
  revise_mode: "auto",
  revise_instruction: null,
  critique_text: "increased specificity in the system message",
  weighted_score: 0.81,
  per_dim_scores: { accuracy: 0.85, relevance: 0.83, safety: 0.75, format: 0.8 },
  downstream_status: { draft: "unverified", polish: "unverified" },
  converged_reason: "delta",
  started_at: "2026-05-20T10:01:00Z",
  finished_at: "2026-05-20T10:01:30Z",
};

export const iterateSessionRunningFixture: IterateSessionResponse = {
  session_id: "sess_test_alpha",
  status: "running",
  converged_reason: null,
  iterations: [iterationBaselineFixture],
  final_version: null,
};

export const iterateSessionConvergedFixture: IterateSessionResponse = {
  session_id: "sess_test_alpha",
  status: "converged",
  converged_reason: "delta",
  iterations: [iterationBaselineFixture, iterationRound2Fixture],
  final_version: 2,
};

export const iterateSessionFailedFixture: IterateSessionResponse = {
  session_id: "sess_test_failed",
  status: "failed",
  converged_reason: "critic_failed",
  iterations: [iterationBaselineFixture],
  final_version: null,
};

/**
 * Default profiles fixture — one OK profile so the MissingKeyBanner
 * stays silent on every page that doesn't care about the banner.
 * Tests that exercise the banner (or the Settings page itself) call
 * ``server.use(...)`` with their own fixture.
 */
export const profilesFixture = [
  {
    id: "prof_default",
    label: "Default OpenAI",
    base_url: "https://api.openai.com/v1",
    protocol: "openai-compat" as const,
    model_id: "gpt-4o-mini",
    notes: "",
    key_configured: true,
    key_source: "keyring" as const,
    key_masked: "sk-...xxxx",
  },
];

/** Default presets fixture — empty so the chip row renders the "no presets" hint. */
export const presetsFixture: Array<{
  name: string;
  base_url: string;
  protocol: "openai-compat" | "anthropic";
  model_id: string;
}> = [];

export const handlers = [
  http.get("/api/settings", () => HttpResponse.json(settingsFixture)),
  http.get("/api/profiles", () => HttpResponse.json(profilesFixture)),
  http.get("/api/profile-presets", () => HttpResponse.json(presetsFixture)),
  http.get("/api/prompts", () => HttpResponse.json(promptListFixture)),
  http.get("/api/prompts/:promptId", ({ params }) => {
    if (params.promptId === "p_test_alpha") {
      return HttpResponse.json(promptDetailFixture);
    }
    return new HttpResponse(
      JSON.stringify({ detail: "unknown prompt id" }),
      { status: 404, headers: { "content-type": "application/json" } },
    );
  }),
  http.get("/api/pipelines", () => HttpResponse.json(pipelineListFixture)),
  http.get("/api/pipelines/:pipelineId", ({ params }) => {
    if (params.pipelineId === "pl_test_one") {
      return HttpResponse.json(pipelineDetailFixture);
    }
    return new HttpResponse(
      JSON.stringify({ detail: "unknown pipeline id" }),
      { status: 404, headers: { "content-type": "application/json" } },
    );
  }),
];
