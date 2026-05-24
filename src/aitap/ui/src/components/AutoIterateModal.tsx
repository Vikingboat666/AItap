/**
 * Auto-iterate launch modal — Playground entry point for `POST /api/iterate`.
 *
 * The modal owns three concerns:
 *
 *   1. Mode toggle (`auto` / `guided` / `manual`) plus the
 *      mode-specific inputs:
 *      - auto: no extras
 *      - guided: a free-text "instruction" string
 *      - manual: a per-round-2 prompt body (the design doc commits
 *        to multi-round manual as a follow-up; for M4 we surface a
 *        single round-2 input keyed at `manual_revisions[2]`).
 *   2. Optional collapsible convergence-config editor — backed by
 *      `ConvergenceConfigForm`. Hidden by default to keep the form
 *      light.
 *   3. The "Start auto-iterate" button — disabled unless the mode-
 *      specific input is filled in. On submit, dispatches the POST and
 *      hands the resulting `session_id` to `onStart` so the parent can
 *      switch its view (typically to `<IterationProgress />`).
 *
 * Validation is purely client-side here. The route layer re-validates
 * mode preconditions and returns 400 on a malformed payload, but we
 * prefer a disabled button + inline hint over a round-trip error.
 *
 * The modal renders as an `aria-modal` dialog so Testing Library
 * queries by role catch it without DOM jitter.
 */

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";

import { IterateService } from "../api/generated";
import type {
  ConvergenceConfig,
  IterateSessionRequest,
  IterateSessionResponse,
} from "../api/generated";
import { Badge, Card } from "./primitives";
import {
  ConvergenceConfigForm,
  DEFAULT_CONVERGENCE_CONFIG,
} from "./ConvergenceConfigForm";
import { clsx } from "../lib/clsx";

export type AutoIterateMode = "auto" | "guided" | "manual";

const MODE_LABEL_KEY: Record<AutoIterateMode, string> = {
  auto: "iterate.modeAuto",
  guided: "iterate.modeGuided",
  manual: "iterate.modeManual",
};

export interface AutoIterateModalProps {
  /** Prompt id to iterate. Disabled when null. */
  promptId: string | null;
  /**
   * Optional initial seed for the dataset id input. The user can always
   * edit the value after the modal opens; we no longer accept a fixed
   * `datasetId` prop because the dataset must match an existing file at
   * `.aitap/datasets/<name>.cases.jsonl` — silently substituting the
   * prompt id (the previous fallback) produced empty cases lists and a
   * loop that "converged" with zero scores. Treat this as a default
   * only; the canonical value lives in modal state.
   */
  initialDatasetId?: string | null;
  /** Close (cancel) the modal without starting. */
  onClose: () => void;
  /** Fires with the freshly-minted session response on success. */
  onStart: (session: IterateSessionResponse) => void;
}

export function AutoIterateModal({
  promptId,
  initialDatasetId,
  onClose,
  onStart,
}: AutoIterateModalProps) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<AutoIterateMode>("auto");
  const [instruction, setInstruction] = useState("");
  const [manualText, setManualText] = useState("");
  // Dataset id is now a first-class form field. Default to "" so the
  // Start button stays disabled until the user types a real value;
  // `initialDatasetId` is only used when the caller (e.g. a dataset
  // picker page) wants to pre-populate the field.
  const [datasetId, setDatasetId] = useState<string>(initialDatasetId ?? "");
  const [showConvergence, setShowConvergence] = useState(false);
  const [convergence, setConvergence] = useState<ConvergenceConfig>(
    DEFAULT_CONVERGENCE_CONFIG,
  );

  const trimmedDatasetId = datasetId.trim();

  const startMutation = useMutation({
    mutationFn: async () => {
      if (!promptId || trimmedDatasetId.length === 0) {
        throw new Error(t("iterate.errorPromptDataset"));
      }
      const requestBody: IterateSessionRequest = {
        prompt_id: promptId,
        dataset_id: trimmedDatasetId,
        mode,
        instruction: mode === "guided" ? instruction.trim() : null,
        manual_revisions:
          mode === "manual" && manualText.trim()
            ? { "2": manualText }
            : null,
        convergence,
      };
      return IterateService.startIterateSessionApiIteratePost({ requestBody });
    },
    onSuccess: (session) => {
      onStart(session);
    },
  });

  // Mode-specific input validation: empty trimmed inputs disable Start.
  const guidedReady = mode !== "guided" || instruction.trim().length > 0;
  const manualReady = mode !== "manual" || manualText.trim().length > 0;
  const datasetReady = trimmedDatasetId.length > 0;
  const canStart =
    !!promptId &&
    datasetReady &&
    guidedReady &&
    manualReady &&
    !startMutation.isPending;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={t("iterate.launchLabel")}
      className="fixed inset-0 z-40 flex items-center justify-center bg-ink-900/40 px-4 py-8"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl overflow-hidden rounded-lg bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between border-b border-ink-100 px-4 py-3">
          <div>
            <div className="text-sm font-semibold text-ink-800">
              {t("iterate.launchTitle")}
            </div>
            <div className="mt-0.5 text-xs text-ink-500">
              {t("iterate.launchSubtitle")}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md bg-ink-100 px-2 py-1 text-xs text-ink-700 hover:bg-ink-200"
          >
            {t("common.close")}
          </button>
        </div>

        <div className="space-y-4 px-4 py-4">
          <section>
            <label
              htmlFor="auto-iterate-dataset"
              className="mb-1 block text-[11px] uppercase text-ink-400"
            >
              {t("iterate.dataset")}
            </label>
            <input
              id="auto-iterate-dataset"
              type="text"
              value={datasetId}
              onChange={(e) => setDatasetId(e.target.value)}
              placeholder={t("iterate.datasetPlaceholder")}
              className="w-full rounded-md border border-ink-200 px-2 py-1 text-xs focus:border-brand-500 focus:outline-none"
            />
            <div className="mt-1 text-[10px] italic text-ink-500">
              {t("iterate.datasetHintPrefix")}{" "}
              <code className="font-mono text-ink-700">
                .aitap/datasets/&lt;name&gt;.cases.jsonl
              </code>
              {t("iterate.datasetHintMiddle")}{" "}
              <code className="font-mono text-ink-700">aitap</code>{" "}
              {t("iterate.datasetHintSuffix")}
            </div>
            {!datasetReady && (
              <div className="mt-1 text-[11px] italic text-amber-700">
                {t("iterate.datasetRequired")}
              </div>
            )}
          </section>

          <section>
            <div className="mb-2 text-[11px] uppercase text-ink-400">
              {t("iterate.mode")}
            </div>
            <div className="flex gap-1">
              {(["auto", "guided", "manual"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  data-mode={m}
                  onClick={() => setMode(m)}
                  className={clsx(
                    "rounded-md px-2.5 py-1 text-xs",
                    mode === m
                      ? "bg-brand-600 text-white"
                      : "bg-ink-100 text-ink-700 hover:bg-ink-200",
                  )}
                >
                  {t(MODE_LABEL_KEY[m])}
                </button>
              ))}
            </div>
            <div className="mt-2 text-[11px] text-ink-500">
              {modeHint(mode, t)}
            </div>
          </section>

          {mode === "guided" && (
            <section>
              <label
                htmlFor="auto-iterate-instruction"
                className="mb-1 block text-[11px] uppercase text-ink-400"
              >
                {t("iterate.instruction")}
              </label>
              <input
                id="auto-iterate-instruction"
                type="text"
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                placeholder={t("iterate.instructionPlaceholder")}
                className="w-full rounded-md border border-ink-200 px-2 py-1 text-xs focus:border-brand-500 focus:outline-none"
              />
              {!guidedReady && (
                <div className="mt-1 text-[11px] italic text-amber-700">
                  {t("iterate.instructionRequired")}
                </div>
              )}
            </section>
          )}

          {mode === "manual" && (
            <section>
              <label
                htmlFor="auto-iterate-manual"
                className="mb-1 block text-[11px] uppercase text-ink-400"
              >
                {t("iterate.round2PromptText")}
              </label>
              <textarea
                id="auto-iterate-manual"
                value={manualText}
                onChange={(e) => setManualText(e.target.value)}
                rows={6}
                placeholder={t("iterate.manualPlaceholder")}
                className="w-full rounded-md border border-ink-200 px-2 py-1 font-mono text-[11px] focus:border-brand-500 focus:outline-none"
              />
              {!manualReady && (
                <div className="mt-1 text-[11px] italic text-amber-700">
                  {t("iterate.manualRequired")}
                </div>
              )}
              <div className="mt-1 text-[10px] italic text-ink-400">
                {t("iterate.manualHint")}
              </div>
            </section>
          )}

          <section>
            <button
              type="button"
              onClick={() => setShowConvergence((v) => !v)}
              aria-expanded={showConvergence}
              className="text-[11px] text-ink-600 hover:text-ink-900"
            >
              {showConvergence
                ? t("iterate.convergenceExpanded")
                : t("iterate.convergenceCollapsed")}
            </button>
            {showConvergence && (
              <div className="mt-2 rounded-md border border-ink-100 bg-ink-50/40 p-3">
                <ConvergenceConfigForm
                  value={convergence}
                  onChange={setConvergence}
                  disabled={startMutation.isPending}
                />
              </div>
            )}
          </section>

          {startMutation.error && (
            <Card className="border-rose-200 bg-rose-50/50">
              <div className="px-3 py-2 text-[11px] text-rose-700">
                {(startMutation.error as Error).message}
              </div>
            </Card>
          )}
        </div>

        <div className="flex items-center justify-between border-t border-ink-100 bg-ink-50/60 px-4 py-3">
          <div className="flex items-center gap-2 text-[11px] text-ink-500">
            {!promptId || !datasetReady ? (
              <Badge tone="warn">{t("iterate.promptDatasetRequired")}</Badge>
            ) : (
              <Badge tone="brand">{t(MODE_LABEL_KEY[mode])}</Badge>
            )}
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md bg-ink-100 px-3 py-1.5 text-xs text-ink-700 hover:bg-ink-200"
            >
              {t("common.cancel")}
            </button>
            <button
              type="button"
              disabled={!canStart}
              onClick={() => startMutation.mutate()}
              title={
                !datasetReady
                  ? t("iterate.provideDatasetName")
                  : t("iterate.startSessionTitle")
              }
              className={clsx(
                "rounded-md px-3 py-1.5 text-xs font-medium text-white",
                canStart
                  ? "bg-brand-600 hover:bg-brand-700"
                  : "cursor-not-allowed bg-ink-200",
              )}
            >
              {startMutation.isPending
                ? t("iterate.starting")
                : t("iterate.startAutoIterate")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function modeHint(mode: AutoIterateMode, t: TFunction): string {
  switch (mode) {
    case "auto":
      return t("iterate.modeHintAuto");
    case "guided":
      return t("iterate.modeHintGuided");
    case "manual":
      return t("iterate.modeHintManual");
  }
}
