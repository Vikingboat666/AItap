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

export interface AutoIterateModalProps {
  /** Prompt id to iterate. Disabled when null. */
  promptId: string | null;
  /** Dataset id to evaluate against. Disabled when null. */
  datasetId: string | null;
  /** Close (cancel) the modal without starting. */
  onClose: () => void;
  /** Fires with the freshly-minted session response on success. */
  onStart: (session: IterateSessionResponse) => void;
}

export function AutoIterateModal({
  promptId,
  datasetId,
  onClose,
  onStart,
}: AutoIterateModalProps) {
  const [mode, setMode] = useState<AutoIterateMode>("auto");
  const [instruction, setInstruction] = useState("");
  const [manualText, setManualText] = useState("");
  const [showConvergence, setShowConvergence] = useState(false);
  const [convergence, setConvergence] = useState<ConvergenceConfig>(
    DEFAULT_CONVERGENCE_CONFIG,
  );

  const startMutation = useMutation({
    mutationFn: async () => {
      if (!promptId || !datasetId) {
        throw new Error("prompt + dataset must be selected before starting");
      }
      const requestBody: IterateSessionRequest = {
        prompt_id: promptId,
        dataset_id: datasetId,
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
  const canStart =
    !!promptId &&
    !!datasetId &&
    guidedReady &&
    manualReady &&
    !startMutation.isPending;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="auto-iterate launch"
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
              auto-iterate
            </div>
            <div className="mt-0.5 text-xs text-ink-500">
              run a self-iteration session against the selected dataset
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md bg-ink-100 px-2 py-1 text-xs text-ink-700 hover:bg-ink-200"
          >
            close
          </button>
        </div>

        <div className="space-y-4 px-4 py-4">
          <section>
            <div className="mb-2 text-[11px] uppercase text-ink-400">
              mode
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
                  {m}
                </button>
              ))}
            </div>
            <div className="mt-2 text-[11px] text-ink-500">
              {modeHint(mode)}
            </div>
          </section>

          {mode === "guided" && (
            <section>
              <label
                htmlFor="auto-iterate-instruction"
                className="mb-1 block text-[11px] uppercase text-ink-400"
              >
                instruction
              </label>
              <input
                id="auto-iterate-instruction"
                type="text"
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                placeholder="e.g. tone should be more professional"
                className="w-full rounded-md border border-ink-200 px-2 py-1 text-xs focus:border-brand-500 focus:outline-none"
              />
              {!guidedReady && (
                <div className="mt-1 text-[11px] italic text-amber-700">
                  instruction is required for guided mode
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
                round 2 prompt text
              </label>
              <textarea
                id="auto-iterate-manual"
                value={manualText}
                onChange={(e) => setManualText(e.target.value)}
                rows={6}
                placeholder="the full new prompt body — replaces the baseline verbatim for round 2"
                className="w-full rounded-md border border-ink-200 px-2 py-1 font-mono text-[11px] focus:border-brand-500 focus:outline-none"
              />
              {!manualReady && (
                <div className="mt-1 text-[11px] italic text-amber-700">
                  manual mode needs the new prompt body
                </div>
              )}
              <div className="mt-1 text-[10px] italic text-ink-400">
                multi-round manual lands in a follow-up — for now this becomes
                round 2 only.
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
              {showConvergence ? "▾ convergence" : "▸ convergence"}
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
            {!promptId || !datasetId ? (
              <Badge tone="warn">prompt + dataset required</Badge>
            ) : (
              <Badge tone="brand">{mode}</Badge>
            )}
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md bg-ink-100 px-3 py-1.5 text-xs text-ink-700 hover:bg-ink-200"
            >
              cancel
            </button>
            <button
              type="button"
              disabled={!canStart}
              onClick={() => startMutation.mutate()}
              className={clsx(
                "rounded-md px-3 py-1.5 text-xs font-medium text-white",
                canStart
                  ? "bg-brand-600 hover:bg-brand-700"
                  : "cursor-not-allowed bg-ink-200",
              )}
            >
              {startMutation.isPending ? "starting…" : "start auto-iterate"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function modeHint(mode: AutoIterateMode): string {
  switch (mode) {
    case "auto":
      return "critic LLM rewrites freely based on judge feedback — fully automatic.";
    case "guided":
      return "critic LLM follows your direction — provide a single-sentence instruction.";
    case "manual":
      return "no LLM rewrite — you supply the new prompt body verbatim for round 2.";
  }
}
