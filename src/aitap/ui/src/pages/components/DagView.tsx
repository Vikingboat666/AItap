import { useMemo } from "react";
import ReactFlow, {
  Background,
  Controls,
  MarkerType,
  type Edge,
  type Node,
} from "reactflow";

import type { EdgeKind } from "../../api/generated/models/EdgeKind";
import type { Pipeline } from "../../api/generated/models/Pipeline";
import type { PipelineEdge } from "../../api/generated/models/PipelineEdge";
import type { PipelineNode } from "../../api/generated/models/PipelineNode";
import type { PromptSummary } from "../../api/generated/models/PromptSummary";

interface DagViewProps {
  pipeline: Pipeline;
  siteIndex: Record<string, PromptSummary>;
  /**
   * Ids of nodes to render as selected. DagView is a *controlled*
   * component: it only reflects this prop and reports clicks via
   * `onNodeClick`; the parent (Playground) owns the toggle logic so the
   * same selection drives both the highlight here and the run payload.
   */
  selectedNodeIds?: string[];
  onNodeClick?: (promptId: string) => void;
  onEdgeClick?: (edge: PipelineEdge) => void;
}

const NODE_WIDTH = 220;
const NODE_HEIGHT = 64;

/**
 * Map each EdgeKind to a visual style. The contract distinguishes:
 *   - variable / lc_pipe / function : solid blue line (we statically
 *     resolved a real dataflow edge)
 *   - llamaindex / unresolved       : dashed grey line, animated (we
 *     guessed; user should verify)
 *
 * Centralising this in one switch keeps the legend on the page in sync
 * with what ReactFlow actually renders.
 */
function styleForKind(kind: EdgeKind): {
  stroke: string;
  strokeDasharray?: string;
  animated: boolean;
} {
  switch (kind) {
    case "variable":
    case "lc_pipe":
    case "function":
      return { stroke: "#475dff", animated: false };
    case "langgraph":
      // LangGraph declarations are explicit DAG, not heuristics —
      // render as solid with a distinct hue so they're visually
      // separable from the variable/function edges.
      return { stroke: "#7c3aed", animated: false };
    case "crewai":
      // CrewAI task ordering — matched to the green declared_llm
      // node treatment so a CrewAI graph reads as one colour unit.
      return { stroke: "#16a34a", animated: false };
    case "llamaindex":
    case "unresolved":
    default:
      return { stroke: "#b9c1cf", strokeDasharray: "4 4", animated: true };
  }
}

function layout(
  pipeline: Pipeline,
): Record<string, { x: number; y: number }> {
  // Layer nodes by longest-path depth from any entry. Nodes that the BFS
  // never reaches (cycles, disconnected fragments, edges referencing
  // ids not in the node list) get parked in a trailing "unreached"
  // column so they don't visually collide with entry_points at depth 0.
  const incoming = new Map<string, Set<string>>();
  for (const node of pipeline.nodes) {
    incoming.set(node.prompt_id, new Set());
  }
  for (const edge of pipeline.edges) {
    incoming.get(edge.target)?.add(edge.source);
  }

  const depth = new Map<string, number>();
  const queue: string[] = [];
  for (const node of pipeline.nodes) {
    if ((incoming.get(node.prompt_id)?.size ?? 0) === 0) {
      depth.set(node.prompt_id, 0);
      queue.push(node.prompt_id);
    }
  }
  let guard = pipeline.nodes.length * pipeline.edges.length + 1;
  while (queue.length > 0 && guard-- > 0) {
    const id = queue.shift()!;
    const d = depth.get(id) ?? 0;
    for (const edge of pipeline.edges) {
      if (edge.source !== id) continue;
      const next = depth.get(edge.target);
      if (next === undefined || next < d + 1) {
        depth.set(edge.target, d + 1);
        queue.push(edge.target);
      }
    }
  }

  const reachedMax = Array.from(depth.values()).reduce(
    (m, v) => Math.max(m, v),
    0,
  );
  const unreachedDepth = depth.size === pipeline.nodes.length
    ? null
    : reachedMax + 1;

  const buckets = new Map<number, string[]>();
  for (const node of pipeline.nodes) {
    const d = depth.get(node.prompt_id) ?? unreachedDepth ?? 0;
    if (!buckets.has(d)) buckets.set(d, []);
    buckets.get(d)!.push(node.prompt_id);
  }

  const positions: Record<string, { x: number; y: number }> = {};
  const xGap = NODE_WIDTH + 80;
  const yGap = NODE_HEIGHT + 40;
  for (const [d, ids] of buckets) {
    ids.forEach((id, idx) => {
      positions[id] = {
        x: d * xGap,
        y: idx * yGap - ((ids.length - 1) * yGap) / 2,
      };
    });
  }
  return positions;
}

function makeLabel(node: PipelineNode, summary?: PromptSummary): string {
  const display = node.label ?? summary?.name ?? node.prompt_id;
  const provider = summary?.provider ? ` · ${summary.provider}` : "";
  return `${display}${provider}`;
}

export function DagView({
  pipeline,
  siteIndex,
  selectedNodeIds,
  onNodeClick,
  onEdgeClick,
}: DagViewProps) {
  const positions = useMemo(() => layout(pipeline), [pipeline]);
  const selectedSet = useMemo(
    () => new Set(selectedNodeIds ?? []),
    [selectedNodeIds],
  );

  const nodes: Node[] = useMemo(
    () =>
      pipeline.nodes.map((n) => {
        const summary = siteIndex[n.prompt_id];
        const selected = selectedSet.has(n.prompt_id);
        const isNonLlm = n.kind === "non_llm";
        const isDeclaredLlm = n.kind === "declared_llm";
        // ``selected`` overrides the kind-based border treatment so a
        // user-picked node always looks picked. Non-LLM nodes that
        // aren't selected get a dashed border + lower opacity so the
        // user can tell which steps don't actually cost tokens.
        // ``declared_llm`` (CrewAI Task etc.) gets a solid green
        // border at full opacity — they DO cost tokens, the prompt
        // just lives inside the framework rather than in user code.
        let baseBorderStyle: string;
        if (isNonLlm) {
          baseBorderStyle = "1px dashed #b9c1cf";
        } else if (isDeclaredLlm) {
          baseBorderStyle = "1px solid #16a34a";
        } else {
          baseBorderStyle = "1px solid #dde1e9";
        }
        return {
          id: n.prompt_id,
          position: positions[n.prompt_id] ?? { x: 0, y: 0 },
          // `selected` rides in `data` (not just `style`) so the flag is
          // assertable in tests and stays the single source of the
          // highlight below. ``kind`` rides here too so the
          // ``DagView.nodekind`` tests can assert it without re-reading
          // pipeline.nodes.
          data: { label: makeLabel(n, summary), summary, selected, kind: n.kind ?? "llm" },
          type: "default",
          style: {
            width: NODE_WIDTH,
            padding: 10,
            borderRadius: 8,
            border: selected ? "2px solid #475dff" : baseBorderStyle,
            background: selected ? "#eef1ff" : "#ffffff",
            opacity: isNonLlm && !selected ? 0.65 : 1,
            fontSize: 12,
            fontFamily: "inherit",
          },
        };
      }),
    [pipeline, siteIndex, positions, selectedSet],
  );

  const edges: Edge[] = useMemo(
    () =>
      pipeline.edges.map((e, i) => {
        const { stroke, strokeDasharray, animated } = styleForKind(e.kind);
        return {
          id: `${e.source}->${e.target}-${i}`,
          source: e.source,
          target: e.target,
          label: e.via ?? e.kind,
          animated,
          style: { stroke, strokeDasharray },
          labelStyle: { fontSize: 10, fill: "#5e6678" },
          markerEnd: { type: MarkerType.ArrowClosed, color: stroke },
          data: e,
        };
      }),
    [pipeline],
  );

  return (
    <div className="h-[520px] rounded-lg border border-ink-200 bg-white">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        onNodeClick={(_, node) => onNodeClick?.(node.id)}
        onEdgeClick={(_, edge) => {
          const original = edge.data as PipelineEdge | undefined;
          if (original) onEdgeClick?.(original);
        }}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={16} color="#eef0f4" />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
