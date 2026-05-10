import { useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { Badge, Card, CardHeader } from "../components/primitives";

export function History() {
  const { promptId = "" } = useParams();
  const q = useQuery({
    queryKey: ["history", promptId],
    queryFn: () => api.getHistory(promptId),
    enabled: !!promptId,
  });

  if (q.isLoading || !q.data) {
    return <Card className="p-6 text-sm text-ink-500">loading…</Card>;
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="version history"
          subtitle={`prompt ${q.data.prompt_id}`}
        />
        {q.data.entries.length === 0 ? (
          <div className="px-4 py-6 text-xs italic text-ink-400">
            no versions recorded yet
          </div>
        ) : (
          <ol className="divide-y divide-ink-100">
            {q.data.entries.map((e) => (
              <li
                key={e.version}
                className="flex items-start justify-between gap-4 px-4 py-3 text-xs"
              >
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-sm text-ink-800">
                      v{e.version}
                    </span>
                    <Badge
                      tone={e.created_by === "iteration" ? "warn" : "neutral"}
                    >
                      {e.created_by}
                    </Badge>
                    {e.parent_version != null && (
                      <span className="text-[11px] text-ink-400">
                        parent v{e.parent_version}
                      </span>
                    )}
                  </div>
                  <div className="mt-1 text-ink-500">
                    {e.note ?? "no note"}
                  </div>
                  <div className="mt-1 text-[11px] text-ink-400">
                    {new Date(e.created_at).toLocaleString()}
                  </div>
                </div>
                <div className="shrink-0 text-right">
                  {e.avg_score != null && (
                    <div className="text-sm font-medium text-ink-700">
                      {(e.avg_score * 100).toFixed(0)}%
                    </div>
                  )}
                  <button
                    disabled
                    className="mt-2 rounded-md bg-ink-100 px-2 py-1 text-[11px] text-ink-500"
                    title="rollback wired in M3"
                  >
                    rollback
                  </button>
                </div>
              </li>
            ))}
          </ol>
        )}
      </Card>
    </div>
  );
}
