/**
 * Iteration timeline — History page section listing every iteration
 * *session* that touched a prompt.
 *
 * Fetches `GET /api/iterations/by-prompt/{prompt_id}` and groups rows
 * by `session_id`. Each session is one expandable card showing:
 *
 *   - session_id (truncated for brevity), started_at, total rounds,
 *     final version (= max new_version across the session), converged
 *     reason badge
 *   - on expand: the per-round score chart (inline SVG, same visual
 *     language as IterationProgress / History.ChartBars)
 *
 * Rows arrive sorted newest-first by `started_at`. We re-sort
 * defensively after grouping because the grouping pass loses the
 * server's ordering guarantee for sessions that span a millisecond
 * boundary.
 *
 * Empty state: no iterations recorded → a short copy line so users
 * know the tab is intentional, not broken.
 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { IterateService } from "../api/generated";
import type { IterationView } from "../api/generated";
import { Badge, Card, CardHeader } from "./primitives";
import { DownstreamImpactBanner } from "./DownstreamImpactBanner";
import { clsx } from "../lib/clsx";

interface SessionGroup {
  sessionId: string;
  startedAt: string;
  rounds: IterationView[];
  finalVersion: number | null;
  convergedReason: string | null;
  status: "running" | "converged" | "failed";
  /** Last row's downstream_status, surfaced as the unverified banner. */
  downstreamStatus: Record<string, string> | null;
}

function groupSessions(iterations: IterationView[]): SessionGroup[] {
  const buckets = new Map<string, IterationView[]>();
  for (const it of iterations) {
    const list = buckets.get(it.session_id) ?? [];
    list.push(it);
    buckets.set(it.session_id, list);
  }

  const groups: SessionGroup[] = [];
  for (const [sessionId, rawRows] of buckets) {
    const rows = [...rawRows].sort((a, b) => a.round - b.round);
    const last = rows[rows.length - 1];
    const versions = rows
      .map((r) => r.new_version)
      .filter((v): v is number => v != null);
    const failed = rows.some((r) => r.revise_mode === "failed");
    const convergedReason = last?.converged_reason ?? null;
    let status: SessionGroup["status"] = "running";
    if (failed) status = "failed";
    else if (convergedReason != null) status = "converged";

    groups.push({
      sessionId,
      startedAt: rows[0]?.started_at ?? "",
      rounds: rows,
      finalVersion: versions.length ? Math.max(...versions) : null,
      convergedReason,
      status,
      downstreamStatus: last?.downstream_status ?? null,
    });
  }

  groups.sort((a, b) => b.startedAt.localeCompare(a.startedAt));
  return groups;
}

export interface IterationTimelineProps {
  promptId: string;
}

export function IterationTimeline({ promptId }: IterationTimelineProps) {
  const iterationsQ = useQuery<IterationView[]>({
    queryKey: ["iterations-by-prompt", promptId],
    queryFn: () =>
      IterateService.listIterationsForPromptApiIterationsByPromptPromptIdGet({
        promptId,
      }),
    enabled: !!promptId,
  });

  const groups = useMemo(
    () => (iterationsQ.data ? groupSessions(iterationsQ.data) : []),
    [iterationsQ.data],
  );

  if (iterationsQ.isLoading) {
    return (
      <Card aria-busy="true">
        <CardHeader title="iteration sessions" subtitle="loading…" />
        <div className="space-y-2 px-4 py-3">
          {[0, 1].map((i) => (
            <div
              key={i}
              className="h-12 w-full animate-pulse rounded bg-ink-100"
            />
          ))}
        </div>
      </Card>
    );
  }

  if (iterationsQ.isError) {
    return (
      <Card className="space-y-3 p-4">
        <div className="text-sm font-medium text-rose-700">
          failed to load iteration sessions
        </div>
        <div className="text-xs text-ink-500">
          {iterationsQ.error instanceof Error
            ? iterationsQ.error.message
            : "unknown error"}
        </div>
        <button
          type="button"
          onClick={() => iterationsQ.refetch()}
          className="rounded-md bg-brand-600 px-3 py-1 text-xs font-medium text-white hover:bg-brand-700"
        >
          retry
        </button>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader
        title="iteration sessions"
        subtitle={
          groups.length === 0
            ? "no sessions recorded yet"
            : `${groups.length} session${groups.length === 1 ? "" : "s"}`
        }
      />
      {groups.length === 0 ? (
        <div className="px-4 py-6 text-xs italic text-ink-400">
          start an auto-iterate session from the playground to populate this
          timeline.
        </div>
      ) : (
        <ul className="divide-y divide-ink-100">
          {groups.map((group) => (
            <SessionRow key={group.sessionId} group={group} />
          ))}
        </ul>
      )}
    </Card>
  );
}

function SessionRow({ group }: { group: SessionGroup }) {
  const [open, setOpen] = useState(false);
  // Match the IterationProgress tone scheme: failed sessions get the
  // rose `err` palette so the inline status badge agrees visually with
  // the rose-red failure banners further down the page.
  const tone =
    group.status === "converged"
      ? "ok"
      : group.status === "failed"
        ? "err"
        : "warn";
  return (
    <li className="px-4 py-3 text-xs">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-start justify-between gap-3 text-left hover:bg-ink-50"
      >
        <div>
          <div className="flex items-center gap-2">
            <span className="font-mono text-[11px] text-ink-700">
              {group.sessionId.slice(0, 10)}…
            </span>
            <Badge tone={tone}>{group.status}</Badge>
            {group.convergedReason && (
              <Badge tone="neutral">{group.convergedReason}</Badge>
            )}
          </div>
          <div className="mt-1 text-[11px] text-ink-500">
            started {new Date(group.startedAt).toLocaleString()} ·{" "}
            {group.rounds.length} round
            {group.rounds.length === 1 ? "" : "s"}
            {group.finalVersion != null && (
              <>
                {" "}
                · final v{group.finalVersion}
              </>
            )}
          </div>
        </div>
        <span
          aria-hidden
          className={clsx(
            "shrink-0 text-base text-ink-400 transition-transform",
            open ? "rotate-90" : "rotate-0",
          )}
        >
          ▸
        </span>
      </button>
      {open && (
        <div className="mt-3 space-y-3">
          <SessionRoundsChart rounds={group.rounds} />
          <RoundsList rounds={group.rounds} />
          {group.status === "converged" && (
            <DownstreamImpactBanner
              downstreamStatus={group.downstreamStatus}
            />
          )}
        </div>
      )}
    </li>
  );
}

function SessionRoundsChart({ rounds }: { rounds: IterationView[] }) {
  const width = 480;
  const height = 120;
  const padX = 28;
  const padY = 20;
  const barGap = 6;
  const innerW = width - padX * 2;
  const innerH = height - padY * 2;
  const barW = Math.max(
    6,
    (innerW - barGap * (rounds.length - 1)) / Math.max(1, rounds.length),
  );
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="weighted score per round for this session"
      className="w-full"
    >
      <line
        x1={padX}
        x2={width - padX}
        y1={height - padY}
        y2={height - padY}
        stroke="currentColor"
        className="text-ink-200"
      />
      {rounds.map((it, idx) => {
        const score = Math.max(0, Math.min(1, it.weighted_score));
        const x = padX + idx * (barW + barGap);
        const barH = score * innerH;
        const y = height - padY - barH;
        return (
          <g key={it.id}>
            <rect
              x={x}
              y={y}
              width={barW}
              height={barH}
              rx={2}
              className={
                it.is_baseline ? "fill-ink-400" : "fill-brand-500"
              }
            >
              <title>
                round {it.round} — {(score * 100).toFixed(0)}%
                {it.is_baseline ? " (baseline)" : ""}
              </title>
            </rect>
            <text
              x={x + barW / 2}
              y={height - padY + 12}
              textAnchor="middle"
              className="fill-ink-500 text-[10px]"
            >
              r{it.round}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function RoundsList({ rounds }: { rounds: IterationView[] }) {
  return (
    <ul className="divide-y divide-ink-100 rounded-md border border-ink-100">
      {rounds.map((it) => (
        <li
          key={it.id}
          className="flex items-center justify-between gap-3 px-3 py-2 text-[11px]"
        >
          <div>
            <span className="font-mono text-ink-700">round {it.round}</span>
            {it.is_baseline && (
              <span className="ml-2 text-ink-500">baseline</span>
            )}
            {it.revise_mode && !it.is_baseline && (
              <span className="ml-2 text-ink-500">{it.revise_mode}</span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-ink-700">
              {(it.weighted_score * 100).toFixed(0)}%
            </span>
            {it.new_version != null && (
              <Badge tone="brand">v{it.new_version}</Badge>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}
