import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { Badge, Card, CardHeader } from "../components/primitives";

export function PromptDetail() {
  const { id = "" } = useParams();
  const q = useQuery({
    queryKey: ["prompt", id],
    queryFn: () => api.getPrompt(id),
    enabled: !!id,
  });

  if (q.isLoading || !q.data) {
    return <Card className="p-6 text-sm text-ink-500">loading…</Card>;
  }
  const { site, versions } = q.data;

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <div className="space-y-4 lg:col-span-2">
        <Card>
          <CardHeader
            title={
              <span className="font-mono text-sm">
                {site.name}{" "}
                <Badge tone="brand">{site.provider}</Badge>
              </span>
            }
            subtitle={
              <>
                {site.location.file}:{site.location.line_start}–
                {site.location.line_end}
              </>
            }
            action={
              <div className="flex gap-2">
                <Link
                  to={`/playground/prompt/${encodeURIComponent(site.id)}`}
                  className="rounded-md bg-brand-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-700"
                >
                  open in playground
                </Link>
                <Link
                  to={`/history/${encodeURIComponent(site.id)}`}
                  className="rounded-md bg-ink-100 px-3 py-1.5 text-xs font-medium text-ink-700 hover:bg-ink-200"
                >
                  history
                </Link>
              </div>
            }
          />
          <div className="px-4 py-3 text-xs text-ink-600">
            {site.purpose ?? (
              <span className="italic text-ink-400">
                no purpose inferred (L2 not run)
              </span>
            )}
          </div>
        </Card>

        <Card>
          <CardHeader title="messages" />
          <ul className="divide-y divide-ink-100">
            {site.messages.map((m, i) => (
              <li key={i} className="px-4 py-3">
                <div className="mb-2 flex items-center gap-2">
                  <Badge>{m.role}</Badge>
                  <span className="text-[11px] text-ink-400">
                    {m.template_kind ?? "literal"}
                  </span>
                  {m.variables?.length ? (
                    <span className="text-[11px] text-ink-400">
                      vars: {m.variables.map((v) => v.name).join(", ")}
                    </span>
                  ) : null}
                </div>
                <pre className="whitespace-pre-wrap rounded-md bg-ink-50 px-3 py-2 font-mono text-xs text-ink-700">
                  {m.template_text}
                </pre>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <div className="space-y-4">
        <Card>
          <CardHeader title="parameters" />
          <dl className="grid grid-cols-2 gap-2 px-4 py-3 text-xs">
            {Object.entries(site.parameters ?? {})
              .filter(([, v]) => v !== null && v !== undefined)
              .map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-ink-500">{k}</dt>
                  <dd className="font-mono text-ink-700">
                    {typeof v === "object" ? JSON.stringify(v) : String(v)}
                  </dd>
                </div>
              ))}
          </dl>
        </Card>

        <Card>
          <CardHeader title="versions" />
          <ul className="divide-y divide-ink-100">
            {versions.map((v) => (
              <li
                key={v.version}
                className="flex items-center justify-between px-4 py-2 text-xs"
              >
                <div>
                  <span className="font-mono text-ink-700">v{v.version}</span>
                  <span className="ml-2 text-ink-500">
                    {v.note ?? "no note"}
                  </span>
                </div>
                <Badge tone={v.created_by === "iteration" ? "warn" : "neutral"}>
                  {v.created_by}
                </Badge>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  );
}
