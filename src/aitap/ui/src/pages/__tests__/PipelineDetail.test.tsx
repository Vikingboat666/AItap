/**
 * PipelineDetail — loading, DagView rendering (edge style check), error retry.
 *
 * The DAG itself is rendered via ReactFlow. ReactFlow's SVG edge paths
 * only paint once it has measured real DOM dimensions — something jsdom
 * does not provide. We mock ReactFlow with a minimal stub that simply
 * renders the props it received as plain DOM, which lets us:
 *   - assert that one edge made it through with the correct `stroke`
 *     color derived from `styleForKind("variable")` — that mapping is
 *     the source of truth for the on-screen legend, so a regression
 *     here would silently desync the legend from what users actually
 *     see when running against a real browser.
 *   - assert the legend text is rendered on the page (it lives outside
 *     ReactFlow, so the real component path runs).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";

// Capture the edges the page hands to ReactFlow. The stub also forwards
// the markup-relevant pieces (style, label) into a flat DOM tree so
// assertions can use plain selectors.
const mockEdgesCapture: { current: Array<{ id: string; style: { stroke?: string; strokeDasharray?: string }; label?: string }> } = { current: [] };

vi.mock("reactflow", async () => {
  const React = await import("react");
  type EdgeLike = { id: string; style?: { stroke?: string; strokeDasharray?: string }; label?: string };
  type NodeLike = { id: string; data?: { label?: string } };
  function ReactFlow({ nodes, edges }: { nodes: NodeLike[]; edges: EdgeLike[] }) {
    mockEdgesCapture.current = edges.map((e) => ({
      id: e.id,
      style: { stroke: e.style?.stroke, strokeDasharray: e.style?.strokeDasharray },
      label: typeof e.label === "string" ? e.label : undefined,
    }));
    return React.createElement(
      "div",
      { "data-testid": "rf-stub" },
      React.createElement(
        "ul",
        { "data-testid": "rf-nodes" },
        nodes.map((n) =>
          React.createElement(
            "li",
            { key: n.id, "data-id": n.id },
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
              "data-stroke": e.style?.stroke,
              "data-dasharray": e.style?.strokeDasharray ?? "",
            },
            typeof e.label === "string" ? e.label : "",
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

import { PipelineDetail } from "../PipelineDetail";
import { renderWithProviders, screen } from "../../test-utils/render";
import { server } from "../../setupTests";

describe("PipelineDetail", () => {
  beforeEach(() => {
    mockEdgesCapture.current = [];
  });
  afterEach(() => {
    mockEdgesCapture.current = [];
  });

  it("renders loading skeleton, then the DAG with a solid 'variable' edge", async () => {
    renderWithProviders(<PipelineDetail />, {
      route: "/pipelines/pl_test_one",
      path: "/pipelines/:id",
    });

    // 1. Loading — BlockSkeleton sets aria-busy.
    expect(document.querySelector('[aria-busy="true"]')).not.toBeNull();

    // 2. Success — header shows the pipeline name.
    expect(
      await screen.findByText("test pipeline one"),
    ).toBeInTheDocument();

    // 3. Edge style check on the captured edges. Fixture edge is
    // kind=variable, which styleForKind() maps to a solid "#475dff"
    // line with no dasharray. We assert exactly that here so the
    // legend on the page stays in sync with what ReactFlow renders.
    const edges = screen.getAllByTestId("rf-edge");
    expect(edges).toHaveLength(1);
    expect(edges[0].getAttribute("data-stroke")?.toLowerCase()).toBe("#475dff");
    expect(edges[0].getAttribute("data-dasharray")).toBe("");
    // The stub also captures the edge in a mutable ref — sanity check.
    expect(mockEdgesCapture.current).toHaveLength(1);
    expect(mockEdgesCapture.current[0].style.stroke?.toLowerCase()).toBe(
      "#475dff",
    );
    // Edge label comes from `via` ("payload" in the fixture).
    expect(edges[0]).toHaveTextContent("payload");

    // Legend reflects the same mapping — rendered outside ReactFlow.
    expect(
      screen.getByText(/solid: resolved dataflow/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/dashed: inferred/i),
    ).toBeInTheDocument();
  });

  it("renders ErrorState and offers retry when the pipeline endpoint fails", async () => {
    server.use(
      http.get(
        "/api/pipelines/:pipelineId",
        () => new HttpResponse(null, { status: 500 }),
        { once: true },
      ),
    );

    renderWithProviders(<PipelineDetail />, {
      route: "/pipelines/pl_test_one",
      path: "/pipelines/:id",
    });

    expect(
      await screen.findByText(/couldn't load pipeline/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /retry/i }),
    ).toBeInTheDocument();
  });
});
