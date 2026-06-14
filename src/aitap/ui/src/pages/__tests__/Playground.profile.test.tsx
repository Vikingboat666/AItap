/**
 * Playground — multi-provider profile picker (A2-P2).
 *
 * Three cases lock down the wire shape:
 *   1. The default profile from ``settings.defaults.model_profile_id``
 *      auto-seeds the picker and rides into the POST as ``profile_id``.
 *   2. Picking the "use legacy" option sends ``profile_id: null`` so the
 *      backend falls through to its existing provider/model dispatch.
 *   3. The empty-profiles state renders the plain-language pointer at
 *      Settings (no picker, no extra wire fields).
 *
 * Uses the pipeline + end-to-end target (mirroring Playground.segment.test)
 * because pipeline runs don't require case-input wiring — the picker
 * assertions are about wire shape, not target validation.
 */
import { describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import userEvent from "@testing-library/user-event";

type NodeLike = { id: string; data?: { label?: string; selected?: boolean } };

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
  runDetailFixture,
  settingsFixture,
} from "../../test-utils/handlers";

function installRunCapture(): { body: Record<string, unknown> | null } {
  const captured: { body: Record<string, unknown> | null } = { body: null };
  server.use(
    http.post("/api/runs", async ({ request }) => {
      captured.body = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json({ run_id: "run_test_profile", status: "running" });
    }),
    http.get("/api/runs/:runId", () =>
      HttpResponse.json({ ...runDetailFixture, run_id: "run_test_profile" }),
    ),
  );
  return captured;
}

async function switchToEndToEndAndRun(): Promise<void> {
  // Pipeline targets default to node mode (needs a selection). Switch
  // to end-to-end so Run enables with no further input.
  await userEvent.click(screen.getByRole("button", { name: /^end-to-end$/i }));
  const runBtn = screen.getByRole("button", { name: /^run$/i });
  await waitFor(() => expect(runBtn).toBeEnabled());
  await userEvent.click(runBtn);
}

describe("Playground — multi-provider profile picker (A2-P2)", () => {
  it("auto-seeds the picker from settings.defaults and POSTs profile_id", async () => {
    const captured = installRunCapture();
    renderWithProviders(<Playground />, {
      route: "/playground/pipeline/pl_test_one",
      path: "/playground/:targetKind/:targetId",
    });

    const select = (await screen.findByLabelText(/use profile/i)) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("prof_default"));

    await switchToEndToEndAndRun();
    await waitFor(() => expect(captured.body).not.toBeNull());
    expect(captured.body!.profile_id).toBe("prof_default");
  });

  it("switching to 'use legacy' posts profile_id: null", async () => {
    const captured = installRunCapture();
    renderWithProviders(<Playground />, {
      route: "/playground/pipeline/pl_test_one",
      path: "/playground/:targetKind/:targetId",
    });

    const select = (await screen.findByLabelText(/use profile/i)) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("prof_default"));
    await userEvent.selectOptions(select, "");
    expect(select.value).toBe("");

    await switchToEndToEndAndRun();
    await waitFor(() => expect(captured.body).not.toBeNull());
    expect(captured.body!.profile_id).toBeNull();
    // Legacy fallback still rides on the wire so the backend has the
    // provider/model to dispatch with.
    expect(captured.body!.provider).toBeDefined();
    expect(captured.body!.model).toBeDefined();
  });

  it("renders the plain-language empty state when no profiles exist", async () => {
    server.use(
      http.get("/api/profiles", () => HttpResponse.json([])),
      // No configured default — seeding logic should leave the picker on
      // the legacy fallback.
      http.get("/api/settings", () =>
        HttpResponse.json({
          ...settingsFixture,
          defaults: { model_profile_id: null, judge_profile_id: null },
        }),
      ),
    );
    renderWithProviders(<Playground />, {
      route: "/playground/pipeline/pl_test_one",
      path: "/playground/:targetKind/:targetId",
    });

    // The empty-state text from i18n; we assert by the "Open Settings"
    // pointer that CLAUDE.md plain-language compliance requires.
    expect(
      await screen.findByText(/Open Settings to add one/i),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText(/use profile/i)).toBeNull();
  });
});
