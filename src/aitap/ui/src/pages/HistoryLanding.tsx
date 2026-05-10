import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { Badge, Card, CardHeader, EmptyState } from "../components/primitives";

export function HistoryLanding() {
  const q = useQuery({ queryKey: ["prompts"], queryFn: api.listPrompts });

  if (q.isLoading) {
    return <Card className="p-6 text-sm text-ink-500">loading…</Card>;
  }
  if (!q.data?.prompts.length) {
    return (
      <EmptyState
        title="no prompts to show history for"
        hint="run `aitap scan` first"
      />
    );
  }

  return (
    <Card>
      <CardHeader
        title="prompt version history"
        subtitle="pick a prompt to see its version timeline"
      />
      <ul className="divide-y divide-ink-100">
        {q.data.prompts.map((p) => (
          <li key={p.id}>
            <Link
              to={`/history/${encodeURIComponent(p.id)}`}
              className="flex items-center justify-between gap-4 px-4 py-3 hover:bg-ink-50"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm font-medium text-ink-800">
                    {p.name}
                  </span>
                  <Badge tone="brand">{p.provider}</Badge>
                </div>
                <div className="mt-1 truncate text-xs text-ink-500">
                  {p.purpose ?? "—"}
                </div>
              </div>
              <span className="shrink-0 font-mono text-[11px] text-ink-400">
                latest v{p.latest_version}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </Card>
  );
}
