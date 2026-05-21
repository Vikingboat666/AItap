/**
 * IterationTimeline — list + expand behaviour.
 *
 * Asserts:
 *   1. Empty list → empty-state copy is rendered.
 *   2. Two-round session → one collapsed row showing round count +
 *      final version + converged_reason badge.
 *   3. Expanding the row reveals the per-round list.
 */
import { describe, expect, it } from "vitest";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import { IterationTimeline } from "../components/IterationTimeline";
import { renderWithProviders, screen } from "../test-utils/render";
import { server } from "../setupTests";
import {
  iterationBaselineFixture,
  iterationRound2Fixture,
} from "../test-utils/handlers";

describe("IterationTimeline", () => {
  it("renders empty-state copy when no iterations exist for the prompt", async () => {
    server.use(
      http.get(
        "/api/iterations/by-prompt/p_test_alpha",
        () => HttpResponse.json([]),
      ),
    );
    renderWithProviders(<IterationTimeline promptId="p_test_alpha" />);
    expect(
      await screen.findByText(/no sessions recorded yet/i),
    ).toBeInTheDocument();
  });

  it("groups rows by session and surfaces the round count + converged reason", async () => {
    server.use(
      http.get(
        "/api/iterations/by-prompt/p_test_alpha",
        () =>
          HttpResponse.json([iterationRound2Fixture, iterationBaselineFixture]),
      ),
    );
    renderWithProviders(<IterationTimeline promptId="p_test_alpha" />);

    // Session row header — round count + final version.
    expect(await screen.findByText(/2 rounds/i)).toBeInTheDocument();
    expect(screen.getByText(/final v2/i)).toBeInTheDocument();

    // Status badge ("converged") and converged_reason ("delta") render
    // as separate badges next to the truncated session id.
    expect(
      screen.getByText(/converged/i, { selector: "span" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/delta/i, { selector: "span" }),
    ).toBeInTheDocument();
  });

  it("expands the row to show the per-round list", async () => {
    const user = userEvent.setup();
    server.use(
      http.get(
        "/api/iterations/by-prompt/p_test_alpha",
        () =>
          HttpResponse.json([iterationRound2Fixture, iterationBaselineFixture]),
      ),
    );
    renderWithProviders(<IterationTimeline promptId="p_test_alpha" />);

    // Wait for the row to mount.
    const row = await screen.findByRole("button", { expanded: false });
    await user.click(row);

    // After expanding, both rounds appear as list items. The per-round
    // line splits "round" and the number across separate text nodes
    // (`<span>round {it.round}</span>`), so we match with a function
    // matcher over the combined textContent rather than a regex over a
    // single text node.
    const matchRound = (n: number) => (_: string, el: Element | null) =>
      el?.tagName === "SPAN" && el.textContent === `round ${n}`;
    expect(
      await screen.findByText(matchRound(1)),
    ).toBeInTheDocument();
    expect(screen.getByText(matchRound(2))).toBeInTheDocument();
    // Baseline is labelled as such — the rounds list renders an exact
    // "baseline" span next to round 1; chart `<title>` elements also
    // contain the word, so we restrict to the list span via tag.
    const baselineLabels = screen
      .getAllByText(/baseline/i)
      .filter((el) => el.tagName === "SPAN");
    expect(baselineLabels.length).toBeGreaterThan(0);
  });
});
