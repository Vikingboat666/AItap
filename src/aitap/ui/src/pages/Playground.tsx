import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { Badge, Card, CardHeader } from "../components/primitives";
import { clsx } from "../lib/clsx";

type Mode = "node" | "segment" | "end-to-end";

export function Playground() {
  const { targetKind, targetId } = useParams();
  const promptsQ = useQuery({
    queryKey: ["prompts"],
    queryFn: api.listPrompts,
  });
  const pipelinesQ = useQuery({
    queryKey: ["pipelines"],
    queryFn: api.listPipelines,
  });
  const settingsQ = useQuery({
    queryKey: ["settings"],
    queryFn: api.getSettings,
  });

  const [mode, setMode] = useState<Mode>("node");
  const [selectedTarget, setSelectedTarget] = useState<{
    kind: "prompt" | "pipeline";
    id: string;
  } | null>(
    targetKind === "prompt" || targetKind === "pipeline"
      ? { kind: targetKind, id: targetId ?? "" }
      : null,
  );

  const targetLabel = useMemo(() => {
    if (!selectedTarget) return "no target selected";
    if (selectedTarget.kind === "prompt") {
      const p = promptsQ.data?.prompts.find(
        (x) => x.id === selectedTarget.id,
      );
      return p ? `prompt · ${p.name}` : `prompt · ${selectedTarget.id}`;
    }
    const p = pipelinesQ.data?.pipelines.find(
      (x) => x.id === selectedTarget.id,
    );
    return p ? `pipeline · ${p.name}` : `pipeline · ${selectedTarget.id}`;
  }, [selectedTarget, promptsQ.data, pipelinesQ.data]);

  const runM = useMutation({
    mutationFn: async () => {
      if (!selectedTarget) throw new Error("no target selected");
      const created = await api.createRun({
        target_kind: selectedTarget.kind,
        target_id: selectedTarget.id,
        target_version: 1,
        provider: settingsQ.data?.provider ?? "openai",
        model: settingsQ.data?.model ?? "gpt-4o-mini",
        parameters: { model: settingsQ.data?.model ?? "gpt-4o-mini" },
        cases: [
          { inputs: { email_body: "hi, where is my order?" } },
          { inputs: { email_body: "refund please" } },
        ],
      });
      return api.getRun(created.run_id);
    },
  });

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <Card className="lg:col-span-1">
        <CardHeader title="target" />
        <div className="space-y-2 px-4 py-3">
          <div className="text-xs text-ink-500">{targetLabel}</div>
          <details className="text-xs">
            <summary className="cursor-pointer text-ink-600 hover:text-ink-800">
              pick a different target
            </summary>
            <div className="mt-2 space-y-3">
              <div>
                <div className="mb-1 text-[11px] uppercase text-ink-400">
                  prompts
                </div>
                <ul className="max-h-40 overflow-auto rounded-md border border-ink-200">
                  {promptsQ.data?.prompts.map((p) => (
                    <li key={p.id}>
                      <button
                        onClick={() =>
                          setSelectedTarget({ kind: "prompt", id: p.id })
                        }
                        className={clsx(
                          "block w-full px-2 py-1 text-left text-xs hover:bg-ink-50",
                          selectedTarget?.id === p.id && "bg-brand-50",
                        )}
                      >
                        {p.name}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <div className="mb-1 text-[11px] uppercase text-ink-400">
                  pipelines
                </div>
                <ul className="max-h-40 overflow-auto rounded-md border border-ink-200">
                  {pipelinesQ.data?.pipelines.map((p) => (
                    <li key={p.id}>
                      <button
                        onClick={() =>
                          setSelectedTarget({ kind: "pipeline", id: p.id })
                        }
                        className={clsx(
                          "block w-full px-2 py-1 text-left text-xs hover:bg-ink-50",
                          selectedTarget?.id === p.id && "bg-brand-50",
                        )}
                      >
                        {p.name}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </details>

          {selectedTarget?.kind === "pipeline" && (
            <div className="pt-2">
              <div className="mb-1 text-[11px] uppercase text-ink-400">
                run mode
              </div>
              <div className="flex gap-1">
                {(["node", "segment", "end-to-end"] as const).map((m) => (
                  <button
                    key={m}
                    onClick={() => setMode(m)}
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

          <button
            disabled={!selectedTarget || runM.isPending}
            onClick={() => runM.mutate()}
            className="mt-3 w-full rounded-md bg-brand-600 px-3 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-ink-200"
          >
            {runM.isPending ? "running…" : "run with sample dataset"}
          </button>
        </div>
      </Card>

      <Card className="lg:col-span-2">
        <CardHeader
          title="run output"
          subtitle="results from the most recent run on this target"
          action={
            runM.data ? (
              <Badge
                tone={
                  runM.data.status === "done"
                    ? "ok"
                    : runM.data.status === "failed"
                      ? "warn"
                      : "neutral"
                }
              >
                {runM.data.status}
              </Badge>
            ) : null
          }
        />
        <div className="px-4 py-3 text-xs">
          {runM.isPending ? (
            <div className="text-ink-500">running…</div>
          ) : runM.error ? (
            <div className="text-rose-600">{String(runM.error)}</div>
          ) : runM.data ? (
            <ul className="space-y-3">
              {runM.data.outputs.map((o) => (
                <li
                  key={o.case_index}
                  className="rounded-md border border-ink-100 bg-ink-50 p-3"
                >
                  <div className="mb-1 text-[11px] uppercase text-ink-400">
                    case #{o.case_index}
                  </div>
                  {o.text && (
                    <pre className="whitespace-pre-wrap font-mono text-xs text-ink-700">
                      {o.text}
                    </pre>
                  )}
                  {o.error && (
                    <div className="text-rose-600">{o.error}</div>
                  )}
                </li>
              ))}
              <li className="text-[11px] text-ink-500">
                cost: ${runM.data.cost_usd.toFixed(4)}
              </li>
            </ul>
          ) : (
            <div className="italic text-ink-400">
              pick a target on the left and hit run
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}
