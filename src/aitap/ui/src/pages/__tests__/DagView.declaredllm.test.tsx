/**
 * DagView — declared_llm node + CrewAI edge rendering (B2-CrewAI).
 *
 * Adds a third visual treatment alongside the existing two:
 *
 *   - ``kind === "llm"`` → solid blue border + 1.0 opacity (baseline)
 *   - ``kind === "non_llm"`` → dashed grey border + 0.65 opacity
 *     (LangGraph add_node("parse", parse_json) — doesn't cost tokens)
 *   - ``kind === "declared_llm"`` → solid green border #16a34a +
 *     full opacity (CrewAI Task(description=…) — real LLM call,
 *     prompt lives inside the framework so no PromptSite link)
 *
 * ``EdgeKind.CREWAI`` edges get their own colour too (also green,
 * matched to the node treatment so the framework reads as one
 * visual unit).
 */
import { describe, expect, it, vi } from "vitest";

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

const CREWAI_PIPELINE: Pipeline = {
  id: "crew_test",
  name: "crewai:crew",
  nodes: [
    {
      prompt_id: "crewai:app.py:crew:research",
      label: "Research X (Researcher)",
      kind: "declared_llm",
    },
    {
      prompt_id: "crewai:app.py:crew:write",
      label: "Write blog post (Writer)",
      kind: "declared_llm",
    },
  ],
  edges: [
    {
      source: "crewai:app.py:crew:research",
      target: "crewai:app.py:crew:write",
      kind: "crewai",
      via: "app.py::Crew(crew)",
      confidence: "high",
    },
  ],
  entry_points: ["crewai:app.py:crew:research"],
  exit_points: ["crewai:app.py:crew:write"],
};

const EMPTY_SITE_INDEX = {} as never;

describe("DagView — declared_llm node kind", () => {
  it("renders declared_llm nodes with a solid green border at full opacity", () => {
    renderWithProviders(
      <DagView pipeline={CREWAI_PIPELINE} siteIndex={EMPTY_SITE_INDEX} />,
    );
    const researchNode = screen
      .getAllByTestId("rf-node")
      .find((n) => n.getAttribute("data-id")?.includes("research"));
    expect(researchNode).toBeDefined();
    expect(researchNode!.getAttribute("data-kind")).toBe("declared_llm");
    const border = researchNode!.getAttribute("data-border") ?? "";
    // Solid (not dashed) — distinct from non_llm.
    expect(border).not.toContain("dashed");
    // Green hue #16a34a — distinct from llm's #dde1e9 grey baseline.
    expect(border.toLowerCase()).toContain("#16a34a");
    // Full opacity — these nodes cost tokens, no dim treatment.
    const opacity = Number(researchNode!.getAttribute("data-opacity") || "1");
    expect(opacity).toBe(1);
  });
});

describe("DagView — CrewAI edge style", () => {
  it("renders crewai edges in the matching green stroke", () => {
    renderWithProviders(
      <DagView pipeline={CREWAI_PIPELINE} siteIndex={EMPTY_SITE_INDEX} />,
    );
    const edges = screen.getAllByTestId("rf-edge");
    expect(edges.length).toBe(1);
    const edge = edges[0];
    expect(edge.getAttribute("data-stroke")?.toLowerCase()).toContain(
      "#16a34a",
    );
    // CrewAI edges are explicit declarations — solid, not dashed.
    expect(edge.getAttribute("data-dasharray")).toBe("");
  });
});
