import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type {
  PipelineSummary,
  PromptSummary,
} from "../api/types";
import { Badge, Card, CardHeader, EmptyState } from "../components/primitives";
import { clsx } from "../lib/clsx";

type Tab = "prompts" | "pipelines";

export function Inventory() {
  const [tab, setTab] = useState<Tab>("prompts");
  const promptsQ = useQuery({
    queryKey: ["prompts"],
    queryFn: api.listPrompts,
  });
  const pipelinesQ = useQuery({
    queryKey: ["pipelines"],
    queryFn: api.listPipelines,
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <TabButton
          active={tab === "prompts"}
          label="prompts"
          count={promptsQ.data?.prompts.length}
          onClick={() => setTab("prompts")}
        />
        <TabButton
          active={tab === "pipelines"}
          label="pipelines"
          count={pipelinesQ.data?.pipelines.length}
          onClick={() => setTab("pipelines")}
        />
      </div>

      {tab === "prompts" ? (
        <PromptList
          isLoading={promptsQ.isLoading}
          prompts={promptsQ.data?.prompts ?? []}
        />
      ) : (
        <PipelineList
          isLoading={pipelinesQ.isLoading}
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
  prompts,
}: {
  isLoading: boolean;
  prompts: PromptSummary[];
}) {
  if (isLoading) {
    return <Card className="p-6 text-sm text-ink-500">loading prompts…</Card>;
  }
  if (prompts.length === 0) {
    return (
      <EmptyState
        title="no prompts yet"
        hint="run `aitap scan` to discover prompts in this project"
      />
    );
  }
  return (
    <Card>
      <CardHeader
        title="discovered prompts"
        subtitle="from L1 scan of project tree (mock)"
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
  pipelines,
}: {
  isLoading: boolean;
  pipelines: PipelineSummary[];
}) {
  if (isLoading) {
    return <Card className="p-6 text-sm text-ink-500">loading pipelines…</Card>;
  }
  if (pipelines.length === 0) {
    return (
      <EmptyState
        title="no pipelines yet"
        hint="pipelines are discovered when prompts share data flow"
      />
    );
  }
  return (
    <Card>
      <CardHeader
        title="discovered pipelines"
        subtitle="prompts connected by data flow"
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
                  {p.node_count} nodes · {p.edge_count} edges ·{" "}
                  {p.entry_count} entry · {p.exit_count} exit
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
