/**
 * DagView — non-LLM node + LangGraph edge rendering (B2-LG, stage 3).
 *
 * The B2-LG worktree adds two display concepts:
 *
 *   1. ``PipelineNode.kind === "non_llm"`` — DAG steps the framework
 *      declared (LangGraph ``add_node("parse", parse_json)``) that
 *      don't themselves call an LLM. The visual treatment is dashed
 *      border + dimmer text so users can tell which steps cost tokens.
 *   2. ``EdgeKind.LANGGRAPH`` — explicit DAG declaration. Solid line,
 *      not the dashed treatment LlamaIndex/unresolved get.
 *
 * Same ReactFlow stub as DagView.selection.test — jsdom doesn't paint
 * SVG, so we mirror the props ReactFlow receives into flat DOM and
 * assert against those.
 */
import { describe, expect, it } from "vitest";

type NodeLike = {
  id: string;
  data?: { label?: string; kind?: string };
  style?: Record<string, unknown>;
};
type EdgeLike = {
  id: string;
  label?: string;
  style?: Record<string, unknown>;
};

import { vi } from "vitest";

vi.mock("reactflow", async () => {
  const React = await import("react");
  function ReactFlow({
    nodes,
    edges,
  }: {
    nodes: NodeLike[];
    edges: EdgeLike[];
  }) {
    return React.createElement(
      "div",
      { "data-testid": "rf-root" },
      React.createElement(
        "ul",
        { "data-testid": "rf-nodes" },
        nodes.map((n) =>
          React.createElement(
            "li",
            {
              key: n.id,
              "data-testid": "rf-node",
              "data-id": n.id,
              "data-kind": n.data?.kind ?? "llm",
              "data-border": (n.style?.border as string) ?? "",
              "data-opacity": String(n.style?.opacity ?? ""),
            },
            n.data?.label ?? n.id,
          ),
        ),
      ),
      React.createElement(
        "ul",
        { "data-testid": "rf-edges" },
        edges.map((e) =>
          React.createElement(
            "li",
            {
              key: e.id,
              "data-testid": "rf-edge",
              "data-label": e.label ?? "",
              "data-stroke": (e.style?.stroke as string) ?? "",
              "data-dasharray":
                (e.style?.strokeDasharray as string) ?? "",
            },
            e.label,
          ),
        ),
      ),
    );
  }
  const noop = () => null;
  return {
    __esModule: true,
    default: ReactFlow,
    Background: noop,
    Controls: noop,
    MarkerType: { ArrowClosed: "arrowclosed" },
  };
});

import { DagView } from "../components/DagView";
import { renderWithProviders, screen } from "../../test-utils/render";
import type { Pipeline } from "../../api/types";

const LANGGRAPH_PIPELINE: Pipeline = {
  id: "lg_test",
  name: "langgraph:graph",
  nodes: [
    { prompt_id: "p_classify", label: "classify", kind: "llm" },
    { prompt_id: "langgraph:app.py:graph:parse", label: "parse", kind: "non_llm" },
    { prompt_id: "p_respond", label: "respond", kind: "llm" },
  ],
  edges: [
    {
      source: "p_classify",
      target: "langgraph:app.py:graph:parse",
      kind: "langgraph",
      via: "app.py::StateGraph(graph)",
      confidence: "high",
    },
    {
      source: "langgraph:app.py:graph:parse",
      target: "p_respond",
      kind: "langgraph",
      via: "app.py::StateGraph(graph)",
      confidence: "high",
    },
  ],
  entry_points: ["p_classify"],
  exit_points: ["p_respond"],
};

const EMPTY_SITE_INDEX = {} as never;

describe("DagView — non-LLM node kind", () => {
  it("renders non_llm nodes with a dashed border + dimmer opacity", () => {
    renderWithProviders(
      <DagView pipeline={LANGGRAPH_PIPELINE} siteIndex={EMPTY_SITE_INDEX} />,
    );
    const parseNode = screen
      .getAllByTestId("rf-node")
      .find((n) => n.getAttribute("data-id")?.includes("parse"));
    expect(parseNode).toBeDefined();
    expect(parseNode!.getAttribute("data-kind")).toBe("non_llm");
    // Dashed border so the user can tell this step doesn't cost tokens.
    expect(parseNode!.getAttribute("data-border")).toContain("dashed");
    // Slightly dim so it doesn't compete with LLM nodes for attention.
    // Match anything < 1 — the exact value is a design choice we can tune.
    const opacity = Number(parseNode!.getAttribute("data-opacity"));
    expect(opacity).toBeLessThan(1);
    expect(opacity).toBeGreaterThan(0);
  });

  it("leaves llm nodes with their existing solid-border treatment", () => {
    renderWithProviders(
      <DagView pipeline={LANGGRAPH_PIPELINE} siteIndex={EMPTY_SITE_INDEX} />,
    );
    const classifyNode = screen
      .getAllByTestId("rf-node")
      .find((n) => n.getAttribute("data-id") === "p_classify");
    expect(classifyNode).toBeDefined();
    expect(classifyNode!.getAttribute("data-kind")).toBe("llm");
    // The existing solid border style — no 'dashed' substring.
    expect(classifyNode!.getAttribute("data-border")).not.toContain("dashed");
  });
});

describe("DagView — LangGraph edge style", () => {
  it("renders langgraph edges as solid lines (not dashed)", () => {
    renderWithProviders(
      <DagView pipeline={LANGGRAPH_PIPELINE} siteIndex={EMPTY_SITE_INDEX} />,
    );
    const edges = screen.getAllByTestId("rf-edge");
    expect(edges.length).toBe(2);
    for (const edge of edges) {
      // LangGraph edges are declarative — no dashed treatment.
      expect(edge.getAttribute("data-dasharray")).toBe("");
    }
  });
});
