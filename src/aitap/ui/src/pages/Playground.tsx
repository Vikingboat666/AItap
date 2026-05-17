/**
 * Playground — pick a prompt or pipeline, edit a dataset, fire a run,
 * watch results land. The page is the single human-facing surface for
 * `POST /api/runs` + `GET /api/runs/{id}` + `POST /api/runs/{id}/feedback`.
 *
 * State model:
 *   - `selectedTarget` mirrors the URL params and is the only mutable
 *     state owned at this level; the prompt/pipeline lists come from
 *     react-query.
 *   - `cases` is a CaseDraft[] (raw strings); we parse on demand for
 *     the run-mutation payload and gate the Run button on validity.
 *   - `model` / `provider` / `temperature` are local form state seeded
 *     from settings (so the first paint shows the user's defaults).
 *   - `activeRun` holds the latest run's RunDetailResponse so reaction
 *     buttons can stamp feedback against the right `run_id`.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import { useParams } from "react-router-dom";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import {
  PipelinesService,
  PromptsService,
  RunsService,
  SettingsService,
} from "../api/generated";
import type {
  FeedbackCreate,
  FeedbackResponse,
  PromptDetailResponse,
  RunDetailResponse,
} from "../api/generated";
import { Badge, Card, CardHeader } from "../components/primitives";
import {
  CaseEditor,
  newCaseDraft,
  parseCases,
  type CaseDraft,
} from "../components/CaseEditor";
import {
  ResultsTable,
  type FeedbackSubmission,
} from "../components/ResultsTable";
import { clsx } from "../lib/clsx";

type Mode = "node" | "segment" | "end-to-end";

interface TargetSelection {
  kind: "prompt" | "pipeline";
  id: string;
}

const DEFAULT_DRAFTS: CaseDraft[] = [
  newCaseDraft(),
  newCaseDraft(),
];

export function Playground() {
  const { targetKind, targetId } = useParams();
  const queryClient = useQueryClient();

  const promptsQ = useQuery({
    queryKey: ["prompts"],
    queryFn: () => PromptsService.listPromptsApiPromptsGet(),
  });
  const pipelinesQ = useQuery({
    queryKey: ["pipelines"],
    queryFn: () => PipelinesService.listPipelinesApiPipelinesGet(),
  });
  const settingsQ = useQuery({
    queryKey: ["settings"],
    queryFn: () => SettingsService.getSettingsEndpointApiSettingsGet(),
  });

  const [selectedTarget, setSelectedTarget] = useState<TargetSelection | null>(
    targetKind === "prompt" || targetKind === "pipeline"
      ? { kind: targetKind, id: targetId ?? "" }
      : null,
  );
  const [mode, setMode] = useState<Mode>("node");
  const [cases, setCases] = useState<CaseDraft[]>(DEFAULT_DRAFTS);
  const [model, setModel] = useState<string>("");
  const [temperature, setTemperature] = useState<number>(0.2);
  const [activeRun, setActiveRun] = useState<RunDetailResponse | null>(null);

  // Seed model from settings the first time settings resolve. After
  // that the user owns the field.
  useEffect(() => {
    if (settingsQ.data && !model) {
      setModel(settingsQ.data.model);
    }
  }, [settingsQ.data, model]);

  // Pull the selected prompt detail so we know which template vars to
  // seed new cases with. Pipelines don't have a single var list — we
  // skip the placeholder for them and let users hand-write inputs.
  const promptDetailQ = useQuery<PromptDetailResponse>({
    queryKey: ["prompt", selectedTarget?.id ?? ""],
    queryFn: () =>
      PromptsService.getPromptApiPromptsPromptIdGet({
        promptId: selectedTarget?.id ?? "",
      }),
    enabled: selectedTarget?.kind === "prompt" && !!selectedTarget?.id,
  });

  const placeholderVariables = useMemo<string[] | undefined>(() => {
    if (selectedTarget?.kind !== "prompt" || !promptDetailQ.data) {
      return undefined;
    }
    const seen = new Set<string>();
    for (const message of promptDetailQ.data.site.messages) {
      for (const variable of message.variables ?? []) {
        if (variable.name) seen.add(variable.name);
      }
    }
    return Array.from(seen);
  }, [selectedTarget, promptDetailQ.data]);

  const targetLabel = useMemo(() => {
    if (!selectedTarget) return "no target selected";
    if (selectedTarget.kind === "prompt") {
      const p = promptsQ.data?.prompts.find((x) => x.id === selectedTarget.id);
      return p ? `prompt · ${p.name}` : `prompt · ${selectedTarget.id}`;
    }
    const p = pipelinesQ.data?.pipelines.find(
      (x) => x.id === selectedTarget.id,
    );
    return p ? `pipeline · ${p.name}` : `pipeline · ${selectedTarget.id}`;
  }, [selectedTarget, promptsQ.data, pipelinesQ.data]);

  const { cases: parsedCases, hasErrors: caseHasErrors } = useMemo(
    () => parseCases(cases),
    [cases],
  );

  const targetVersion = useMemo(() => {
    if (selectedTarget?.kind === "prompt") {
      const summary = promptsQ.data?.prompts.find(
        (x) => x.id === selectedTarget.id,
      );
      return summary?.latest_version ?? 1;
    }
    return 1;
  }, [selectedTarget, promptsQ.data]);

  const runMutation = useMutation({
    mutationFn: async () => {
      if (!selectedTarget) throw new Error("no target selected");
      if (parsedCases.length === 0) {
        throw new Error("add at least one case");
      }
      const effectiveModel =
        model || settingsQ.data?.model || "gpt-4o-mini";
      const provider = settingsQ.data?.provider ?? "openai";
      const created = await RunsService.createRunApiRunsPost({
        requestBody: {
          target_kind: selectedTarget.kind,
          target_id: selectedTarget.id,
          target_version: targetVersion,
          provider,
          model: effectiveModel,
          parameters: {
            model: effectiveModel,
            temperature,
          },
          cases: parsedCases,
          pipeline_segment:
            selectedTarget.kind === "pipeline" && mode === "segment"
              ? []
              : null,
        },
      });
      return RunsService.getRunApiRunsRunIdGet({ runId: created.run_id });
    },
    onSuccess: (detail) => {
      setActiveRun(detail);
      queryClient.setQueryData(["run", detail.run_id], detail);
    },
  });

  // Optimistic feedback — flip the UI immediately, roll back on error.
  const feedbackMutation = useMutation({
    mutationFn: async (payload: FeedbackCreate & { runId: string }) => {
      const { runId, ...body } = payload;
      const res = await RunsService.postFeedbackApiRunsRunIdFeedbackPost({
        runId,
        requestBody: body,
      });
      return { res, runId, body };
    },
    onMutate: async (payload) => {
      const cacheKey = ["feedback", payload.runId] as const;
      await queryClient.cancelQueries({ queryKey: cacheKey });
      const previous = queryClient.getQueryData<
        Record<number, -1 | 0 | 1 | null>
      >(cacheKey);
      const next: Record<number, -1 | 0 | 1 | null> = { ...(previous ?? {}) };
      next[payload.case_index] = payload.rating ?? null;
      queryClient.setQueryData(cacheKey, next);
      return { previous, cacheKey };
    },
    onError: (_err, _payload, context) => {
      if (context?.previous) {
        queryClient.setQueryData(context.cacheKey, context.previous);
      }
    },
  });

  const ratingByCase = useQuery<Record<number, -1 | 0 | 1 | null>>({
    queryKey: ["feedback", activeRun?.run_id ?? ""],
    queryFn: () => Promise.resolve({}),
    enabled: !!activeRun,
    initialData: {},
  });

  const handleFeedback = useCallback(
    (submission: FeedbackSubmission) => {
      if (!activeRun) return;
      const payload: FeedbackCreate & { runId: string } = {
        runId: activeRun.run_id,
        case_index: submission.caseIndex,
        rating: submission.rating,
        critique: submission.critique ?? null,
      };
      feedbackMutation.mutate(payload);
    },
    [activeRun, feedbackMutation],
  );

  const canRun =
    !!selectedTarget &&
    !caseHasErrors &&
    parsedCases.length > 0 &&
    !runMutation.isPending;

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
      <div className="space-y-4 xl:col-span-1">
        <TargetCard
          targetLabel={targetLabel}
          selectedTarget={selectedTarget}
          onPickTarget={setSelectedTarget}
          prompts={promptsQ.data?.prompts ?? []}
          pipelines={pipelinesQ.data?.pipelines ?? []}
          promptsLoading={promptsQ.isLoading}
          pipelinesLoading={pipelinesQ.isLoading}
          mode={mode}
          onModeChange={setMode}
        />

        <ModelControls
          model={model}
          temperature={temperature}
          onModelChange={setModel}
          onTemperatureChange={setTemperature}
          providerHint={settingsQ.data?.provider}
        />

        <button
          type="button"
          disabled={!canRun}
          onClick={() => runMutation.mutate()}
          className={clsx(
            "w-full rounded-md px-3 py-2 text-sm font-medium text-white",
            canRun
              ? "bg-brand-600 hover:bg-brand-700"
              : "cursor-not-allowed bg-ink-200",
          )}
        >
          {runMutation.isPending ? "running…" : "run"}
        </button>

        {runMutation.error && (
          <Card className="border-rose-200 bg-rose-50/40">
            <div className="space-y-2 px-4 py-3 text-xs text-rose-700">
              <div>{(runMutation.error as Error).message}</div>
              <button
                type="button"
                onClick={() => runMutation.mutate()}
                className="rounded-md bg-rose-600 px-2 py-1 text-[11px] font-medium text-white hover:bg-rose-700"
              >
                retry
              </button>
            </div>
          </Card>
        )}
      </div>

      <div className="space-y-4 xl:col-span-2">
        <CaseEditor
          cases={cases}
          onChange={setCases}
          placeholderVariables={placeholderVariables}
          disabled={runMutation.isPending}
        />

        {runMutation.isPending ? (
          <ResultsSkeleton />
        ) : activeRun ? (
          <ResultsTable
            outputs={activeRun.outputs}
            costUsd={activeRun.cost_usd}
            title="run output"
            subtitle={`run ${activeRun.run_id} · ${activeRun.status}`}
            ratingByCase={ratingByCase.data ?? {}}
            onFeedback={handleFeedback}
            feedbackDisabled={feedbackMutation.isPending}
          />
        ) : (
          <ResultsTable
            outputs={[]}
            emptyHint="pick a target, add cases, then hit run"
          />
        )}

        {feedbackMutation.data && (
          <FeedbackToast
            response={feedbackMutation.data.res}
            onDismiss={() => feedbackMutation.reset()}
          />
        )}
      </div>
    </div>
  );
}

interface TargetCardProps {
  targetLabel: string;
  selectedTarget: TargetSelection | null;
  onPickTarget: (target: TargetSelection) => void;
  prompts: Array<{ id: string; name: string }>;
  pipelines: Array<{ id: string; name: string }>;
  promptsLoading: boolean;
  pipelinesLoading: boolean;
  mode: Mode;
  onModeChange: (m: Mode) => void;
}

function TargetCard({
  targetLabel,
  selectedTarget,
  onPickTarget,
  prompts,
  pipelines,
  promptsLoading,
  pipelinesLoading,
  mode,
  onModeChange,
}: TargetCardProps) {
  return (
    <Card>
      <CardHeader title="target" />
      <div className="space-y-3 px-4 py-3">
        <div className="text-xs text-ink-500">{targetLabel}</div>
        <details className="text-xs" open={!selectedTarget}>
          <summary className="cursor-pointer text-ink-600 hover:text-ink-800">
            pick a different target
          </summary>
          <div className="mt-2 space-y-3">
            <TargetList
              title="prompts"
              loading={promptsLoading}
              items={prompts}
              selectedId={selectedTarget?.id ?? null}
              onPick={(id) => onPickTarget({ kind: "prompt", id })}
              kind="prompt"
            />
            <TargetList
              title="pipelines"
              loading={pipelinesLoading}
              items={pipelines}
              selectedId={selectedTarget?.id ?? null}
              onPick={(id) => onPickTarget({ kind: "pipeline", id })}
              kind="pipeline"
            />
          </div>
        </details>

        {selectedTarget?.kind === "pipeline" && (
          <div>
            <div className="mb-1 text-[11px] uppercase text-ink-400">
              run mode
            </div>
            <div className="flex gap-1">
              {(["node", "segment", "end-to-end"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => onModeChange(m)}
                  className={clsx(
                    "rounded-md px-2 py-1 text-xs",
                    mode === m
                      ? "bg-brand-600 text-white"
                      : "bg-ink-100 text-ink-700 hover:bg-ink-200",
                  )}
                >
                  {m}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

function TargetList({
  title,
  loading,
  items,
  selectedId,
  onPick,
  kind,
}: {
  title: string;
  loading: boolean;
  items: Array<{ id: string; name: string }>;
  selectedId: string | null;
  onPick: (id: string) => void;
  kind: "prompt" | "pipeline";
}) {
  return (
    <div>
      <div className="mb-1 text-[11px] uppercase text-ink-400">{title}</div>
      {loading ? (
        <div className="rounded-md border border-ink-200 px-2 py-2 text-[11px] text-ink-400">
          loading {title}…
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-md border border-dashed border-ink-200 px-2 py-2 text-[11px] italic text-ink-400">
          none discovered
        </div>
      ) : (
        <ul className="max-h-40 overflow-auto rounded-md border border-ink-200">
          {items.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                onClick={() => onPick(p.id)}
                className={clsx(
                  "block w-full px-2 py-1 text-left text-xs hover:bg-ink-50",
                  selectedId === p.id && "bg-brand-50",
                )}
                data-target-kind={kind}
                data-target-id={p.id}
              >
                {p.name}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ModelControls({
  model,
  temperature,
  onModelChange,
  onTemperatureChange,
  providerHint,
}: {
  model: string;
  temperature: number;
  onModelChange: (s: string) => void;
  onTemperatureChange: (n: number) => void;
  providerHint?: string;
}) {
  return (
    <Card>
      <CardHeader
        title="model"
        action={providerHint ? <Badge tone="brand">{providerHint}</Badge> : null}
      />
      <div className="space-y-3 px-4 py-3">
        <div>
          <label
            htmlFor="model-input"
            className="mb-1 block text-[11px] uppercase text-ink-400"
          >
            model
          </label>
          <input
            id="model-input"
            type="text"
            value={model}
            onChange={(e) => onModelChange(e.target.value)}
            placeholder="gpt-4o-mini"
            className="w-full rounded-md border border-ink-200 px-2 py-1 text-xs focus:border-brand-500 focus:outline-none"
          />
        </div>
        <div>
          <label
            htmlFor="temperature-input"
            className="mb-1 block text-[11px] uppercase text-ink-400"
          >
            temperature ({temperature.toFixed(2)})
          </label>
          <input
            id="temperature-input"
            type="range"
            min={0}
            max={2}
            step={0.05}
            value={temperature}
            onChange={(e) => onTemperatureChange(Number(e.target.value))}
            className="w-full"
          />
        </div>
      </div>
    </Card>
  );
}

function ResultsSkeleton() {
  return (
    <Card>
      <CardHeader title="run output" subtitle="dispatching to backend…" />
      <div className="space-y-2 px-4 py-3">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="h-12 animate-pulse rounded-md bg-ink-100"
            aria-hidden
          />
        ))}
        <div className="text-xs italic text-ink-400">
          this will keep the result table area reserved while the run completes
        </div>
      </div>
    </Card>
  );
}

function FeedbackToast({
  response,
  onDismiss,
}: {
  response: FeedbackResponse;
  onDismiss: () => void;
}) {
  return (
    <div
      role="status"
      className="flex items-center justify-between rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700"
    >
      <span>feedback recorded (id #{response.feedback_id})</span>
      <button
        type="button"
        onClick={onDismiss}
        className="text-[11px] text-emerald-700 hover:text-emerald-900"
      >
        dismiss
      </button>
    </div>
  );
}
