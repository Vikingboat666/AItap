/**
 * IterationProgress — status rendering + terminal-status callback.
 *
 * We avoid asserting on the React Query polling interval directly (it
 * has its own timer plumbing). Instead, we drive the *observable*
 * behaviour: the component fetches the session endpoint, the response
 * shape determines the rendered UI, and `onTerminal` fires exactly
 * once per terminal status.
 *
 * Cases:
 *   1. status=running → spinner + "running…" subtitle visible.
 *   2. status=converged → final version badge + reason copy visible.
 *   3. status=failed (critic_failed) → red error banner is rendered.
 *   4. onTerminal is called once when the session resolves to a
 *      terminal status (proxy for "polling stops").
 */
import { describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";

import { IterationProgress } from "../components/IterationProgress";
import { renderWithProviders, screen, waitFor } from "../test-utils/render";
import { server } from "../setupTests";
import {
  iterateSessionConvergedFixture,
  iterateSessionFailedFixture,
  iterateSessionRunningFixture,
  iterationBaselineFixture,
  iterationRound2Fixture,
} from "../test-utils/handlers";

function registerSessionHandlers(sessionId: string, payload: {
  session: Parameters<typeof HttpResponse.json>[0];
  latest: Parameters<typeof HttpResponse.json>[0];
}) {
  server.use(
    http.get(`/api/iterations/${sessionId}`, () =>
      HttpResponse.json(payload.session),
    ),
    http.get(`/api/iterations/${sessionId}/latest`, () =>
      HttpResponse.json(payload.latest),
    ),
  );
}

describe("IterationProgress", () => {
  it("renders the running spinner + 'running' header while the session is in flight", async () => {
    registerSessionHandlers("sess_test_alpha", {
      session: iterateSessionRunningFixture,
      latest: iterationBaselineFixture,
    });

    renderWithProviders(
      <IterationProgress
        sessionId="sess_test_alpha"
        pollIntervalMs={5_000}
      />,
    );

    expect(await screen.findByText(/auto-iterate/i)).toBeInTheDocument();
    expect(
      await screen.findByText(/running/i, { selector: "span" }),
    ).toBeInTheDocument();
    // The spinner has role=status with a known aria-label.
    expect(
      screen.getByRole("status", { name: /iteration in progress/i }),
    ).toBeInTheDocument();
  });

  it("renders converged badge + final version + reason copy on success", async () => {
    registerSessionHandlers("sess_test_alpha", {
      session: iterateSessionConvergedFixture,
      latest: iterationRound2Fixture,
    });

    const onTerminal = vi.fn();
    renderWithProviders(
      <IterationProgress
        sessionId="sess_test_alpha"
        pollIntervalMs={5_000}
        onTerminal={onTerminal}
      />,
    );

    // "converged" badge + final version (v2).
    expect(
      await screen.findByText(/converged/i, { selector: "span" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("v2")).toBeInTheDocument();

    // Delta-convergence copy from the REASON_COPY map.
    expect(
      await screen.findByText(/score improved past the delta threshold/i),
    ).toBeInTheDocument();

    // onTerminal fires once with the session.
    await waitFor(() => {
      expect(onTerminal).toHaveBeenCalledTimes(1);
    });
    expect(onTerminal.mock.calls[0][0].status).toBe("converged");
  });

  it("renders a red error banner when the session is failed (critic_failed)", async () => {
    registerSessionHandlers("sess_test_failed", {
      session: iterateSessionFailedFixture,
      latest: iterationBaselineFixture,
    });

    renderWithProviders(
      <IterationProgress
        sessionId="sess_test_failed"
        pollIntervalMs={5_000}
      />,
    );

    // role=alert is on the failure card.
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/the critic failed/i);
    expect(alert.className).toMatch(/rose/);

    // The "failed" status badge is also surfaced — rose tone now so it
    // visually matches the FailureBanner above (the tone class names
    // include `rose` for the err palette).
    const failedBadge = screen.getByText(/failed/i, { selector: "span" });
    expect(failedBadge).toBeInTheDocument();
    expect(failedBadge.className).toMatch(/rose/);
  });

  it("stops polling once the session reports a terminal status", async () => {
    // Drive polling with real timers but a tight interval (20ms) so the
    // test doesn't have to wait long. The session handler counts how
    // many times it's been called; once it has been called twice we
    // flip it to return a terminal status. After that observation the
    // polling should freeze — we assert the count plateaus by sampling
    // it at two well-spaced points.
    let sessionCalls = 0;
    server.use(
      http.get("/api/iterations/sess_polling_stop", () => {
        sessionCalls += 1;
        if (sessionCalls < 2) {
          return HttpResponse.json(iterateSessionRunningFixture);
        }
        return HttpResponse.json(iterateSessionConvergedFixture);
      }),
      http.get(
        "/api/iterations/sess_polling_stop/latest",
        () => HttpResponse.json(iterationBaselineFixture),
      ),
    );

    renderWithProviders(
      <IterationProgress
        sessionId="sess_polling_stop"
        pollIntervalMs={20}
      />,
    );

    // Wait until the terminal status renders (proxy for "we observed
    // converged"). After this the polling stop-condition kicks in.
    await screen.findByText(/converged/i, { selector: "span" });

    // Snapshot the call count at the moment polling should stop, then
    // wait long enough that — if the stop didn't work — many more
    // polls would have fired.
    const callsAfterConverged = sessionCalls;
    await new Promise((resolve) => setTimeout(resolve, 200));

    // Allow at most one trailing in-flight call after the terminal
    // observation (React Query may have one queued already); anything
    // more than that means the refetchInterval kept firing.
    expect(sessionCalls).toBeLessThanOrEqual(callsAfterConverged + 1);
  });

  it("stops polling /latest once the session reports failed", async () => {
    // failed-via-placeholder regression test. /latest 404s forever in
    // that scenario; before the fix the latest query polled
    // indefinitely because `converged_reason` was always null. The
    // sessionQ-based gate is the only thing that stops it.
    let latestCalls = 0;
    server.use(
      http.get("/api/iterations/sess_polling_failed", () =>
        HttpResponse.json(iterateSessionFailedFixture),
      ),
      http.get("/api/iterations/sess_polling_failed/latest", () => {
        latestCalls += 1;
        return new HttpResponse(
          JSON.stringify({ detail: "no rows" }),
          { status: 404, headers: { "content-type": "application/json" } },
        );
      }),
    );

    renderWithProviders(
      <IterationProgress
        sessionId="sess_polling_failed"
        pollIntervalMs={20}
      />,
    );

    // Wait for the failure banner to confirm we have observed failed.
    await screen.findByRole("alert");
    const callsAfterFailed = latestCalls;
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(latestCalls).toBeLessThanOrEqual(callsAfterFailed + 1);
  });
});
