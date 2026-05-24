/* eslint-disable react-refresh/only-export-components */
/**
 * Convergence-config editor for the Auto-iterate panel.
 *
 * Surfaces the four Decision 3 knobs as a compact inline form:
 *
 *   - `max_rounds`        — hard cap on round count
 *   - `delta_from_baseline` — score delta that signals convergence
 *   - `stagnation_window` — number of rounds in the stagnation window
 *   - `stagnation_epsilon` — round-over-round delta below which we plateau
 *
 * `absolute_threshold` is intentionally omitted from the UI — the design
 * doc lists it as an opt-in advanced setting and we don't want users to
 * accidentally pin an absolute score gate before they've established a
 * sensible baseline. It can be added later behind a "show advanced"
 * toggle without breaking this component's surface.
 *
 * The component is fully controlled — every field is a number, the
 * parent owns the state, and we never silently coerce empty strings to
 * NaN (a blank field falls back to `defaults`). This keeps the form
 * round-trippable so a user who clears a field can still hit "Start".
 */

import { useCallback } from "react";
import { useTranslation } from "react-i18next";

import type { ConvergenceConfig } from "../api/generated";

/**
 * The Pydantic defaults from `iterate/loop.py:ConvergenceConfig`. We
 * duplicate them here because the generated TS type marks every field
 * as optional (no default value lands in the OpenAPI schema). Keep in
 * sync with the backend if defaults shift.
 */
export const DEFAULT_CONVERGENCE_CONFIG: Required<
  Pick<
    ConvergenceConfig,
    "max_rounds" | "delta_from_baseline" | "stagnation_window" | "stagnation_epsilon"
  >
> = {
  max_rounds: 5,
  delta_from_baseline: 0.15,
  stagnation_window: 3,
  stagnation_epsilon: 0.02,
};

export interface ConvergenceConfigFormProps {
  value: ConvergenceConfig;
  onChange: (next: ConvergenceConfig) => void;
  /** When true, all inputs are disabled (e.g. session in flight). */
  disabled?: boolean;
}

export function ConvergenceConfigForm({
  value,
  onChange,
  disabled = false,
}: ConvergenceConfigFormProps) {
  const { t } = useTranslation();
  const update = useCallback(
    (patch: Partial<ConvergenceConfig>) => onChange({ ...value, ...patch }),
    [value, onChange],
  );

  return (
    <div className="space-y-3 text-xs">
      <NumberField
        label={t("convergence.maxRounds")}
        id="convergence-max-rounds"
        value={value.max_rounds ?? DEFAULT_CONVERGENCE_CONFIG.max_rounds}
        step={1}
        min={1}
        max={20}
        disabled={disabled}
        onChange={(n) => update({ max_rounds: n })}
        hint={t("convergence.maxRoundsHint")}
      />
      <NumberField
        label={t("convergence.deltaFromBaseline")}
        id="convergence-delta"
        value={
          value.delta_from_baseline ??
          DEFAULT_CONVERGENCE_CONFIG.delta_from_baseline
        }
        step={0.01}
        min={0}
        max={1}
        disabled={disabled}
        onChange={(n) => update({ delta_from_baseline: n })}
        hint={t("convergence.deltaHint")}
      />
      <NumberField
        label={t("convergence.stagnationWindow")}
        id="convergence-stag-window"
        value={
          value.stagnation_window ??
          DEFAULT_CONVERGENCE_CONFIG.stagnation_window
        }
        step={1}
        min={1}
        max={10}
        disabled={disabled}
        onChange={(n) => update({ stagnation_window: n })}
        hint={t("convergence.stagnationWindowHint")}
      />
      <NumberField
        label={t("convergence.stagnationEpsilon")}
        id="convergence-stag-eps"
        value={
          value.stagnation_epsilon ??
          DEFAULT_CONVERGENCE_CONFIG.stagnation_epsilon
        }
        step={0.005}
        min={0}
        max={1}
        disabled={disabled}
        onChange={(n) => update({ stagnation_epsilon: n })}
        hint={t("convergence.stagnationEpsilonHint")}
      />
    </div>
  );
}

interface NumberFieldProps {
  label: string;
  id: string;
  value: number;
  step: number;
  min: number;
  max: number;
  onChange: (n: number) => void;
  hint?: string;
  disabled?: boolean;
}

function NumberField({
  label,
  id,
  value,
  step,
  min,
  max,
  onChange,
  hint,
  disabled,
}: NumberFieldProps) {
  return (
    <div>
      <label
        htmlFor={id}
        className="mb-1 block text-[11px] uppercase text-ink-400"
      >
        {label}
      </label>
      <input
        id={id}
        type="number"
        step={step}
        min={min}
        max={max}
        value={value}
        disabled={disabled}
        onChange={(e) => {
          const next = Number(e.target.value);
          // Empty / NaN input bubbles up as the default — never a silent
          // NaN that would crash the backend Pydantic validator.
          onChange(Number.isFinite(next) ? next : value);
        }}
        className="w-full rounded-md border border-ink-200 px-2 py-1 text-xs focus:border-brand-500 focus:outline-none disabled:cursor-not-allowed disabled:bg-ink-50"
      />
      {hint && (
        <div className="mt-1 text-[10px] italic text-ink-400">{hint}</div>
      )}
    </div>
  );
}
