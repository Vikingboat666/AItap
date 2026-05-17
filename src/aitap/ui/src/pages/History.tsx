/**
 * Version history for one prompt.
 *
 * Reads `GET /api/history/{prompt_id}` via the generated `HistoryService`
 * and renders three things:
 *
 *   1. A reverse-chronological version timeline (newest first) with
 *      author tone (human vs. iteration), note, parent lineage, and
 *      timestamp.
 *   2. A per-version score chart — an inline SVG bar chart so we don't
 *      pull a charting library for a handful of points. Versions whose
 *      `avg_score` is null render as a striped placeholder bar so users
 *      can still see them on the x-axis (and learn that "no score yet"
 *      is a real state, not a bug).
 *   3. A "Diff" affordance per row that opens a side-by-side compare
 *      modal vs. the previous version. The body of the modal is a
 *      placeholder pointing at `aitap diff` — wiring the actual prompt
 *      text + critique deltas needs the version-content endpoint that
 *      lands in M3, so we stop short of fetching it here on purpose.
 *
 * Loading + error states are explicit (no spinner-of-doom): we show a
 * skeleton card while the query is in flight and a retry button when it
 * fails, so users on flaky networks aren't stuck.
 */

import type { ReactNode } from "react";
import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { HistoryService } from "../api/generated";
import type { HistoryEntry, HistoryResponse } from "../api/generated";
import { Badge, Card, CardHeader, EmptyState } from "../components/primitives";
import { clsx } from "../lib/clsx";

interface DiffTarget {
  current: HistoryEntry;
  previous: HistoryEntry | null;
}

export function History() {
  const { promptId = "" } = useParams();
  const [diffTarget, setDiffTarget] = useState<DiffTarget | null>(null);

  const historyQ = useQuery<HistoryResponse>({
    queryKey: ["history", promptId],
    queryFn: () =>
      HistoryService.getHistoryApiHistoryPromptIdGet({ promptId }),
    enabled: !!promptId,
  });

  // Sort newest-first for the UI without mutating the server payload.
  // The backend returns ascending by version; reversing here keeps the
  // chart x-axis "older -> newer" while the list reads top-down "newer".
  const entries = useMemo<HistoryEntry[]>(() => {
    if (!historyQ.data) return [];
    return [...historyQ.data.entries].sort((a, b) => b.version - a.version);
  }, [historyQ.data]);

  if (!promptId) {
    return (
      <EmptyState
        title="no prompt selected"
        hint="open a prompt from the inventory to view its history"
      />
    );
  }

  if (historyQ.isLoading) {
    return <HistorySkeleton />;
  }

  if (historyQ.isError) {
    return (
      <Card className="space-y-3 p-6">
        <div className="text-sm font-medium text-rose-700">
          failed to load history
        </div>
        <div className="text-xs text-ink-500">
          {historyQ.error instanceof Error
            ? historyQ.error.message
            : "unknown error"}
        </div>
        <button
          type="button"
          onClick={() => historyQ.refetch()}
          className="rounded-md bg-brand-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-700"
        >
          retry
        </button>
      </Card>
    );
  }

  if (!historyQ.data) {
    return <HistorySkeleton />;
  }

  return (
    <div className="space-y-4">
      <ScoreChart entries={historyQ.data.entries} />

      <Card>
        <CardHeader
          title="version history"
          subtitle={`prompt ${historyQ.data.prompt_id}`}
        />
        {entries.length === 0 ? (
          <div className="px-4 py-6 text-xs italic text-ink-400">
            no versions recorded yet
          </div>
        ) : (
          <ol className="divide-y divide-ink-100">
            {entries.map((entry, idx) => {
              // entries is newest-first; the next index in the reversed
              // list is the older sibling, which is the diff baseline.
              const previous = entries[idx + 1] ?? null;
              return (
                <VersionRow
                  key={entry.version}
                  entry={entry}
                  previous={previous}
                  onDiff={() => setDiffTarget({ current: entry, previous })}
                />
              );
            })}
          </ol>
        )}
      </Card>

      {diffTarget && (
        <DiffModal
          promptId={historyQ.data.prompt_id}
          target={diffTarget}
          onClose={() => setDiffTarget(null)}
        />
      )}
    </div>
  );
}

interface VersionRowProps {
  entry: HistoryEntry;
  previous: HistoryEntry | null;
  onDiff: () => void;
}

function VersionRow({ entry, previous, onDiff }: VersionRowProps) {
  return (
    <li className="flex items-start justify-between gap-4 px-4 py-3 text-xs">
      <div>
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm text-ink-800">v{entry.version}</span>
          <Badge tone={entry.created_by === "iteration" ? "warn" : "neutral"}>
            {entry.created_by}
          </Badge>
          {entry.parent_version != null && (
            <span className="text-[11px] text-ink-400">
              parent v{entry.parent_version}
            </span>
          )}
        </div>
        <div className="mt-1 text-ink-500">{entry.note ?? "no note"}</div>
        <div className="mt-1 text-[11px] text-ink-400">
          {new Date(entry.created_at).toLocaleString()}
        </div>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-2">
        {entry.avg_score != null ? (
          <div className="text-sm font-medium text-ink-700">
            {(entry.avg_score * 100).toFixed(0)}%
          </div>
        ) : (
          <div className="text-[11px] italic text-ink-400">no score</div>
        )}
        <button
          type="button"
          onClick={onDiff}
          disabled={!previous}
          title={
            previous
              ? `compare v${entry.version} vs v${previous.version}`
              : "no earlier version to diff against"
          }
          className={clsx(
            "rounded-md px-2 py-1 text-[11px] font-medium",
            previous
              ? "bg-ink-100 text-ink-700 hover:bg-ink-200"
              : "cursor-not-allowed bg-ink-50 text-ink-300",
          )}
        >
          diff
        </button>
      </div>
    </li>
  );
}

interface ScoreChartProps {
  entries: HistoryEntry[];
}

/**
 * Inline SVG bar chart showing avg_score per version (ascending on the
 * x-axis). We deliberately avoid a charting library — the dataset is
 * tiny, and ad-hoc SVG keeps the bundle lean and the styling on-brand.
 */
function ScoreChart({ entries }: ScoreChartProps) {
  // Render ascending by version (so the eye walks left-to-right through
  // time). `entries` comes ascending from the API but we don't trust
  // that — sort defensively.
  const sorted = useMemo(
    () => [...entries].sort((a, b) => a.version - b.version),
    [entries],
  );

  return (
    <Card>
      <CardHeader
        title="score by version"
        subtitle={
          sorted.length === 0
            ? "no versions yet"
            : `${sorted.length} version${sorted.length === 1 ? "" : "s"}`
        }
      />
      <div className="px-4 py-4">
        {sorted.length === 0 ? (
          <div className="text-xs italic text-ink-400">
            score history will appear here once iterations run
          </div>
        ) : (
          <ChartBars entries={sorted} />
        )}
      </div>
    </Card>
  );
}

function ChartBars({ entries }: { entries: HistoryEntry[] }) {
  // Layout constants — keep the SVG viewBox responsive while still
  // pixel-snapping the labels.
  const width = 600;
  const height = 140;
  const padX = 28;
  const padY = 24;
  const barGap = 6;
  const innerW = width - padX * 2;
  const innerH = height - padY * 2;
  const barW = Math.max(
    4,
    (innerW - barGap * (entries.length - 1)) / Math.max(1, entries.length),
  );

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="average score per version"
      className="w-full"
    >
      {/* y-axis baseline */}
      <line
        x1={padX}
        x2={width - padX}
        y1={height - padY}
        y2={height - padY}
        stroke="currentColor"
        className="text-ink-200"
        strokeWidth={1}
      />
      {/* 50% guideline */}
      <line
        x1={padX}
        x2={width - padX}
        y1={padY + innerH / 2}
        y2={padY + innerH / 2}
        stroke="currentColor"
        className="text-ink-100"
        strokeDasharray="3 3"
        strokeWidth={1}
      />
      {entries.map((entry, idx) => {
        const x = padX + idx * (barW + barGap);
        const hasScore = entry.avg_score != null;
        const score = hasScore ? Math.max(0, Math.min(1, entry.avg_score!)) : 0;
        const barH = hasScore ? score * innerH : innerH * 0.08;
        const y = height - padY - barH;
        return (
          <g key={entry.version}>
            <rect
              x={x}
              y={y}
              width={barW}
              height={barH}
              rx={2}
              className={
                hasScore
                  ? entry.created_by === "iteration"
                    ? "fill-amber-400"
                    : "fill-brand-500"
                  : "fill-ink-200"
              }
            >
              <title>
                {`v${entry.version} — ${
                  hasScore ? `${(score * 100).toFixed(0)}%` : "no score"
                }`}
              </title>
            </rect>
            <text
              x={x + barW / 2}
              y={height - padY + 12}
              textAnchor="middle"
              className="fill-ink-500 text-[10px]"
            >
              v{entry.version}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

interface DiffModalProps {
  promptId: string;
  target: DiffTarget;
  onClose: () => void;
}

/**
 * Placeholder side-by-side diff modal. Until the version-content
 * endpoint lands we render the metadata we already have plus an
 * `aitap diff` CLI hint, so users know how to get the actual text
 * comparison today.
 */
function DiffModal({ promptId, target, onClose }: DiffModalProps) {
  const { current, previous } = target;
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`diff v${current.version}`}
      className="fixed inset-0 z-40 flex items-center justify-center bg-ink-900/40 px-4 py-8"
      onClick={onClose}
    >
      <div
        className="w-full max-w-3xl overflow-hidden rounded-lg bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-ink-100 px-4 py-3">
          <div>
            <div className="text-sm font-semibold text-ink-800">
              diff{" "}
              <span className="font-mono text-ink-600">
                {previous ? `v${previous.version}` : "—"} → v{current.version}
              </span>
            </div>
            <div className="text-[11px] text-ink-500">prompt {promptId}</div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md bg-ink-100 px-2 py-1 text-xs text-ink-700 hover:bg-ink-200"
          >
            close
          </button>
        </div>
        <div className="grid grid-cols-1 gap-3 px-4 py-4 md:grid-cols-2">
          <DiffPane title="before" entry={previous} />
          <DiffPane title="after" entry={current} />
        </div>
        <div className="border-t border-ink-100 bg-ink-50/60 px-4 py-3 text-[11px] text-ink-500">
          full text diff lands with the version-content endpoint (M3). for
          now, run{" "}
          <code className="rounded bg-white px-1 py-0.5 font-mono text-ink-700">
            aitap diff {promptId}{" "}
            {previous ? `--from v${previous.version} ` : ""}--to v
            {current.version}
          </code>{" "}
          to see the prompt body delta in your terminal.
        </div>
      </div>
    </div>
  );
}

function DiffPane({
  title,
  entry,
}: {
  title: string;
  entry: HistoryEntry | null;
}) {
  return (
    <div className="rounded-md border border-ink-100 bg-ink-50/40 p-3">
      <div className="mb-2 flex items-center gap-2">
        <Badge tone="neutral">{title}</Badge>
        {entry ? (
          <>
            <span className="font-mono text-xs text-ink-700">
              v{entry.version}
            </span>
            <Badge tone={entry.created_by === "iteration" ? "warn" : "neutral"}>
              {entry.created_by}
            </Badge>
          </>
        ) : (
          <span className="text-[11px] italic text-ink-400">
            no earlier version
          </span>
        )}
      </div>
      {entry && (
        <dl className="space-y-1 text-[11px]">
          <Row label="created">
            {new Date(entry.created_at).toLocaleString()}
          </Row>
          <Row label="parent">
            {entry.parent_version != null ? `v${entry.parent_version}` : "—"}
          </Row>
          <Row label="score">
            {entry.avg_score != null
              ? `${(entry.avg_score * 100).toFixed(0)}%`
              : "no score"}
          </Row>
          <Row label="note">{entry.note ?? "no note"}</Row>
        </dl>
      )}
    </div>
  );
}

function Row({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="flex gap-2">
      <dt className="w-16 shrink-0 text-ink-500">{label}</dt>
      <dd className="text-ink-700">{children}</dd>
    </div>
  );
}

function HistorySkeleton() {
  return (
    <div className="space-y-4">
      <Card className="px-4 py-6">
        <div className="h-3 w-32 animate-pulse rounded bg-ink-100" />
        <div className="mt-4 h-24 w-full animate-pulse rounded bg-ink-100" />
      </Card>
      <Card className="px-4 py-6">
        <div className="h-3 w-40 animate-pulse rounded bg-ink-100" />
        <div className="mt-4 space-y-2">
          {[0, 1, 2].map((i) => (
            <div
              key={i}
              className="h-10 w-full animate-pulse rounded bg-ink-100"
            />
          ))}
        </div>
      </Card>
    </div>
  );
}
