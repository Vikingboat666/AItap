/**
 * Inline JSON test-case editor.
 *
 * The Playground feeds a `DatasetCase[]` to `POST /api/runs`. To stay
 * close to the contract while keeping the UX cheap we let users type
 * raw JSON for the `inputs` of each case and parse it on every edit.
 * Validation errors surface inline so the user knows why the Run
 * button is disabled.
 *
 * Why a controlled raw-string + lazy parse (instead of a structured
 * form): cases have arbitrary `Record<string, unknown>` shapes — a
 * structured form would need per-prompt schema awareness we don't have
 * until M4's case-generator lands. A textarea per case is the smallest
 * thing that survives all current prompt shapes.
 *
 * The helper exports (`parseCase`, `parseCases`, `newCaseDraft`) live
 * here on purpose — they're tightly coupled to the `CaseDraft` shape
 * declared above. Splitting them into a separate file just to satisfy
 * `react-refresh/only-export-components` would be churn for no benefit;
 * we disable the rule at the file level instead.
 */

/* eslint-disable react-refresh/only-export-components */
import { useCallback, useId } from "react";

import { Badge, Card, CardHeader } from "./primitives";
import { clsx } from "../lib/clsx";

export interface CaseDraft {
  /** Raw textarea contents; persisted so users can leave invalid JSON mid-edit. */
  raw: string;
}

export interface ParsedCase {
  inputs: Record<string, unknown>;
}

export interface CaseParseResult {
  parsed: ParsedCase | null;
  error: string | null;
}

export function parseCase(draft: CaseDraft): CaseParseResult {
  const trimmed = draft.raw.trim();
  if (!trimmed) {
    return { parsed: null, error: "case is empty" };
  }
  try {
    const value = JSON.parse(trimmed) as unknown;
    if (
      value === null ||
      typeof value !== "object" ||
      Array.isArray(value)
    ) {
      return {
        parsed: null,
        error: "case must be a JSON object of {variable: value}",
      };
    }
    return {
      parsed: { inputs: value as Record<string, unknown> },
      error: null,
    };
  } catch (err) {
    const message = err instanceof Error ? err.message : "invalid JSON";
    return { parsed: null, error: message };
  }
}

export function parseCases(drafts: CaseDraft[]): {
  cases: ParsedCase[];
  errors: Array<string | null>;
  hasErrors: boolean;
} {
  const errors: Array<string | null> = [];
  const cases: ParsedCase[] = [];
  for (const draft of drafts) {
    const { parsed, error } = parseCase(draft);
    errors.push(error);
    if (parsed) cases.push(parsed);
  }
  const hasErrors = errors.some((e) => e !== null);
  return { cases, errors, hasErrors };
}

export function newCaseDraft(template?: string): CaseDraft {
  return { raw: template ?? '{\n  "input": ""\n}' };
}

export interface CaseEditorProps {
  cases: CaseDraft[];
  onChange: (next: CaseDraft[]) => void;
  /** Optional hint about expected template variables to seed new cases. */
  placeholderVariables?: string[];
  /** Disable editing while a run is in-flight. */
  disabled?: boolean;
  /** Optional subtitle for the card header. */
  subtitle?: string;
}

export function CaseEditor({
  cases,
  onChange,
  placeholderVariables,
  disabled = false,
  subtitle,
}: CaseEditorProps) {
  const { errors, hasErrors } = parseCases(cases);

  const seed = useCallback((): string => {
    if (!placeholderVariables || placeholderVariables.length === 0) {
      return '{\n  "input": ""\n}';
    }
    const body = placeholderVariables
      .map((name) => `  ${JSON.stringify(name)}: ""`)
      .join(",\n");
    return `{\n${body}\n}`;
  }, [placeholderVariables]);

  const updateOne = (index: number, raw: string) => {
    const next = cases.slice();
    next[index] = { raw };
    onChange(next);
  };

  const addOne = () => {
    onChange([...cases, newCaseDraft(seed())]);
  };

  const removeOne = (index: number) => {
    const next = cases.slice();
    next.splice(index, 1);
    onChange(next);
  };

  return (
    <Card>
      <CardHeader
        title="dataset"
        subtitle={subtitle ?? "one JSON object per case · keys map to template vars"}
        action={
          hasErrors ? (
            <Badge tone="warn">{errors.filter(Boolean).length} invalid</Badge>
          ) : (
            <Badge tone="ok">{cases.length} cases</Badge>
          )
        }
      />
      <div className="space-y-3 px-4 py-3">
        {cases.length === 0 ? (
          <div className="rounded-md border border-dashed border-ink-200 px-3 py-4 text-center text-xs italic text-ink-400">
            no cases yet — add one to enable run
          </div>
        ) : (
          cases.map((draft, i) => (
            <CaseRow
              key={i}
              index={i}
              draft={draft}
              error={errors[i]}
              disabled={disabled}
              onChange={(raw) => updateOne(i, raw)}
              onRemove={() => removeOne(i)}
            />
          ))
        )}
        <button
          type="button"
          disabled={disabled}
          onClick={addOne}
          className="w-full rounded-md border border-dashed border-ink-300 px-3 py-2 text-xs text-ink-600 hover:bg-ink-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          + add case
        </button>
      </div>
    </Card>
  );
}

interface CaseRowProps {
  index: number;
  draft: CaseDraft;
  error: string | null;
  disabled: boolean;
  onChange: (raw: string) => void;
  onRemove: () => void;
}

function CaseRow({
  index,
  draft,
  error,
  disabled,
  onChange,
  onRemove,
}: CaseRowProps) {
  const textareaId = useId();
  return (
    <div className="rounded-md border border-ink-100 bg-ink-50/40 p-2">
      <div className="mb-1 flex items-center justify-between">
        <label
          htmlFor={textareaId}
          className="text-[11px] uppercase tracking-wide text-ink-500"
        >
          case #{index}
        </label>
        <button
          type="button"
          onClick={onRemove}
          disabled={disabled}
          className="text-[11px] text-ink-500 hover:text-rose-600 disabled:cursor-not-allowed disabled:opacity-50"
          aria-label={`remove case ${index}`}
        >
          remove
        </button>
      </div>
      <textarea
        id={textareaId}
        value={draft.raw}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        rows={Math.max(3, Math.min(10, draft.raw.split("\n").length))}
        spellCheck={false}
        className={clsx(
          "w-full resize-y rounded-md border bg-white px-2 py-1.5 font-mono text-xs focus:outline-none",
          error
            ? "border-rose-300 focus:border-rose-500"
            : "border-ink-200 focus:border-brand-500",
          disabled && "cursor-not-allowed opacity-60",
        )}
      />
      {error && (
        <div className="mt-1 text-[11px] text-rose-600" role="alert">
          {error}
        </div>
      )}
    </div>
  );
}
