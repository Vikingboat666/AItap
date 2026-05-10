/**
 * Hand-transcribed mirror of `src/aitap/server/routes/__init__.py`
 * (contract version 1, 2026-05-09) and `src/aitap/scanner/models.py`.
 *
 * This file is a placeholder until `pnpm run gen:api` can run against
 * the live FastAPI app. When that path is wired, replace this file
 * with output from openapi-typescript-codegen and update the
 * `gen:api` script in package.json accordingly.
 *
 * Adding new optional fields here is fine. Renaming or removing
 * existing fields is a breaking change — see CONTRACTS.md.
 */

// ---------- enums (mirrored from scanner/models.py) ----------

export type Provider =
  | "anthropic"
  | "openai"
  | "langchain"
  | "llamaindex"
  | "dashscope"
  | "unknown";

export type Role = "system" | "user" | "assistant" | "tool";

export type TemplateKind =
  | "literal"
  | "fstring"
  | "jinja2"
  | "concat"
  | "unresolved";

export type Confidence = "high" | "medium" | "low";

export type EdgeKind =
  | "variable"
  | "lc_pipe"
  | "llamaindex"
  | "function"
  | "unresolved";

// ---------- scanner models ----------

export interface CodeLocation {
  file: string;
  line_start: number;
  line_end: number;
  col_start?: number | null;
  col_end?: number | null;
}

export interface TemplateVariable {
  name: string;
  inferred_type?: string | null;
}

export interface Message {
  role: Role;
  template_text: string;
  template_kind?: TemplateKind;
  variables?: TemplateVariable[];
}

export interface CallParameters {
  model?: string | null;
  temperature?: number | null;
  max_tokens?: number | null;
  top_p?: number | null;
  response_format?: string | null;
  extra?: Record<string, string>;
}

export interface PromptSite {
  id: string;
  name: string;
  provider: Provider;
  location: CodeLocation;
  messages: Message[];
  parameters?: CallParameters;
  purpose?: string | null;
  confidence?: Confidence;
  tags?: string[];
}

export interface PipelineNode {
  prompt_id: string;
  label?: string | null;
}

export interface PipelineEdge {
  source: string;
  target: string;
  kind: EdgeKind;
  via?: string | null;
  confidence?: Confidence;
}

export interface Pipeline {
  id: string;
  name: string;
  nodes: PipelineNode[];
  edges: PipelineEdge[];
  entry_points?: string[];
  exit_points?: string[];
}

export interface ProviderEvidence {
  provider: Provider;
  source: ".env" | "config" | "code";
  location: CodeLocation;
  key_var_name: string;
}

// ---------- API DTOs ----------

export interface PromptSummary {
  id: string;
  name: string;
  provider: Provider;
  file: string;
  line_start: number;
  purpose?: string | null;
  confidence: Confidence;
  latest_version: number;
}

export interface PromptListResponse {
  prompts: PromptSummary[];
}

export interface PromptVersionInfo {
  version: number;
  note?: string | null;
  created_at: string;
  created_by: "human" | "iteration";
  parent_version?: number | null;
}

export interface PromptDetailResponse {
  site: PromptSite;
  versions: PromptVersionInfo[];
}

export interface PromptVersionCreate {
  messages: Message[];
  parameters: CallParameters;
  note?: string | null;
  parent_version?: number | null;
}

export interface PromptVersionResponse {
  prompt_id: string;
  version: number;
}

export interface PipelineSummary {
  id: string;
  name: string;
  node_count: number;
  edge_count: number;
  entry_count: number;
  exit_count: number;
}

export interface PipelineListResponse {
  pipelines: PipelineSummary[];
}

export interface PipelineDetailResponse {
  pipeline: Pipeline;
  site_index: Record<string, PromptSummary>;
}

export interface DatasetCase {
  inputs: Record<string, unknown>;
  expected_at?: Record<string, unknown> | null;
}

export interface RunCreate {
  target_kind: "prompt" | "pipeline";
  target_id: string;
  target_version: number;
  cases?: DatasetCase[];
  dataset_id?: string | null;
  provider: Provider;
  model: string;
  parameters: CallParameters;
  pipeline_segment?: string[] | null;
}

export interface RunOutput {
  case_index: number;
  text?: string | null;
  image_path?: string | null;
  error?: string | null;
  intermediate?: Record<string, string> | null;
}

export interface RunResponse {
  run_id: string;
  status: "running" | "done" | "failed";
}

export interface RunDetailResponse {
  run_id: string;
  target_kind: "prompt" | "pipeline";
  target_id: string;
  target_version: number;
  status: "running" | "done" | "failed";
  outputs: RunOutput[];
  cost_usd: number;
  started_at: string;
  finished_at?: string | null;
}

export interface RunListResponse {
  runs: RunResponse[];
}

export interface FeedbackCreate {
  case_index: number;
  rating?: -1 | 0 | 1 | null;
  ideal_answer?: string | null;
  critique?: string | null;
}

export interface FeedbackResponse {
  feedback_id: number;
}

export interface IterateRequest {
  judge_model?: string | null;
  max_iterations?: number;
  convergence_threshold?: number;
  include_downstream?: boolean;
}

export interface IterateResponse {
  new_version: number;
  score_before?: number | null;
  score_after?: number | null;
  converged: boolean;
  downstream_impact?: string[];
}

export interface HistoryEntry {
  version: number;
  note?: string | null;
  created_at: string;
  created_by: "human" | "iteration";
  parent_version?: number | null;
  avg_score?: number | null;
}

export interface HistoryResponse {
  prompt_id: string;
  entries: HistoryEntry[];
}

export interface RollbackRequest {
  target_version: number;
}

export interface CostEstimateResponse {
  estimated_tokens: number;
  estimated_usd: number;
  model: string;
}

export interface SettingsResponse {
  provider: Provider;
  model: string;
  judge_model?: string | null;
  cost_per_run_usd: number;
  cost_per_session_usd: number;
  providers_available: ProviderEvidence[];
}

export interface SettingsUpdate {
  provider?: Provider | null;
  model?: string | null;
  judge_model?: string | null;
  cost_per_run_usd?: number | null;
  cost_per_session_usd?: number | null;
}

export interface ScanRequest {
  path?: string | null;
  deep?: boolean;
}

export interface ScanResponse {
  files_scanned: number;
  prompt_count: number;
  pipeline_count: number;
  warnings: string[];
}
