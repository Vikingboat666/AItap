import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { apiClient } from "../api/client";
import type { PipelineSummary } from "../api/generated/models/PipelineSummary";
import type { PromptSummary } from "../api/generated/models/PromptSummary";
import { Badge, Card, CardHeader, EmptyState } from "../components/primitives";
import { ErrorState } from "../components/feedback";
import { ListSkeleton } from "../components/skeletons";
import { clsx } from "../lib/clsx";

type Tab = "prompts" | "pipelines";

export function Inventory() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>("prompts");
  const promptsQ = useQuery({
    queryKey: ["prompts"],
    queryFn: () => apiClient.prompts.listPromptsApiPromptsGet(),
  });
  const pipelinesQ = useQuery({
    queryKey: ["pipelines"],
    queryFn: () => apiClient.pipelines.listPipelinesApiPipelinesGet(),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <TabButton
          active={tab === "prompts"}
          label={t("inventory.tabPrompts")}
          count={promptsQ.data?.prompts.length}
          onClick={() => setTab("prompts")}
        />
        <TabButton
          active={tab === "pipelines"}
          label={t("inventory.tabPipelines")}
          count={pipelinesQ.data?.pipelines.length}
          onClick={() => setTab("pipelines")}
        />
      </div>

      {tab === "prompts" ? (
        <PromptList
          isLoading={promptsQ.isLoading}
          isError={promptsQ.isError}
          error={promptsQ.error}
          onRetry={() => void promptsQ.refetch()}
          prompts={promptsQ.data?.prompts ?? []}
        />
      ) : (
        <PipelineList
          isLoading={pipelinesQ.isLoading}
          isError={pipelinesQ.isError}
          error={pipelinesQ.error}
          onRetry={() => void pipelinesQ.refetch()}
          pipelines={pipelinesQ.data?.pipelines ?? []}
        />
      )}
    </div>
  );
}

function TabButton({
  active,
  label,
  count,
  onClick,
}: {
  active: boolean;
  label: string;
  count: number | undefined;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={clsx(
        "rounded-md px-3 py-1.5 text-sm font-medium capitalize transition-colors",
        active
          ? "bg-brand-600 text-white"
          : "bg-white text-ink-600 hover:bg-ink-100",
      )}
    >
      {label}
      <span className="ml-2 text-xs opacity-70">{count ?? "—"}</span>
    </button>
  );
}

function PromptList({
  isLoading,
  isError,
  error,
  onRetry,
  prompts,
}: {
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  onRetry: () => void;
  prompts: PromptSummary[];
}) {
  const { t } = useTranslation();
  if (isLoading) {
    return <ListSkeleton label={t("inventory.loadingPrompts")} rows={4} />;
  }
  if (isError) {
    return (
      <ErrorState
        title={t("inventory.couldntLoadPrompts")}
        error={error}
        onRetry={onRetry}
      />
    );
  }
  if (prompts.length === 0) {
    return (
      <EmptyState
        title={t("inventory.noPromptsYet")}
        hint={t("inventory.noPromptsHint")}
      />
    );
  }
  return (
    <Card>
      <CardHeader
        title={t("inventory.discoveredPrompts")}
        subtitle={t("inventory.discoveredPromptsSubtitle")}
      />
      <ul className="divide-y divide-ink-100">
        {prompts.map((p) => (
          <PromptRow key={p.id} prompt={p} />
        ))}
      </ul>
    </Card>
  );
}

function PromptRow({ prompt: p }: { prompt: PromptSummary }) {
  const confidenceTone =
    p.confidence === "high"
      ? "ok"
      : p.confidence === "medium"
        ? "warn"
        : "neutral";
  return (
    <li>
      <Link
        to={`/prompts/${encodeURIComponent(p.id)}`}
        className="flex items-center justify-between gap-4 px-4 py-3 hover:bg-ink-50"
      >
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium text-ink-800">
              {p.name}
            </span>
            <Badge tone="brand">{p.provider}</Badge>
            <Badge tone={confidenceTone}>{p.confidence}</Badge>
          </div>
          <div className="mt-1 truncate text-xs text-ink-500">
            {p.purpose ?? "—"}
          </div>
        </div>
        <div className="shrink-0 font-mono text-[11px] text-ink-400">
          {p.file}:{p.line_start} · v{p.latest_version}
        </div>
      </Link>
    </li>
  );
}

function PipelineList({
  isLoading,
  isError,
  error,
  onRetry,
  pipelines,
}: {
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  onRetry: () => void;
  pipelines: PipelineSummary[];
}) {
  const { t } = useTranslation();
  if (isLoading) {
    return <ListSkeleton label={t("inventory.loadingPipelines")} rows={3} />;
  }
  if (isError) {
    return (
      <ErrorState
        title={t("inventory.couldntLoadPipelines")}
        error={error}
        onRetry={onRetry}
      />
    );
  }
  if (pipelines.length === 0) {
    return (
      <EmptyState
        title={t("inventory.noPipelinesYet")}
        hint={t("inventory.noPipelinesHint")}
      />
    );
  }
  return (
    <Card>
      <CardHeader
        title={t("inventory.discoveredPipelines")}
        subtitle={t("inventory.discoveredPipelinesSubtitle")}
      />
      <ul className="divide-y divide-ink-100">
        {pipelines.map((p) => (
          <li key={p.id}>
            <Link
              to={`/pipelines/${encodeURIComponent(p.id)}`}
              className="flex items-center justify-between gap-4 px-4 py-3 hover:bg-ink-50"
            >
              <div>
                <div className="text-sm font-medium text-ink-800">{p.name}</div>
                <div className="mt-1 text-xs text-ink-500">
                  {t("inventory.pipelineMeta", {
                    nodes: p.node_count,
                    edges: p.edge_count,
                    entry: p.entry_count,
                    exit: p.exit_count,
                  })}
                </div>
              </div>
              <span className="font-mono text-[11px] text-ink-400">{p.id}</span>
            </Link>
          </li>
        ))}
      </ul>
    </Card>
  );
}
