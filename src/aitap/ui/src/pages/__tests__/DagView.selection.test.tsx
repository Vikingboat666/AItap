/**
 * DagView — node-selection highlight + click toggling (A·D2).
 *
 * The real DAG renders through ReactFlow, whose SVG only paints once it
 * has measured DOM dimensions jsdom never provides. We stub ReactFlow
 * with a minimal DOM mirror (same pattern as PipelineDetail.test) so we
 * can assert:
 *   - selected nodes carry a `data-selected="true"` marker derived from
 *     the `selectedNodeIds` prop (the on-screen highlight is driven off
 *     the same flag, so a regression here desyncs the highlight),
 *   - clicking a node forwards its id through `onNodeClick`.
 *
 * Selection *state* (single vs multi, toggling) is owned by the parent
 * (Playground), so it is asserted in Playground.segment.test; DagView is
 * a controlled component — it only reflects `selectedNodeIds` and reports
 * clicks.
 */
import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";

// Capture the nodes DagView hands to ReactFlow and surface the
// selection-relevant bits (id + a boolean selected flag) into flat DOM.
type NodeLike = {
  id: string;
  data?: { label?: string; selected?: boolean };
  style?: Record<string, unknown>;
};
type EdgeLike = { id: string };

vi.mock("reactflow", async () => {
  const React = await import("react");
  function ReactFlow({
    nodes,
    onNodeClick,
  }: {
    nodes: NodeLike[];
    edges: EdgeLike[];
    onNodeClick?: (e: unknown, n: NodeLike) => void;
  }) {
    return React.createElement(
      "ul",
      { "data-testid": "rf-nodes" },
      nodes.map((n) =>
        React.createElement(
          "li",
          { key: n.id },
          React.createElement(
            "button",
            {
              type: "button",
              "data-testid": "rf-node",
              "data-id": n.id,
              "data-selected": n.data?.selected ? "true" : "false",
              onClick: () => onNodeClick?.({}, n),
            },
            n.data?.label ?? n.id,
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
import { pipelineDetailFixture } from "../../test-utils/handlers";

const { pipeline, site_index } = pipelineDetailFixture;

describe("DagView selection", () => {
  it("marks nodes in selectedNodeIds as selected", () => {
    renderWithProviders(
      <DagView
        pipeline={pipeline}
        siteIndex={site_index}
        selectedNodeIds={["p_test_alpha"]}
      />,
    );

    const nodes = screen.getAllByTestId("rf-node");
    const alpha = nodes.find((n) => n.getAttribute("data-id") === "p_test_alpha");
    const beta = nodes.find((n) => n.getAttribute("data-id") === "p_test_beta");
    expect(alpha?.getAttribute("data-selected")).toBe("true");
    expect(beta?.getAttribute("data-selected")).toBe("false");
  });

  it("marks no node when selectedNodeIds is empty/omitted", () => {
    renderWithProviders(
      <DagView pipeline={pipeline} siteIndex={site_index} />,
    );
    for (const node of screen.getAllByTestId("rf-node")) {
      expect(node.getAttribute("data-selected")).toBe("false");
    }
  });

  it("forwards the clicked node id through onNodeClick", async () => {
    const user = userEvent.setup();
    const onNodeClick = vi.fn();
    renderWithProviders(
      <DagView
        pipeline={pipeline}
        siteIndex={site_index}
        onNodeClick={onNodeClick}
      />,
    );

    const beta = screen
      .getAllByTestId("rf-node")
      .find((n) => n.getAttribute("data-id") === "p_test_beta")!;
    await user.click(beta);
    expect(onNodeClick).toHaveBeenCalledTimes(1);
    expect(onNodeClick).toHaveBeenCalledWith("p_test_beta");
  });
});
