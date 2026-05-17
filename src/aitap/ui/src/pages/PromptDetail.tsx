import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../api/client";
import { Badge, Card, CardHeader } from "../components/primitives";
import { ErrorState } from "../components/feedback";
import { BlockSkeleton } from "../components/skeletons";
import type { PromptVersionInfo } from "../api/generated/models/PromptVersionInfo";

export function PromptDetail() {
  const { id = "" } = useParams();
  const [diffPair, setDiffPair] = useState<
    [PromptVersionInfo, PromptVersionInfo] | null
  >(null);

  const q = useQuery({
    queryKey: ["prompt", id],
    queryFn: () =>
      apiClient.prompts.getPromptApiPromptsPromptIdGet({ promptId: id }),
    enabled: !!id,
  });

  if (q.isLoading) {
    return <BlockSkeleton label="loading prompt…" />;
  }
  if (q.isError) {
    return (
      <ErrorState
        title="couldn't load prompt"
        error={q.error}
        onRetry={() => void q.refetch()}
      />
    );
  }
  if (!q.data) {
    // Loading is settled and no error — defensive empty state. Should
    // not happen with the contract types but keeps the component total.
    return (
      <Card className="p-6 text-sm text-ink-500">no prompt data</Card>
    );
  }
  const { site, versions } = q.data;

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <div className="space-y-4 lg:col-span-2">
        <Card>
          <CardHeader
            title={
              <span className="font-mono text-sm">
                {site.name}{" "}
                <Badge tone="brand">{site.provider}</Badge>
              </span>
            }
            subtitle={
              <>
                {site.location.file}:{site.location.line_start}–
                {site.location.line_end}
              </>
            }
            action={
              <div className="flex gap-2">
                <Link
                  to={`/playground/prompt/${encodeURIComponent(site.id)}`}
                  className="rounded-md bg-brand-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-700"
                >
                  open in playground
                </Link>
                <Link
                  to={`/history/${encodeURIComponent(site.id)}`}
                  className="rounded-md bg-ink-100 px-3 py-1.5 text-xs font-medium text-ink-700 hover:bg-ink-200"
                >
                  history
                </Link>
              </div>
            }
          />
          <div className="px-4 py-3 text-xs text-ink-600">
            {site.purpose ?? (
              <span className="italic text-ink-400">
                no purpose inferred (L2 not run)
              </span>
            )}
          </div>
        </Card>

        <Card>
          <CardHeader title="messages" />
          <ul className="divide-y divide-ink-100">
            {site.messages.map((m, i) => (
              <li key={i} className="px-4 py-3">
                <div className="mb-2 flex items-center gap-2">
                  <Badge>{m.role}</Badge>
                  <span className="text-[11px] text-ink-400">
                    {m.template_kind ?? "literal"}
                  </span>
                  {m.variables?.length ? (
                    <span className="text-[11px] text-ink-400">
                      vars: {m.variables.map((v) => v.name).join(", ")}
                    </span>
                  ) : null}
                </div>
                <pre className="whitespace-pre-wrap rounded-md bg-ink-50 px-3 py-2 font-mono text-xs text-ink-700">
                  {m.template_text}
                </pre>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <div className="space-y-4">
        <Card>
          <CardHeader title="parameters" />
          <dl className="grid grid-cols-2 gap-2 px-4 py-3 text-xs">
            {Object.entries(site.parameters ?? {})
              .filter(([, v]) => v !== null && v !== undefined)
              .map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-ink-500">{k}</dt>
                  <dd className="font-mono text-ink-700">
                    {typeof v === "object" ? JSON.stringify(v) : String(v)}
                  </dd>
                </div>
              ))}
          </dl>
        </Card>

        <VersionsCard
          promptId={site.id}
          versions={versions}
          onDiff={(a, b) => setDiffPair([a, b])}
        />
      </div>

      {diffPair && (
        <DiffPlaceholderModal
          promptId={site.id}
          pair={diffPair}
          onClose={() => setDiffPair(null)}
        />
      )}
    </div>
  );
}

function VersionsCard({
  promptId,
  versions,
  onDiff,
}: {
  promptId: string;
  versions: PromptVersionInfo[];
  onDiff: (a: PromptVersionInfo, b: PromptVersionInfo) => void;
}) {
  // "Diff" needs two versions to be meaningful; offer compare-with-parent
  // when the row has a parent_version we can resolve, and silently disable
  // the button when there's nothing to compare against.
  const versionByNumber = new Map(versions.map((v) => [v.version, v]));

  return (
    <Card>
      <CardHeader
        title="versions"
        subtitle={`${versions.length} recorded`}
      />
      {versions.length === 0 ? (
        <div className="px-4 py-3 text-xs italic text-ink-400">
          no versions recorded yet — edit & save in the playground to create v1
        </div>
      ) : (
        <ul className="divide-y divide-ink-100">
          {versions.map((v) => {
            const parent =
              v.parent_version != null
                ? versionByNumber.get(v.parent_version)
                : undefined;
            return (
              <li
                key={v.version}
                className="flex items-center justify-between px-4 py-2 text-xs"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-ink-700">
                      v{v.version}
                    </span>
                    <Badge
                      tone={
                        v.created_by === "iteration" ? "warn" : "neutral"
                      }
                    >
                      {v.created_by}
                    </Badge>
                  </div>
                  <div className="mt-0.5 truncate text-ink-500">
                    {v.note ?? "no note"}
                  </div>
                </div>
                <button
                  type="button"
                  disabled={!parent}
                  onClick={() => parent && onDiff(parent, v)}
                  title={
                    parent
                      ? `diff v${parent.version} vs v${v.version}`
                      : "no parent version to diff against"
                  }
                  className="ml-3 shrink-0 rounded-md bg-ink-100 px-2 py-1 font-mono text-[11px] text-ink-700 hover:bg-ink-200 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  diff{parent ? ` v${parent.version}` : ""}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <div className="border-t border-ink-100 px-4 py-2 text-[11px] text-ink-400">
        prompt id: <span className="font-mono">{promptId}</span>
      </div>
    </Card>
  );
}

function DiffPlaceholderModal({
  promptId,
  pair,
  onClose,
}: {
  promptId: string;
  pair: [PromptVersionInfo, PromptVersionInfo];
  onClose: () => void;
}) {
  // Real side-by-side diff is a follow-up (M4). For now we direct users
  // to the CLI which already renders a unified diff for any two versions.
  const [a, b] = pair;
  const cliHint = `aitap diff ${promptId} ${a.version} ${b.version}`;
  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink-900/40 p-4"
      onClick={onClose}
    >
      <Card
        className="w-full max-w-md space-y-3 p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="text-sm font-semibold text-ink-800">
          diff v{a.version} ↔ v{b.version}
        </div>
        <p className="text-xs text-ink-600">
          A graphical diff view is on the M4 roadmap. For now, run the
          CLI command below to see a unified diff of every message and
          parameter change between these two versions:
        </p>
        <pre className="overflow-x-auto rounded-md bg-ink-50 px-3 py-2 font-mono text-xs text-ink-800">
          {cliHint}
        </pre>
        <div className="flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md bg-ink-100 px-3 py-1.5 text-xs font-medium text-ink-700 hover:bg-ink-200"
          >
            close
          </button>
        </div>
      </Card>
    </div>
  );
}
