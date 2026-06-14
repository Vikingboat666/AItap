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
import { useTranslation } from "react-i18next";

import {
  PipelinesService,
  ProfilesService,
  PromptsService,
  RunsService,
  SettingsService,
} from "../api/generated";
import type {
  FeedbackCreate,
  FeedbackResponse,
  IterateSessionResponse,
  Profile,
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
import { AutoIterateModal } from "../components/AutoIterateModal";
import { IterationProgress } from "../components/IterationProgress";
import { PromptPreviewCard } from "../components/PromptPreviewCard";
import { DagView } from "./components/DagView";
import { clsx } from "../lib/clsx";

type Mode = "node" | "segment" | "end-to-end";

/** Map the UI's hyphenated mode to the wire enum (underscore). */
function toWireMode(m: Mode): "node" | "segment" | "end_to_end" {
  return m === "end-to-end" ? "end_to_end" : m;
}

/**
 * True when `selected` forms at most one connected component over the
 * pipeline `edges` (treated as undirected). Empty / single-node
 * selections are trivially contiguous. Drives a *non-blocking* warning
 * only — the backend runs a disconnected selection fine, each fragment
 * independently — so this never gates the Run button.
 */
function isContiguousSelection(
  selected: string[],
  edges: ReadonlyArray<{ source: string; target: string }>,
): boolean {
  if (selected.length <= 1) return true;
  const sel = new Set(selected);
  const adj = new Map<string, string[]>();
  for (const id of selected) adj.set(id, []);
  for (const e of edges) {
    if (sel.has(e.source) && sel.has(e.target)) {
      adj.get(e.source)!.push(e.target);
      adj.get(e.target)!.push(e.source);
    }
  }
  const seen = new Set<string>([selected[0]]);
  const queue = [selected[0]];
  while (queue.length > 0) {
    const cur = queue.shift()!;
    for (const nb of adj.get(cur) ?? []) {
      if (!seen.has(nb)) {
        seen.add(nb);
        queue.push(nb);
      }
    }
  }
  return seen.size === sel.size;
}

interface TargetSelection {
  kind: "prompt" | "pipeline";
  id: string;
}

const DEFAULT_DRAFTS: CaseDraft[] = [
  newCaseDraft(),
  newCaseDraft(),
];

export function Playground() {
  const { t } = useTranslation();
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
  // Pipeline node selection for node/segment modes. The parent owns this
  // (DagView is controlled). Cleared on every mode switch so we never
  // emit conflicting selectors (node_id + segment) — the backend 422s on
  // those by design.
  const [pipelineSelection, setPipelineSelection] = useState<string[]>([]);
  const [cases, setCases] = useState<CaseDraft[]>(DEFAULT_DRAFTS);
  const [temperature, setTemperature] = useState<number>(0.2);
  // Selected multi-provider profile id (A2-P2). ``null`` means "use the
  // legacy provider/model dispatch" — same behaviour the page had
  // before this PR. When set, the wire payload carries ``profile_id``
  // and the backend routes through ``deep.factory.get_client_for_profile_config``.
  const [profileId, setProfileId] = useState<string | null>(null);
  const [profileSeeded, setProfileSeeded] = useState(false);
  const [activeRun, setActiveRun] = useState<RunDetailResponse | null>(null);

  // Auto-iterate state — modal visibility + the active session id once
  // a POST /api/iterate succeeds. We keep `iterateSession` so the
  // session_id survives a `<IterationProgress />` unmount/remount (e.g.
  // the user switches tabs and comes back).
  const [iterateModalOpen, setIterateModalOpen] = useState(false);
  const [iterateSession, setIterateSession] =
    useState<IterateSessionResponse | null>(null);

  // Multi-provider profile picker (A2-P2). Lists every configured
  // profile so the user can pick one and route the run through the
  // profile-keyed dispatch instead of the legacy provider/model
  // pathway. Empty list ⇒ the picker renders an empty-state pointing
  // at Settings.
  const profilesQ = useQuery<Profile[]>({
    queryKey: ["profiles"],
    queryFn: () => ProfilesService.listProfilesApiProfilesGet(),
  });

  // Seed profileId once both queries land. We prefer the configured
  // default (``settings.defaults.model_profile_id``) when the profile
  // is still present; otherwise leave the picker on the legacy
  // fallback so today's behaviour stays the default. The
  // ``profileSeeded`` flag means a user who clicks the legacy option
  // once doesn't get reverted to the default on the next refetch.
  //
  // Race we care about (caught by tech review of A2-P2): the first
  // ``profilesQ.data`` arrival can land *before* it actually carries
  // the configured default (cold-cache, list still being scanned).
  // We only flip ``profileSeeded`` on the success path or on an
  // explicit-no-default path so a later refetch that *does* include
  // the default still gets to seed.
  useEffect(() => {
    if (profileSeeded) return;
    if (!profilesQ.data || !settingsQ.data) return;
    const defaultProfileId = settingsQ.data.defaults?.model_profile_id ?? null;
    if (!defaultProfileId) {
      // No default configured at all — nothing for a future refetch to
      // resolve, lock in the legacy fallback.
      setProfileSeeded(true);
      return;
    }
    if (profilesQ.data.some((p) => p.id === defaultProfileId)) {
      setProfileId(defaultProfileId);
      setProfileSeeded(true);
    }
    // Otherwise: leave ``profileSeeded`` false so the next
    // ``profilesQ`` payload that contains the configured default can
    // still seed it.
  }, [profilesQ.data, settingsQ.data, profileSeeded]);

  // Reconcile against the live profile list. If the user picked a
  // profile and a refetch later returns a list that doesn't contain
  // it (deleted in another tab, renamed, …), drop back to the legacy
  // fallback so the wire stays consistent with what the picker shows
  // (browsers fall back to the empty option when ``<select value>``
  // doesn't match, but the React state would still send the stale
  // id). Caught by tech review of A2-P2.
  useEffect(() => {
    if (!profilesQ.data || profileId === null) return;
    if (!profilesQ.data.some((p) => p.id === profileId)) {
      setProfileId(null);
    }
  }, [profilesQ.data, profileId]);

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

  // Pipeline detail (nodes + edges) for the node/segment picker. Only
  // fetched when a pipeline is the selected target.
  const pipelineDetailQ = useQuery({
    queryKey: ["pipeline", selectedTarget?.id ?? ""],
    queryFn: () =>
      PipelinesService.getPipelineApiPipelinesPipelineIdGet({
        pipelineId: selectedTarget?.id ?? "",
      }),
    enabled: selectedTarget?.kind === "pipeline" && !!selectedTarget?.id,
  });

  // Switching run mode clears any node selection so a stale node_id can't
  // ride along with a segment request (or vice versa) and trip the
  // backend's conflicting-selector 422.
  const handleModeChange = useCallback((next: Mode) => {
    setMode(next);
    setPipelineSelection([]);
  }, []);

  // Switching target clears the selection too: node ids belong to one
  // pipeline's DAG, so carrying a selection from pipeline A into B would
  // dispatch ids that don't exist there (the backend runner 422s on the
  // dangling reference). Reset so the picker starts clean on the new DAG.
  const handlePickTarget = useCallback((target: TargetSelection) => {
    setSelectedTarget(target);
    setPipelineSelection([]);
  }, []);

  const handleNodeClick = useCallback(
    (promptId: string) => {
      if (mode === "node") {
        // Single-select: click to pick, click again to clear.
        setPipelineSelection((prev) => (prev[0] === promptId ? [] : [promptId]));
      } else if (mode === "segment") {
        // Multi-select toggle.
        setPipelineSelection((prev) =>
          prev.includes(promptId)
            ? prev.filter((id) => id !== promptId)
            : [...prev, promptId],
        );
      }
      // end-to-end: clicks are no-ops (no selection needed).
    },
    [mode],
  );

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
    if (!selectedTarget) return t("playground.noTargetSelected");
    if (selectedTarget.kind === "prompt") {
      const p = promptsQ.data?.prompts.find((x) => x.id === selectedTarget.id);
      return t("playground.promptLabel", { name: p ? p.name : selectedTarget.id });
    }
    const p = pipelinesQ.data?.pipelines.find(
      (x) => x.id === selectedTarget.id,
    );
    return t("playground.pipelineLabel", {
      name: p ? p.name : selectedTarget.id,
    });
  }, [selectedTarget, promptsQ.data, pipelinesQ.data, t]);

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
      if (!selectedTarget) throw new Error(t("playground.errorNoTarget"));
      if (parsedCases.length === 0) {
        throw new Error(t("playground.errorAddCase"));
      }
      if (!profileId) throw new Error(t("playground.errorNoProfile"));
      // Pipeline runs carry an explicit mode + exactly one matching
      // selector. Prompt runs carry none (the backend only validates
      // pipeline targets). We send only the selector for the active mode
      // so we never trip the conflicting-selector 422.
      const isPipeline = selectedTarget.kind === "pipeline";
      const wireMode = isPipeline ? toWireMode(mode) : null;
      const created = await RunsService.createRunApiRunsPost({
        requestBody: {
          target_kind: selectedTarget.kind,
          target_id: selectedTarget.id,
          target_version: targetVersion,
          // Contract v4 (A2-P3): ``profile_id`` is the only dispatch
          // selector. The backend resolves it to a concrete client via
          // ``deep.factory.get_client_for_profile_config``.
          profile_id: profileId,
          parameters: {
            temperature,
          },
          cases: parsedCases,
          pipeline_mode: wireMode,
          pipeline_node_id:
            wireMode === "node" ? (pipelineSelection[0] ?? null) : null,
          pipeline_segment:
            wireMode === "segment" ? pipelineSelection : null,
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

  // The cache for this query is the *only* source of truth for the
  // optimistic feedback state — the backend has no GET /feedback today
  // and the placeholder queryFn returns an empty object. The global
  // `staleTime: 30_000` in client.ts would otherwise refetch after
  // 30s and clobber the optimistic write we did in `onMutate`, making
  // the user's thumbs/critique appear to vanish. Pin staleTime to
  // Infinity so React Query never auto-refetches this key; we'll
  // remove this override once a real GET /api/runs/{run_id}/feedback
  // endpoint exists (M4) and we can trust the network response.
  const ratingByCase = useQuery<Record<number, -1 | 0 | 1 | null>>({
    queryKey: ["feedback", activeRun?.run_id ?? ""],
    queryFn: () => Promise.resolve({}),
    enabled: !!activeRun,
    initialData: {},
    staleTime: Number.POSITIVE_INFINITY,
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

  const isPipelineTarget = selectedTarget?.kind === "pipeline";

  // Node mode needs exactly one node; segment mode needs at least one
  // (an empty segment is the zero-node footgun the backend 422s). e2e
  // and prompt targets need no selection.
  const pipelineSelectionReady =
    !isPipelineTarget ||
    mode === "end-to-end" ||
    (mode === "node" && pipelineSelection.length === 1) ||
    (mode === "segment" && pipelineSelection.length >= 1);

  // Non-blocking advisory: a segment whose nodes span >1 connected
  // component still runs (each fragment independently), but we warn so
  // the user knows the dataflow won't bridge the gap.
  const segmentNotContiguous =
    isPipelineTarget &&
    mode === "segment" &&
    pipelineSelection.length > 1 &&
    pipelineDetailQ.data != null &&
    !isContiguousSelection(
      pipelineSelection,
      pipelineDetailQ.data.pipeline.edges,
    );

  const canRun =
    !!selectedTarget &&
    !caseHasErrors &&
    parsedCases.length > 0 &&
    pipelineSelectionReady &&
    // Contract v4 (A2-P3): no profile ⇒ no dispatch path. The picker
    // empty-state surfaces "Open Settings to add one" so the user
    // knows the next action.
    !!profileId &&
    !runMutation.isPending;

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
      <div className="space-y-4 xl:col-span-1">
        <TargetCard
          targetLabel={targetLabel}
          selectedTarget={selectedTarget}
          onPickTarget={handlePickTarget}
          prompts={promptsQ.data?.prompts ?? []}
          pipelines={pipelinesQ.data?.pipelines ?? []}
          promptsLoading={promptsQ.isLoading}
          pipelinesLoading={pipelinesQ.isLoading}
          mode={mode}
          onModeChange={handleModeChange}
        />

        <ProfileSelector
          profiles={profilesQ.data ?? []}
          profileId={profileId}
          onProfileChange={setProfileId}
          loading={profilesQ.isLoading}
        />

        <TemperatureControl
          temperature={temperature}
          onTemperatureChange={setTemperature}
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
          {runMutation.isPending ? t("playground.running") : t("playground.run")}
        </button>

        {/*
          Auto-iterate button — disabled while a single-shot run is in
          flight (so the user can't dispatch a session against a moving
          target) and when no prompt is selected. The modal carries the
          mode/instruction/manual-text inputs and gates the POST itself.
        */}
        <button
          type="button"
          disabled={
            !selectedTarget ||
            selectedTarget.kind !== "prompt" ||
            runMutation.isPending
          }
          onClick={() => setIterateModalOpen(true)}
          className={clsx(
            "w-full rounded-md px-3 py-2 text-sm font-medium",
            selectedTarget?.kind === "prompt" && !runMutation.isPending
              ? "border border-brand-300 bg-white text-brand-700 hover:bg-brand-50"
              : "cursor-not-allowed border border-ink-200 bg-ink-50 text-ink-400",
          )}
          title={
            selectedTarget?.kind === "prompt"
              ? t("playground.autoIterateEnabledTitle")
              : t("playground.autoIterateDisabledTitle")
          }
        >
          {t("playground.autoIterate")}
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
                {t("common.retry")}
              </button>
            </div>
          </Card>
        )}
      </div>

      <div className="space-y-4 xl:col-span-2">
        {isPipelineTarget && (mode === "node" || mode === "segment") && (
          <Card>
            <CardHeader
              title={
                mode === "node"
                  ? t("playground.pickANode")
                  : t("playground.pickASegment")
              }
              subtitle={
                mode === "node"
                  ? t("playground.pickANodeSubtitle")
                  : t("playground.pickASegmentSubtitle")
              }
            />
            <div className="px-3 py-3">
              {pipelineDetailQ.isLoading ? (
                <div className="text-xs text-ink-500">
                  {t("pipeline.loading")}
                </div>
              ) : pipelineDetailQ.data ? (
                <>
                  <div className="mb-2 flex items-center gap-2 text-[11px]">
                    {pipelineSelection.length === 0 ? (
                      <Badge tone="warn">
                        {mode === "node"
                          ? t("playground.selectANode")
                          : t("playground.selectAtLeastOneNode")}
                      </Badge>
                    ) : (
                      <Badge tone="brand">
                        {mode === "node"
                          ? t("playground.nodeBadge", {
                              id: pipelineSelection[0],
                            })
                          : t("playground.segmentBadge", {
                              count: pipelineSelection.length,
                            })}
                      </Badge>
                    )}
                  </div>
                  <DagView
                    pipeline={pipelineDetailQ.data.pipeline}
                    siteIndex={pipelineDetailQ.data.site_index}
                    selectedNodeIds={pipelineSelection}
                    onNodeClick={handleNodeClick}
                  />
                  {segmentNotContiguous && (
                    <div
                      role="alert"
                      className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-[11px] text-amber-700"
                    >
                      {t("playground.notContiguous")}
                    </div>
                  )}
                </>
              ) : (
                <div className="text-xs text-ink-500">
                  {t("playground.noPipelineData")}
                </div>
              )}
            </div>
          </Card>
        )}

        {/*
          Prompt-template preview — shows the actual system/user text
          the user is about to test, so the Playground stops reading
          as "a form of empty boxes." Pipelines don't get a preview
          (each node already surfaces text on click in the DAG); we
          only render the card when the target is a prompt and the
          detail fetch has resolved. Stays out of the way (collapsible
          via its own header button) once the user has gotten the
          mental model.
        */}
        {selectedTarget?.kind === "prompt" && promptDetailQ.data && (
          <PromptPreviewCard site={promptDetailQ.data.site} />
        )}

        <CaseEditor
          cases={cases}
          onChange={setCases}
          placeholderVariables={placeholderVariables}
          disabled={runMutation.isPending}
          subtitle={t("playground.caseExplanation")}
        />

        {runMutation.isPending ? (
          <ResultsSkeleton />
        ) : activeRun ? (
          <ResultsTable
            outputs={activeRun.outputs}
            costUsd={activeRun.cost_usd}
            title={t("playground.runOutput")}
            subtitle={t("playground.runOutputSubtitle", {
              runId: activeRun.run_id,
              status: activeRun.status,
            })}
            ratingByCase={ratingByCase.data ?? {}}
            onFeedback={handleFeedback}
            feedbackDisabled={feedbackMutation.isPending}
          />
        ) : (
          <ResultsTable
            outputs={[]}
            emptyHint={t("playground.emptyHint")}
          />
        )}

        {iterateSession && (
          <IterationProgress
            sessionId={iterateSession.session_id}
            maxRounds={5}
          />
        )}

        {feedbackMutation.data && (
          <FeedbackToast
            response={feedbackMutation.data.res}
            onDismiss={() => feedbackMutation.reset()}
          />
        )}
      </div>

      {iterateModalOpen && selectedTarget?.kind === "prompt" && (
        <AutoIterateModal
          promptId={selectedTarget.id}
          // No `initialDatasetId` here — datasets live under
          // `.aitap/datasets/<name>.cases.jsonl` and have no derivable
          // mapping from a prompt id. We previously seeded with
          // `selectedTarget.id` to mask a missing picker UI, but the
          // backend silently treats unknown ids as an empty case list
          // (every round scores 0 → "converged via max_rounds" with a
          // flat-zero chart). The modal now requires the user to type
          // the dataset name explicitly.
          onClose={() => setIterateModalOpen(false)}
          onStart={(session) => {
            setIterateSession(session);
            setIterateModalOpen(false);
          }}
        />
      )}
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

const MODE_LABEL_KEY: Record<Mode, string> = {
  node: "playground.modeNode",
  segment: "playground.modeSegment",
  "end-to-end": "playground.modeEndToEnd",
};

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
  const { t } = useTranslation();
  return (
    <Card>
      <CardHeader title={t("playground.target")} />
      <div className="space-y-3 px-4 py-3">
        <div className="text-xs text-ink-500">{targetLabel}</div>
        <details className="text-xs" open={!selectedTarget}>
          <summary className="cursor-pointer text-ink-600 hover:text-ink-800">
            {t("playground.pickDifferentTarget")}
          </summary>
          <div className="mt-2 space-y-3">
            <TargetList
              title={t("playground.prompts")}
              loading={promptsLoading}
              items={prompts}
              selectedId={selectedTarget?.id ?? null}
              onPick={(id) => onPickTarget({ kind: "prompt", id })}
              kind="prompt"
            />
            <TargetList
              title={t("playground.pipelines")}
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
              {t("playground.runMode")}
            </div>
            {/*
              All three modes are exposed now that the node-pick UI
              (segment/node selection on the DAG) has landed. The picker
              gates an empty segment behind a disabled Run button, so the
              old "zero-node segment silently succeeds" footgun can't
              occur from the UI; the backend also 422s an empty segment.
            */}
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
                  {t(MODE_LABEL_KEY[m])}
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
  const { t } = useTranslation();
  return (
    <div>
      <div className="mb-1 text-[11px] uppercase text-ink-400">{title}</div>
      {loading ? (
        <div className="rounded-md border border-ink-200 px-2 py-2 text-[11px] text-ink-400">
          {t("playground.loadingList", { title })}
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-md border border-dashed border-ink-200 px-2 py-2 text-[11px] italic text-ink-400">
          {t("playground.noneDiscovered")}
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

/**
 * Multi-provider profile picker. After contract v4 (A2-P3) the picker
 * is required — every run dispatches through
 * ``deep.factory.get_client_for_profile_config``. The legacy
 * provider/model fallback was removed; the empty-state points the
 * user at Settings (CLAUDE.md plain-language: explain the cause +
 * name the next action) and the parent's ``canRun`` gate disables
 * Run until a profile is chosen.
 */
function ProfileSelector({
  profiles,
  profileId,
  onProfileChange,
  loading,
}: {
  profiles: Profile[];
  profileId: string | null;
  onProfileChange: (id: string | null) => void;
  loading: boolean;
}) {
  const { t } = useTranslation();
  const hasProfiles = profiles.length > 0;
  return (
    <Card>
      <CardHeader title={t("playground.profile")} />
      <div className="space-y-2 px-4 py-3">
        {loading ? (
          <div className="text-xs italic text-ink-400">
            {t("playground.profileLoading")}
          </div>
        ) : !hasProfiles ? (
          <div className="text-xs text-ink-500">
            {t("playground.profileEmpty")}
          </div>
        ) : (
          <>
            <label
              htmlFor="profile-select"
              className="mb-1 block text-[11px] uppercase text-ink-400"
            >
              {t("playground.profileLabel")}
            </label>
            <select
              id="profile-select"
              value={profileId ?? ""}
              onChange={(e) =>
                onProfileChange(e.target.value === "" ? null : e.target.value)
              }
              className="w-full rounded-md border border-ink-200 px-2 py-1 text-xs focus:border-brand-500 focus:outline-none"
            >
              {profileId === null && (
                <option value="" disabled>
                  {t("playground.profilePickPlaceholder")}
                </option>
              )}
              {profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label} ({p.model_id})
                </option>
              ))}
            </select>
            <p className="text-[11px] text-ink-400">
              {profileId === null
                ? t("playground.profilePickHint")
                : t("playground.profileHintActive")}
            </p>
          </>
        )}
      </div>
    </Card>
  );
}

/**
 * Temperature-only sub-card. After contract v4 (A2-P3) the model is
 * dictated by the chosen profile, so the only remaining knob a user
 * tweaks per-run is sampling temperature.
 */
function TemperatureControl({
  temperature,
  onTemperatureChange,
}: {
  temperature: number;
  onTemperatureChange: (n: number) => void;
}) {
  const { t } = useTranslation();
  return (
    <Card>
      <CardHeader title={t("playground.sampling")} />
      <div className="space-y-3 px-4 py-3">
        <div>
          <label
            htmlFor="temperature-input"
            className="mb-1 block text-[11px] uppercase text-ink-400"
          >
            {t("playground.temperature", { value: temperature.toFixed(2) })}
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
  const { t } = useTranslation();
  return (
    <Card>
      <CardHeader
        title={t("playground.runOutput")}
        subtitle={t("playground.dispatching")}
      />
      <div className="space-y-2 px-4 py-3">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="h-12 animate-pulse rounded-md bg-ink-100"
            aria-hidden
          />
        ))}
        <div className="text-xs italic text-ink-400">
          {t("playground.skeletonHint")}
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
  const { t } = useTranslation();
  return (
    <div
      role="status"
      className="flex items-center justify-between rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700"
    >
      <span>
        {t("playground.feedbackRecorded", { id: response.feedback_id })}
      </span>
      <button
        type="button"
        onClick={onDismiss}
        className="text-[11px] text-emerald-700 hover:text-emerald-900"
      >
        {t("common.dismiss")}
      </button>
    </div>
  );
}
