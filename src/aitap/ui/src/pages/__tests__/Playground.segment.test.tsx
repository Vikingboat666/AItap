/**
 * Playground — pipeline run-mode selection (M5 segment, A·D2 / A·D3).
 *
 * Covers the parent-owned selection logic that DagView only reflects:
 *   - node / segment / end-to-end each POST the right `pipeline_mode`
 *     (wire enum, underscore) plus *only* its matching selector field,
 *   - an empty selection disables Run (zero-node segment footgun, A·D3),
 *   - switching mode clears the selection so a stale node_id/segment can't
 *     ride along and trip the backend's conflicting-selector 422,
 *   - a non-contiguous segment shows a non-blocking warning but still runs.
 *
 * ReactFlow is stubbed (same approach as PipelineDetail/DagView tests):
 * jsdom never measures DOM so the real SVG never paints. The stub renders
 * each node as a button carrying `data-id` + `data-selected` and forwards
 * clicks through `onNodeClick`.
 */
import { describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import userEvent from "@testing-library/user-event";

type NodeLike = {
  id: string;
  data?: { label?: string; selected?: boolean };
};

vi.mock("reactflow", async () => {
  const React = await import("react");
  function ReactFlow({
    nodes,
    onNodeClick,
  }: {
    nodes: NodeLike[];
    edges: unknown[];
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

import { Playground } from "../Playground";
import {
  renderWithProviders,
  screen,
  waitFor,
} from "../../test-utils/render";
import { server } from "../../setupTests";
import {
  pipelineDetailFixture,
  pipelineDisconnectedFixture,
  runDetailFixture,
  settingsFixture,
} from "../../test-utils/handlers";

/** Install settings + runs handlers; returns a ref that captures the
 *  POST /api/runs request body so assertions can inspect the wire shape. */
function installRunCapture(): { body: Record<string, unknown> | null } {
  const captured: { body: Record<string, unknown> | null } = { body: null };
  server.use(
    http.get("/api/settings", () => HttpResponse.json(settingsFixture)),
    http.post("/api/runs", async ({ request }) => {
      captured.body = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json({ run_id: "run_test_one", status: "running" });
    }),
    http.get("/api/runs/:runId", () => HttpResponse.json(runDetailFixture)),
  );
  return captured;
}

function nodeButton(id: string): HTMLElement {
  const found = screen
    .getAllByTestId("rf-node")
    .find((n) => n.getAttribute("data-id") === id);
  if (!found) throw new Error(`node button ${id} not rendered`);
  return found;
}

/** Wait for a node button to render (the DAG paints only after the
 *  pipeline-detail query resolves), then click it. */
async function clickNode(id: string): Promise<void> {
  const btn = await waitFor(() => nodeButton(id));
  await userEvent.click(btn);
}

function runButton(): HTMLElement {
  return screen.getByRole("button", { name: /^run$/i });
}

describe("Playground — pipeline run-mode selection", () => {
  it("node mode: a node must be picked, then POSTs pipeline_mode=node + node_id only", async () => {
    const captured = installRunCapture();
    renderWithProviders(<Playground />, {
      route: "/playground/pipeline/pl_test_one",
      path: "/playground/:targetKind/:targetId",
    });

    // Picker renders once the pipeline detail loads. Default mode is node.
    await screen.findByText(/pick a node/i);

    // No node picked yet → Run disabled (A·D3-style empty guard).
    expect(runButton()).toBeDisabled();

    await clickNode("p_test_alpha");
    expect(nodeButton("p_test_alpha").getAttribute("data-selected")).toBe(
      "true",
    );
    expect(runButton()).toBeEnabled();

    await userEvent.click(runButton());
    await waitFor(() => expect(captured.body).not.toBeNull());

    expect(captured.body).toMatchObject({
      target_kind: "pipeline",
      pipeline_mode: "node",
      pipeline_node_id: "p_test_alpha",
      pipeline_segment: null,
    });
  });

  it("segment mode: multi-select POSTs pipeline_mode=segment + segment only (no node_id)", async () => {
    const captured = installRunCapture();
    renderWithProviders(<Playground />, {
      route: "/playground/pipeline/pl_test_one",
      path: "/playground/:targetKind/:targetId",
    });

    await screen.findByText(/pick a node/i);
    await userEvent.click(screen.getByRole("button", { name: /^segment$/i }));
    await screen.findByText(/pick a segment/i);

    // Empty segment → Run disabled.
    expect(runButton()).toBeDisabled();

    await clickNode("p_test_alpha");
    await clickNode("p_test_beta");
    expect(runButton()).toBeEnabled();

    await userEvent.click(runButton());
    await waitFor(() => expect(captured.body).not.toBeNull());

    expect(captured.body).toMatchObject({
      pipeline_mode: "segment",
      pipeline_segment: ["p_test_alpha", "p_test_beta"],
      pipeline_node_id: null,
    });
  });

  it("end-to-end mode: runs with no selection and POSTs the underscore wire enum", async () => {
    const captured = installRunCapture();
    renderWithProviders(<Playground />, {
      route: "/playground/pipeline/pl_test_one",
      path: "/playground/:targetKind/:targetId",
    });

    await screen.findByText(/pick a node/i);
    await userEvent.click(
      screen.getByRole("button", { name: /^end-to-end$/i }),
    );

    // No picker, no selection needed → Run is immediately enabled.
    expect(runButton()).toBeEnabled();

    await userEvent.click(runButton());
    await waitFor(() => expect(captured.body).not.toBeNull());

    expect(captured.body).toMatchObject({
      pipeline_mode: "end_to_end",
      pipeline_node_id: null,
      pipeline_segment: null,
    });
  });

  it("switching mode clears the selection so no stale selector is sent", async () => {
    const captured = installRunCapture();
    renderWithProviders(<Playground />, {
      route: "/playground/pipeline/pl_test_one",
      path: "/playground/:targetKind/:targetId",
    });

    await screen.findByText(/pick a node/i);
    // Pick a node in node mode…
    await clickNode("p_test_alpha");
    expect(runButton()).toBeEnabled();

    // …switch to segment: selection must reset (Run disabled again).
    await userEvent.click(screen.getByRole("button", { name: /^segment$/i }));
    await screen.findByText(/pick a segment/i);
    expect(runButton()).toBeDisabled();
    expect(nodeButton("p_test_alpha").getAttribute("data-selected")).toBe(
      "false",
    );

    // Now pick a fresh segment and run — body carries segment, never the
    // node_id from the earlier node-mode pick.
    await clickNode("p_test_beta");
    await userEvent.click(runButton());
    await waitFor(() => expect(captured.body).not.toBeNull());
    expect(captured.body).toMatchObject({
      pipeline_mode: "segment",
      pipeline_segment: ["p_test_beta"],
      pipeline_node_id: null,
    });
  });

  it("non-contiguous segment warns but still allows Run", async () => {
    installRunCapture();
    // Serve the split fixture (alpha->beta, gamma island) for this id.
    server.use(
      http.get("/api/pipelines/:pipelineId", ({ params }) =>
        params.pipelineId === "pl_test_split"
          ? HttpResponse.json(pipelineDisconnectedFixture)
          : HttpResponse.json(pipelineDetailFixture),
      ),
    );

    renderWithProviders(<Playground />, {
      route: "/playground/pipeline/pl_test_split",
      path: "/playground/:targetKind/:targetId",
    });

    await screen.findByText(/pick a node/i);
    await userEvent.click(screen.getByRole("button", { name: /^segment$/i }));
    await screen.findByText(/pick a segment/i);

    // alpha + gamma span two components → warning, but Run stays enabled.
    await clickNode("p_test_alpha");
    await clickNode("p_test_gamma");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /aren't connected/i,
    );
    expect(runButton()).toBeEnabled();
  });

  it("a contiguous segment shows no connectivity warning", async () => {
    installRunCapture();
    renderWithProviders(<Playground />, {
      route: "/playground/pipeline/pl_test_one",
      path: "/playground/:targetKind/:targetId",
    });

    await screen.findByText(/pick a node/i);
    await userEvent.click(screen.getByRole("button", { name: /^segment$/i }));
    await screen.findByText(/pick a segment/i);

    await clickNode("p_test_alpha");
    await clickNode("p_test_beta");

    expect(screen.queryByRole("alert")).toBeNull();
    expect(runButton()).toBeEnabled();
  });
});
