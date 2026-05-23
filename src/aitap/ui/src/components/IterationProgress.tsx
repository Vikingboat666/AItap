/**
 * Iteration session progress view.
 *
 * Polls `GET /api/iterations/{session_id}/latest` while the session is
 * `running`, then fetches the full session via
 * `GET /api/iterations/{session_id}` once a terminal status is observed
 * — that gives the per-round score chart something to render without
 * having to maintain a client-side accumulator across polls.
 *
 * Status states are derived from `IterateSessionResponse.status`:
 *
 *   - `running`   → spinner + current round badge + live score bars
 *   - `converged` → success header + converged-reason copy + final
 *                   version badge + bars
 *   - `failed`    → red error banner + critique_text (if any), no bars
 *
 * Polling cadence is 1500ms — fast enough to feel reactive on a 2-3s
 * LLM round, slow enough to avoid hammering SQLite while a background
 * task writes the next iteration row. Polling stops as soon as the
 * *session* query reports `converged` or `failed`; the `latest` query
 * piggybacks on that signal via its `enabled` flag, so a failed-via-
 * placeholder session (where `/latest` 404s forever) still terminates
 * cleanly instead of polling indefinitely.
 *
 * Tests bypass real polling by mocking the session endpoint to return a
 * terminal status on the first call; the component degrades to a single
 * fetch in that case. See `IterationProgress.test.tsx`.
 */

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { IterateService } from "../api/generated";
import type {
  IterateSessionResponse,
  IterationView,
} from "../api/generated";
import { Badge, Card, CardHeader } from "./primitives";
import { DownstreamImpactBanner } from "./DownstreamImpactBanner";
import { clsx } from "../lib/clsx";

/**
 * Default polling interval (ms). Exposed as a prop so tests can shrink
 * it to a near-zero value without going through React Query's internal
 * scheduling.
 */
export const DEFAULT_POLL_INTERVAL_MS = 1500;

/**
 * Copy + tone for the five `converged_reason` enums. Centralising the
 * mapping here means a future spec rename (or a new reason) is a single
 * edit; the rest of the file just looks up by key.
 */
const REASON_COPY: Record<
  string,
  { title: string; detail: string; tone: "ok" | "warn" | "err" }
> = {
  delta: {
    title: "score improved past the delta threshold",
    detail:
      "weighted score rose above baseline by the configured delta — the iteration succeeded.",
    tone: "ok",
  },
  absolute: {
    title: "absolute threshold reached",
    detail:
      "an opt-in non-negotiable dimension (e.g. safety) cleared its absolute bar.",
    tone: "ok",
  },
  stagnation: {
    title: "score plateaued",
    detail:
      "round-over-round delta dropped below the stagnation epsilon — further rounds unlikely to help.",
    tone: "warn",
  },
  max_rounds: {
    title: "max rounds reached",
    detail:
      "the loop ran the configured number of rounds without crossing the delta threshold.",
    tone: "warn",
  },
  critic_failed: {
    title: "the critic failed",
    detail:
      "the rewriter LLM call could not produce a valid revision — see critique text below for the failure detail.",
    tone: "err",
  },
};

export interface IterationProgressProps {
  sessionId: string;
  /** Cap shown in the "round N of M" header; defaults to 5 (loop default). */
  maxRounds?: number;
  /** Override the polling interval (e.g. zero in tests). */
  pollIntervalMs?: number;
  /** Optional callback fired once a terminal status is observed. */
  onTerminal?: (session: IterateSessionResponse) => void;
}

export function IterationProgress({
  sessionId,
  maxRounds = 5,
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
  onTerminal,
}: IterationProgressProps) {
  // The session query is the canonical source of `status` — both the
  // happy path and the failed-via-placeholder path land here. Polling
  // stops as soon as the session reports a terminal status.
  const sessionQ = useQuery<IterateSessionResponse>({
    queryKey: ["iteration-session", sessionId],
    queryFn: () =>
      IterateService.getIterateSessionApiIterationsSessionIdGet({ sessionId }),
    // Always run once on mount so we have a status from the start (the
    // POST response is also a session response but the parent may not
    // have threaded it through). Re-run on every poll so we catch the
    // moment the background task completes.
    enabled: true,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === "converged" || status === "failed") return false;
      return pollIntervalMs;
    },
    refetchOnWindowFocus: false,
  });

  // The "latest" poll is the live row tick, used to drive the spinner
  // and any per-round summary. It piggybacks on the session query for
  // its stop condition — when `sessionQ.data?.status` is terminal, we
  // disable this query entirely. This avoids the failed-via-placeholder
  // trap where `/latest` 404s forever (placeholder filtered out) and a
  // `converged_reason`-only check would never stop polling.
  const sessionStatus = sessionQ.data?.status;
  const isTerminalStatus =
    sessionStatus === "converged" || sessionStatus === "failed";
  // The hook return is intentionally discarded — we keep the query
  // registered so it stays warm in the cache for any sibling component
  // that mounts later, but the bars + header read off `sessionQ` only.
  useQuery<IterationView | null>({
    queryKey: ["iteration-latest", sessionId],
    queryFn: async () => {
      try {
        return await IterateService.getIterateSessionLatestApiIterationsSessionIdLatestGet(
          { sessionId },
        );
      } catch {
        // The endpoint 404s while the placeholder is the only row.
        // We treat that as "no data yet" — keep polling.
        return null;
      }
    },
    enabled: !isTerminalStatus,
    refetchInterval: isTerminalStatus ? false : pollIntervalMs,
    refetchOnWindowFocus: false,
  });

  // Tell the parent we have hit a terminal status (so it can switch the
  // outer button label, dismiss spinners, etc). Effect rather than
  // onSuccess because we want the callback to fire exactly once per
  // terminal observation; effect-with-ref dedupes.
  useNotifyTerminal(sessionQ.data, onTerminal);

  // The bars feed off the full session iterations list — `latest`
  // alone is one row, useless for trend rendering.
  const iterations = useMemo<IterationView[]>(
    () => sessionQ.data?.iterations ?? [],
    [sessionQ.data?.iterations],
  );

  const status = sessionQ.data?.status ?? "running";
  const lastIteration = iterations[iterations.length - 1] ?? null;
  const downstreamStatus =
    status === "converged" ? lastIteration?.downstream_status ?? null : null;

  // Banner dismissal is client-side per Decision 4 (M5 persists this).
  const [bannerDismissed, setBannerDismissed] = useState(false);

  return (
    <div className="space-y-3" data-testid="iteration-progress">
      <ProgressHeader
        status={status}
        currentRound={lastIteration?.round ?? 0}
        maxRounds={maxRounds}
        finalVersion={sessionQ.data?.final_version ?? null}
        convergedReason={sessionQ.data?.converged_reason ?? null}
      />

      {status === "failed" ? (
        <FailureBanner
          critique={lastIteration?.critique_text ?? null}
          reason={sessionQ.data?.converged_reason ?? "critic_failed"}
        />
      ) : (
        <IterationBars iterations={iterations} />
      )}

      {status !== "failed" &&
        sessionQ.data?.converged_reason &&
        REASON_COPY[sessionQ.data.converged_reason] && (
          <ConvergedSummary reason={sessionQ.data.converged_reason} />
        )}

      <DownstreamImpactBanner
        downstreamStatus={downstreamStatus}
        dismissed={bannerDismissed}
        onDismiss={() => setBannerDismissed(true)}
      />
    </div>
  );
}

/**
 * Fire `onTerminal` exactly once per session, the first time we observe
 * a terminal status. The hook owns its own "have we announced yet"
 * cursor; callers don't need to know whether it fired.
 */
function useNotifyTerminal(
  session: IterateSessionResponse | undefined,
  onTerminal: ((s: IterateSessionResponse) => void) | undefined,
): void {
  const [announced, setAnnounced] = useState<
    IterateSessionResponse["status"] | null
  >(null);

  useEffect(() => {
    if (!session) return;
    if (session.status === "running") return;
    if (announced === session.status) return;
    setAnnounced(session.status);
    onTerminal?.(session);
  }, [session, announced, onTerminal]);
}

interface ProgressHeaderProps {
  status: IterateSessionResponse["status"];
  currentRound: number;
  maxRounds: number;
  finalVersion: number | null;
  convergedReason: string | null;
}

function ProgressHeader({
  status,
  currentRound,
  maxRounds,
  finalVersion,
  convergedReason,
}: ProgressHeaderProps) {
  let subtitle: string;
  if (status === "running") {
    subtitle = `round ${currentRound || 1} of ${maxRounds} — running…`;
  } else if (status === "converged") {
    subtitle = `${currentRound} round${currentRound === 1 ? "" : "s"} · ${
      convergedReason ?? "converged"
    }`;
  } else {
    subtitle = "session failed — see error detail below";
  }

  return (
    <Card>
      <CardHeader
        title="auto-iterate"
        subtitle={subtitle}
        action={
          <div className="flex items-center gap-2">
            {status === "running" && (
              <Spinner aria-label="iteration in progress" />
            )}
            <StatusBadge status={status} />
            {finalVersion != null && (
              <Badge tone="brand">v{finalVersion}</Badge>
            )}
          </div>
        }
      />
    </Card>
  );
}

function StatusBadge({
  status,
}: {
  status: IterateSessionResponse["status"];
}) {
  if (status === "running") return <Badge tone="warn">running</Badge>;
  if (status === "converged") return <Badge tone="ok">converged</Badge>;
  // Use the rose `err` tone so the inline badge matches the rose
  // FailureBanner's severity — previously we rendered amber/warn here
  // while the banner was rose, giving conflicting affordances.
  return <Badge tone="err">failed</Badge>;
}

function Spinner({ "aria-label": label }: { "aria-label": string }) {
  return (
    <span
      role="status"
      aria-label={label}
      className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-amber-300 border-t-transparent"
    />
  );
}

interface FailureBannerProps {
  critique: string | null;
  reason: string;
}

function FailureBanner({ critique, reason }: FailureBannerProps) {
  const copy = REASON_COPY[reason] ?? REASON_COPY.critic_failed;
  return (
    <Card
      role="alert"
      aria-live="assertive"
      className="border-rose-200 bg-rose-50 text-rose-800"
    >
      <div className="space-y-2 px-4 py-3 text-xs">
        <div className="font-medium">{copy.title}</div>
        <div>{copy.detail}</div>
        {critique && (
          <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-md bg-white/70 p-2 text-[11px] font-mono text-rose-900">
            {critique}
          </pre>
        )}
      </div>
    </Card>
  );
}

function ConvergedSummary({ reason }: { reason: string }) {
  const copy = REASON_COPY[reason];
  if (!copy) return null;
  const tone =
    copy.tone === "ok"
      ? "border-emerald-200 bg-emerald-50 text-emerald-800"
      : "border-amber-200 bg-amber-50 text-amber-800";
  return (
    <Card className={clsx("border", tone)}>
      <div className="space-y-1 px-4 py-3 text-xs">
        <div className="font-medium">{copy.title}</div>
        <div>{copy.detail}</div>
      </div>
    </Card>
  );
}

interface IterationBarsProps {
  iterations: IterationView[];
}

/**
 * Inline SVG bar chart of the per-round weighted score. Same visual
 * language as History.tsx's ChartBars — we considered factoring out a
 * shared component but the data shapes diverge enough (HistoryEntry vs.
 * IterationView) that two small functions read clearer than one
 * generic.
 */
function IterationBars({ iterations }: IterationBarsProps) {
  if (iterations.length === 0) {
    return (
      <Card>
        <CardHeader title="rounds" />
        <div className="px-4 py-3 text-xs italic text-ink-400">
          waiting for the first round to land…
        </div>
      </Card>
    );
  }

  const width = 600;
  const height = 160;
  const padX = 32;
  const padY = 28;
  const barGap = 8;
  const innerW = width - padX * 2;
  const innerH = height - padY * 2;
  const barW = Math.max(
    8,
    (innerW - barGap * (iterations.length - 1)) /
      Math.max(1, iterations.length),
  );

  return (
    <Card>
      <CardHeader
        title="round scores"
        subtitle={`${iterations.length} round${
          iterations.length === 1 ? "" : "s"
        } recorded`}
      />
      <div className="px-4 py-4">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label="weighted score per round"
          className="w-full"
          data-testid="iteration-bars"
        >
          <line
            x1={padX}
            x2={width - padX}
            y1={height - padY}
            y2={height - padY}
            stroke="currentColor"
            className="text-ink-200"
            strokeWidth={1}
          />
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
          {iterations.map((it, idx) => {
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
                  data-round={it.round}
                  data-score={score}
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
      </div>
    </Card>
  );
}
