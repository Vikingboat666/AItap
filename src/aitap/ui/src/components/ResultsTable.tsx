/**
 * Per-case results renderer used by Playground (live run output).
 * Renders one row per `RunOutput` and exposes optional thumbs/critique
 * controls so the parent owns mutation logic but the visual stays
 * consistent.
 *
 * Reuse status: this component is *currently* only imported by
 * Playground. It was designed to be lifted into History's diff modal
 * for read-only per-case output panes, but the `/api/history`
 * endpoint does not yet expose per-case outputs (it returns only
 * version metadata: `version`, `avg_score`, `note`, `parent_version`).
 * Once that endpoint surfaces per-case outputs (M4), History should
 * import ResultsTable for its diff panes; until then the History
 * DiffModal renders metadata-only `DiffPane` components.
 *
 * The component stays intentionally presentational — it doesn't
 * import react-query directly. The Playground attaches an optimistic
 * feedback mutation and passes the loading flag through; this keeps
 * the component drop-in usable for read-only contexts (e.g., History)
 * later by simply omitting the `onFeedback` prop.
 */

import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Badge, Card, CardHeader } from "./primitives";
import { clsx } from "../lib/clsx";
import type { RunOutput } from "../api/generated";

export interface FeedbackSubmission {
  caseIndex: number;
  rating: -1 | 0 | 1 | null;
  critique?: string | null;
}

export interface ResultsTableProps {
  outputs: RunOutput[];
  /** Cost surfaced in the footer; omitted when undefined. */
  costUsd?: number | null;
  /** Per-case rating to highlight (typically the optimistic cache value). */
  ratingByCase?: Record<number, -1 | 0 | 1 | null>;
  /** Called when the user clicks a feedback button. Omit for read-only views. */
  onFeedback?: (submission: FeedbackSubmission) => void;
  /** Disable the feedback buttons (e.g., while parent mutation pending). */
  feedbackDisabled?: boolean;
  /** Card heading override; defaults to "results". */
  title?: string;
  /** Card subtitle (e.g., "run #1234"). */
  subtitle?: string;
  /** Empty-state body when outputs.length === 0. */
  emptyHint?: string;
}

export function ResultsTable({
  outputs,
  costUsd,
  ratingByCase,
  onFeedback,
  feedbackDisabled = false,
  title,
  subtitle,
  emptyHint,
}: ResultsTableProps) {
  const { t } = useTranslation();
  return (
    <Card>
      <CardHeader
        title={title ?? t("results.defaultTitle")}
        subtitle={subtitle}
        action={
          costUsd != null ? (
            <Badge tone="neutral">
              {t("results.cost", { amount: costUsd.toFixed(4) })}
            </Badge>
          ) : null
        }
      />
      <div className="px-4 py-3">
        {outputs.length === 0 ? (
          <div className="rounded-md border border-dashed border-ink-200 px-3 py-6 text-center text-xs italic text-ink-400">
            {emptyHint ?? t("results.defaultEmptyHint")}
          </div>
        ) : (
          <ul className="space-y-3">
            {outputs.map((output) => (
              <ResultRow
                key={output.case_index}
                output={output}
                rating={ratingByCase?.[output.case_index] ?? null}
                onFeedback={onFeedback}
                feedbackDisabled={feedbackDisabled}
              />
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}

interface ResultRowProps {
  output: RunOutput;
  rating: -1 | 0 | 1 | null;
  onFeedback?: (submission: FeedbackSubmission) => void;
  feedbackDisabled: boolean;
}

function ResultRow({
  output,
  rating,
  onFeedback,
  feedbackDisabled,
}: ResultRowProps) {
  const { t } = useTranslation();
  const [critiqueDraft, setCritiqueDraft] = useState("");
  const [critiqueOpen, setCritiqueOpen] = useState(false);

  const isErrored = !!output.error;
  return (
    <li
      className={clsx(
        "rounded-md border p-3",
        isErrored
          ? "border-rose-200 bg-rose-50/40"
          : "border-ink-100 bg-ink-50/40",
      )}
    >
      <div className="mb-1 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Badge tone={isErrored ? "warn" : "neutral"}>
            {t("results.caseBadge", { index: output.case_index })}
          </Badge>
          {output.intermediate && (
            <span className="text-[11px] text-ink-500">
              {t("results.intermediateNodes", {
                count: Object.keys(output.intermediate).length,
              })}
            </span>
          )}
        </div>
        {onFeedback && (
          <FeedbackButtons
            caseIndex={output.case_index}
            rating={rating}
            disabled={feedbackDisabled}
            onFeedback={onFeedback}
            onToggleCritique={() => setCritiqueOpen((v) => !v)}
            critiqueOpen={critiqueOpen}
          />
        )}
      </div>

      {output.text && (
        <pre className="whitespace-pre-wrap break-words font-mono text-xs text-ink-700">
          {output.text}
        </pre>
      )}
      {output.image_path && (
        <div className="mt-2 font-mono text-[11px] text-ink-500">
          {t("results.image", { path: output.image_path })}
        </div>
      )}
      {output.error && (
        <pre className="whitespace-pre-wrap break-words font-mono text-xs text-rose-600">
          {output.error}
        </pre>
      )}

      {critiqueOpen && onFeedback && (
        <form
          className="mt-2 space-y-2"
          onSubmit={(e) => {
            e.preventDefault();
            onFeedback({
              caseIndex: output.case_index,
              rating,
              critique: critiqueDraft.trim() || null,
            });
            setCritiqueDraft("");
            setCritiqueOpen(false);
          }}
        >
          <textarea
            value={critiqueDraft}
            onChange={(e) => setCritiqueDraft(e.target.value)}
            rows={3}
            placeholder={t("results.critiquePlaceholder")}
            className="w-full rounded-md border border-ink-200 px-2 py-1 font-mono text-xs focus:border-brand-500 focus:outline-none"
          />
          <button
            type="submit"
            disabled={feedbackDisabled || !critiqueDraft.trim()}
            className="rounded-md bg-brand-600 px-2 py-1 text-[11px] font-medium text-white disabled:cursor-not-allowed disabled:bg-ink-200"
          >
            {t("results.submitCritique")}
          </button>
        </form>
      )}
    </li>
  );
}

interface FeedbackButtonsProps {
  caseIndex: number;
  rating: -1 | 0 | 1 | null;
  disabled: boolean;
  onFeedback: (submission: FeedbackSubmission) => void;
  onToggleCritique: () => void;
  critiqueOpen: boolean;
}

function FeedbackButtons({
  caseIndex,
  rating,
  disabled,
  onFeedback,
  onToggleCritique,
  critiqueOpen,
}: FeedbackButtonsProps) {
  const { t } = useTranslation();
  const baseBtn =
    "rounded-md px-2 py-1 text-xs font-medium disabled:cursor-not-allowed disabled:opacity-50";
  return (
    <div className="flex items-center gap-1">
      <button
        type="button"
        disabled={disabled}
        aria-label={t("results.thumbsUp")}
        aria-pressed={rating === 1}
        onClick={() => onFeedback({ caseIndex, rating: rating === 1 ? null : 1 })}
        className={clsx(
          baseBtn,
          rating === 1
            ? "bg-emerald-100 text-emerald-700"
            : "bg-ink-100 text-ink-600 hover:bg-ink-200",
        )}
      >
        {t("results.plusOne")}
      </button>
      <button
        type="button"
        disabled={disabled}
        aria-label={t("results.thumbsDown")}
        aria-pressed={rating === -1}
        onClick={() =>
          onFeedback({ caseIndex, rating: rating === -1 ? null : -1 })
        }
        className={clsx(
          baseBtn,
          rating === -1
            ? "bg-rose-100 text-rose-700"
            : "bg-ink-100 text-ink-600 hover:bg-ink-200",
        )}
      >
        {t("results.minusOne")}
      </button>
      <button
        type="button"
        onClick={onToggleCritique}
        className={clsx(
          baseBtn,
          critiqueOpen
            ? "bg-brand-100 text-brand-700"
            : "bg-ink-100 text-ink-600 hover:bg-ink-200",
        )}
      >
        {t("results.critique")}
      </button>
    </div>
  );
}
