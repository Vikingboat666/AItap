/**
 * Canned ScanResult-shaped fixtures. Mirrors the Python contract types
 * so pages can render the real shapes before the backend exists.
 */

import type {
  HistoryResponse,
  PipelineDetailResponse,
  PipelineListResponse,
  PromptDetailResponse,
  PromptListResponse,
  PromptSite,
  PromptSummary,
  RunCreate,
  RunDetailResponse,
  RunResponse,
  ScanRequest,
  ScanResponse,
  SettingsResponse,
} from "./types";

const sites: PromptSite[] = [
  {
    id: "p_summarize_email_001",
    name: "summarize_email",
    provider: "openai",
    location: {
      file: "starter_app/openai_summarizer.py",
      line_start: 18,
      line_end: 32,
    },
    messages: [
      {
        role: "system",
        template_text:
          "You are a precise email summarizer. Output 3 bullets, no preamble.",
        template_kind: "literal",
      },
      {
        role: "user",
        template_text: "Summarize the following email:\n\n{email_body}",
        template_kind: "fstring",
        variables: [{ name: "email_body" }],
      },
    ],
    parameters: {
      model: "gpt-4o-mini",
      temperature: 0.2,
      max_tokens: 256,
      response_format: "text",
    },
    purpose: "summarize incoming customer emails into 3 bullets",
    confidence: "high",
    tags: ["summarization", "customer-support"],
  },
  {
    id: "p_outline_blog_002",
    name: "outline_blog",
    provider: "openai",
    location: {
      file: "starter_app/blog_pipeline.py",
      line_start: 12,
      line_end: 20,
    },
    messages: [
      {
        role: "system",
        template_text:
          "Draft a 5-section outline for a technical blog post. Be concise.",
      },
      {
        role: "user",
        template_text: "Topic: {topic}\nAudience: {audience}",
        template_kind: "fstring",
        variables: [{ name: "topic" }, { name: "audience" }],
      },
    ],
    parameters: { model: "gpt-4o-mini", temperature: 0.4 },
    purpose: "produce a 5-section outline given a topic + audience",
    confidence: "high",
  },
  {
    id: "p_polish_blog_003",
    name: "polish_blog",
    provider: "anthropic",
    location: {
      file: "starter_app/blog_pipeline.py",
      line_start: 34,
      line_end: 52,
    },
    messages: [
      {
        role: "system",
        template_text:
          "Rewrite the following draft for clarity and flow without changing meaning.",
      },
      {
        role: "user",
        template_text: "{draft}",
        template_kind: "fstring",
        variables: [{ name: "draft" }],
      },
    ],
    parameters: { model: "claude-haiku-4-5", temperature: 0.3 },
    purpose: "polish a blog draft for clarity",
    confidence: "medium",
  },
  {
    id: "p_critic_blog_004",
    name: "critic_blog",
    provider: "anthropic",
    location: {
      file: "starter_app/blog_pipeline.py",
      line_start: 60,
      line_end: 78,
    },
    messages: [
      {
        role: "system",
        template_text: "Critique the following draft. Be specific.",
      },
      {
        role: "user",
        template_text: "{draft}",
        template_kind: "fstring",
        variables: [{ name: "draft" }],
      },
    ],
    parameters: { model: "claude-sonnet-4-6", temperature: 0.0 },
    purpose: "produce a structured critique of a blog draft",
    confidence: "high",
  },
];

function summarize(site: PromptSite, version = 1): PromptSummary {
  return {
    id: site.id,
    name: site.name,
    provider: site.provider,
    file: site.location.file,
    line_start: site.location.line_start,
    purpose: site.purpose ?? null,
    confidence: site.confidence ?? "high",
    latest_version: version,
  };
}

const promptIndex: Record<string, PromptSite> = Object.fromEntries(
  sites.map((s) => [s.id, s]),
);

const versionLog: Record<string, HistoryResponse["entries"]> = {
  p_summarize_email_001: [
    {
      version: 1,
      note: "initial extraction",
      created_at: "2026-05-09T10:01:00Z",
      created_by: "human",
      parent_version: null,
      avg_score: 0.78,
    },
    {
      version: 2,
      note: "tightened system prompt",
      created_at: "2026-05-09T15:42:00Z",
      created_by: "iteration",
      parent_version: 1,
      avg_score: 0.86,
    },
  ],
  p_outline_blog_002: [
    {
      version: 1,
      note: "initial",
      created_at: "2026-05-09T10:01:00Z",
      created_by: "human",
      parent_version: null,
      avg_score: 0.71,
    },
  ],
  p_polish_blog_003: [
    {
      version: 1,
      note: "initial",
      created_at: "2026-05-09T10:01:00Z",
      created_by: "human",
      parent_version: null,
      avg_score: null,
    },
  ],
  p_critic_blog_004: [
    {
      version: 1,
      note: "initial",
      created_at: "2026-05-09T10:01:00Z",
      created_by: "human",
      parent_version: null,
      avg_score: 0.65,
    },
  ],
};

const pipelines: PipelineDetailResponse[] = [
  {
    pipeline: {
      id: "pl_blog_workflow_001",
      name: "blog content workflow",
      nodes: [
        { prompt_id: "p_outline_blog_002", label: "outline" },
        { prompt_id: "p_polish_blog_003", label: "polish" },
        { prompt_id: "p_critic_blog_004", label: "critic" },
      ],
      edges: [
        {
          source: "p_outline_blog_002",
          target: "p_polish_blog_003",
          kind: "variable",
          via: "outline_text",
        },
        {
          source: "p_polish_blog_003",
          target: "p_critic_blog_004",
          kind: "variable",
          via: "draft",
        },
      ],
      entry_points: ["p_outline_blog_002"],
      exit_points: ["p_critic_blog_004"],
    },
    site_index: {
      p_outline_blog_002: summarize(promptIndex.p_outline_blog_002, 1),
      p_polish_blog_003: summarize(promptIndex.p_polish_blog_003, 1),
      p_critic_blog_004: summarize(promptIndex.p_critic_blog_004, 1),
    },
  },
];

export function listPrompts(): PromptListResponse {
  return {
    prompts: sites.map((s) =>
      summarize(s, versionLog[s.id]?.at(-1)?.version ?? 1),
    ),
  };
}

export function getPrompt(promptId: string): PromptDetailResponse {
  const site = promptIndex[promptId] ?? sites[0];
  return {
    site,
    versions: versionLog[site.id] ?? [
      {
        version: 1,
        note: null,
        created_at: "2026-05-09T10:01:00Z",
        created_by: "human",
        parent_version: null,
      },
    ],
  };
}

export function listPipelines(): PipelineListResponse {
  return {
    pipelines: pipelines.map(({ pipeline }) => ({
      id: pipeline.id,
      name: pipeline.name,
      node_count: pipeline.nodes.length,
      edge_count: pipeline.edges.length,
      entry_count: pipeline.entry_points?.length ?? 0,
      exit_count: pipeline.exit_points?.length ?? 0,
    })),
  };
}

export function getPipeline(pipelineId: string): PipelineDetailResponse {
  return pipelines.find((p) => p.pipeline.id === pipelineId) ?? pipelines[0];
}

export function createRun(body: RunCreate): RunResponse {
  return {
    run_id: `run_${body.target_id}_${Date.now()}`,
    status: "running",
  };
}

export function getRun(runId: string): RunDetailResponse {
  return {
    run_id: runId,
    target_kind: "prompt",
    target_id: "p_summarize_email_001",
    target_version: 2,
    status: "done",
    started_at: "2026-05-09T16:00:00Z",
    finished_at: "2026-05-09T16:00:08Z",
    cost_usd: 0.012,
    outputs: [
      {
        case_index: 0,
        text: "- Customer reports login failure on Safari\n- Repro: clear cache, retry\n- Suggest enabling 3rd-party cookies",
      },
      {
        case_index: 1,
        text: "- Refund request for order #4421\n- Item arrived damaged, photo attached\n- Recommend full refund + apology coupon",
      },
    ],
  };
}

export function getHistory(promptId: string): HistoryResponse {
  return {
    prompt_id: promptId,
    entries: versionLog[promptId] ?? [],
  };
}

export function getSettings(): SettingsResponse {
  return {
    provider: "openai",
    model: "gpt-4o-mini",
    judge_model: "claude-sonnet-4-6",
    cost_per_run_usd: 0.05,
    cost_per_session_usd: 0.5,
    providers_available: [
      {
        provider: "openai",
        source: ".env",
        location: { file: ".env.example", line_start: 1, line_end: 1 },
        key_var_name: "OPENAI_API_KEY",
      },
      {
        provider: "anthropic",
        source: ".env",
        location: { file: ".env.example", line_start: 2, line_end: 2 },
        key_var_name: "ANTHROPIC_API_KEY",
      },
    ],
  };
}

export function triggerScan(_body: ScanRequest): ScanResponse {
  return {
    files_scanned: 12,
    prompt_count: sites.length,
    pipeline_count: pipelines.length,
    warnings: [],
  };
}
