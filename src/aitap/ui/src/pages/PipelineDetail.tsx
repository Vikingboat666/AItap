import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { Badge, Card, CardHeader } from "../components/primitives";
import { DagView } from "./components/DagView";
import type { PipelineEdge } from "../api/types";

export function PipelineDetail() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [selectedEdge, setSelectedEdge] = useState<PipelineEdge | null>(null);

  const q = useQuery({
    queryKey: ["pipeline", id],
    queryFn: () => api.getPipeline(id),
    enabled: !!id,
  });

  if (q.isLoading || !q.data) {
    return <Card className="p-6 text-sm text-ink-500">loading…</Card>;
  }
  const { pipeline, site_index } = q.data;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title={pipeline.name}
          subtitle={
            <>
              {pipeline.nodes.length} nodes · {pipeline.edges.length} edges
            </>
          }
          action={
            <Link
              to={`/playground/pipeline/${encodeURIComponent(pipeline.id)}`}
              className="rounded-md bg-brand-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-700"
            >
              run pipeline
            </Link>
          }
        />
        <div className="p-3">
          <DagView
            pipeline={pipeline}
            siteIndex={site_index}
            onNodeClick={(promptId) =>
              navigate(`/prompts/${encodeURIComponent(promptId)}`)
            }
            onEdgeClick={setSelectedEdge}
          />
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="nodes" />
          <ul className="divide-y divide-ink-100">
            {pipeline.nodes.map((n) => {
              const summary = site_index[n.prompt_id];
              return (
                <li key={n.prompt_id}>
                  <Link
                    to={`/prompts/${encodeURIComponent(n.prompt_id)}`}
                    className="flex items-center justify-between px-4 py-2 text-xs hover:bg-ink-50"
                  >
                    <div>
                      <div className="font-medium text-ink-800">
                        {n.label ?? summary?.name ?? n.prompt_id}
                      </div>
                      <div className="text-ink-500">
                        {summary?.file}:{summary?.line_start}
                      </div>
                    </div>
                    {summary?.provider && (
                      <Badge tone="brand">{summary.provider}</Badge>
                    )}
                  </Link>
                </li>
              );
            })}
          </ul>
        </Card>

        <Card>
          <CardHeader
            title="selected edge"
            subtitle="click an edge in the DAG to inspect"
          />
          <div className="px-4 py-3 text-xs">
            {selectedEdge ? (
              <dl className="grid grid-cols-3 gap-2">
                <dt className="text-ink-500">source</dt>
                <dd className="col-span-2 font-mono text-ink-700">
                  {selectedEdge.source}
                </dd>
                <dt className="text-ink-500">target</dt>
                <dd className="col-span-2 font-mono text-ink-700">
                  {selectedEdge.target}
                </dd>
                <dt className="text-ink-500">kind</dt>
                <dd className="col-span-2">
                  <Badge>{selectedEdge.kind}</Badge>
                </dd>
                <dt className="text-ink-500">via</dt>
                <dd className="col-span-2 font-mono text-ink-700">
                  {selectedEdge.via ?? "—"}
                </dd>
              </dl>
            ) : (
              <span className="italic text-ink-400">
                no edge selected
              </span>
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}
