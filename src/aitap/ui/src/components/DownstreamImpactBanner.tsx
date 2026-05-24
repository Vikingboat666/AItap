/**
 * Downstream-impact banner (Decision 4 of the Wave 4 design doc).
 *
 * Renders when an iteration session committed a new prompt version and
 * the iterated prompt is a pipeline node — server stamps the last
 * iteration row's `downstream_status` with `{node_id: "unverified"}` for
 * every downstream consumer.
 *
 * UX behaviour:
 *   - Warn-by-default: yellow/amber styling, NOT red — this is a
 *     warning, not an error.
 *   - Only nodes currently in the `unverified` state are surfaced. Once
 *     a node flips to `verified` / `regressed` / `improved` (via the
 *     M5 re-run flow), it drops out of the count.
 *   - Three actions: **Skip** (client-side dismiss only — no backend
 *     write today, M5 will persist), **Re-run all** and **Re-run
 *     selected** as M5 placeholders with explanatory titles so users
 *     understand why they're disabled.
 *   - Skip is a client-side state change owned by the parent (so the
 *     banner stays dismissed for the lifetime of the page session).
 *
 * Returns `null` when there is nothing to surface (no downstream
 * status, all nodes resolved, or the user already skipped). This lets
 * callers drop the component in unconditionally — it self-hides.
 */

import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { Card } from "./primitives";
import { clsx } from "../lib/clsx";

export interface DownstreamImpactBannerProps {
  /**
   * The `downstream_status` map from the latest iteration row.
   * `null` / `undefined` means the prompt is not a pipeline node, so
   * the banner is omitted entirely.
   */
  downstreamStatus: Record<string, string> | null | undefined;
  /**
   * Whether the user has dismissed the banner this session. The parent
   * owns this flag so dismissing one place propagates — re-rendering
   * the banner elsewhere doesn't resurrect it.
   */
  dismissed?: boolean;
  /** Fired when the user clicks "Skip". */
  onDismiss?: () => void;
  /** Fired when the user clicks "Re-run all" (M5 placeholder). */
  onRerunAll?: () => void;
  /** Fired when the user clicks "Re-run selected" (M5 placeholder). */
  onRerunSelected?: () => void;
  /** Override class on the wrapper card (e.g. layout-specific margin). */
  className?: string;
}

/**
 * Nodes still requiring verification — the only state we actually
 * surface. Verified / regressed / improved are M5 results; they show
 * elsewhere (per-node badges) once that work lands.
 */
function unverifiedNodes(
  downstreamStatus: Record<string, string> | null | undefined,
): string[] {
  if (!downstreamStatus) return [];
  return Object.entries(downstreamStatus)
    .filter(([, status]) => status === "unverified")
    .map(([nodeId]) => nodeId)
    .sort();
}

export function DownstreamImpactBanner({
  downstreamStatus,
  dismissed = false,
  onDismiss,
  onRerunAll,
  onRerunSelected,
  className,
}: DownstreamImpactBannerProps) {
  const { t } = useTranslation();
  const unverified = useMemo(
    () => unverifiedNodes(downstreamStatus),
    [downstreamStatus],
  );

  // Self-hide when there is nothing to warn about. Returning null is
  // the simplest way to keep call sites unconditional — they can drop
  // <DownstreamImpactBanner ... /> into JSX without an outer guard.
  if (dismissed || unverified.length === 0) {
    return null;
  }

  const headline = t("downstream.headline", { count: unverified.length });
  const nodeList = unverified.join(", ");

  return (
    <Card
      role="status"
      aria-live="polite"
      className={clsx(
        "border-amber-200 bg-amber-50 text-amber-900",
        className,
      )}
    >
      <div className="space-y-2 px-4 py-3 text-xs">
        <div className="flex items-start gap-2">
          <span aria-hidden className="text-base leading-none">
            ⚠
          </span>
          <div>
            <div className="font-medium">{headline}</div>
            <div className="mt-0.5 text-[11px] text-amber-800">
              {t("downstream.affected")}{" "}
              <span className="font-mono">{nodeList}</span>
            </div>
            <div className="mt-1 text-[11px] text-amber-700">
              {t("downstream.warning")}
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 pl-6">
          <button
            type="button"
            onClick={onDismiss}
            className="rounded-md bg-white px-2 py-1 text-[11px] font-medium text-amber-800 ring-1 ring-amber-300 hover:bg-amber-100"
          >
            {t("downstream.skip")}
          </button>
          <button
            type="button"
            onClick={onRerunAll}
            disabled
            title={t("downstream.comingInM5")}
            className="cursor-not-allowed rounded-md bg-amber-200 px-2 py-1 text-[11px] font-medium text-amber-700 opacity-70"
          >
            {t("downstream.rerunAll")}
          </button>
          <button
            type="button"
            onClick={onRerunSelected}
            disabled
            title={t("downstream.comingInM5")}
            className="cursor-not-allowed rounded-md bg-amber-200 px-2 py-1 text-[11px] font-medium text-amber-700 opacity-70"
          >
            {t("downstream.rerunSelected")}
          </button>
        </div>
      </div>
    </Card>
  );
}
