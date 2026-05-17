/**
 * Inventory page — loading / success (dual-tab) / error+retry coverage.
 *
 * Why this matters: Inventory is the first page a user sees. If react-
 * query's loading skeleton fails to render, or the prompts/pipelines tab
 * switch breaks, the rest of the app is unreachable. We assert on:
 *   1. Initial loading skeleton (aria-busy).
 *   2. Both tabs render their respective lists once data resolves.
 *   3. A failing `/api/prompts` flips into ErrorState, and clicking
 *      retry triggers a successful refetch.
 */
import { describe, expect, it } from "vitest";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import { Inventory } from "../Inventory";
import { renderWithProviders, screen, waitFor } from "../../test-utils/render";
import { server } from "../../setupTests";
import { promptListFixture } from "../../test-utils/handlers";

describe("Inventory", () => {
  it("shows the loading skeleton, then renders prompts and pipelines tabs", async () => {
    renderWithProviders(<Inventory />);

    // 1. Loading — the ListSkeleton sets aria-busy on its <Card>.
    expect(
      document.querySelector('[aria-busy="true"]'),
    ).not.toBeNull();

    // 2. Success — prompts tab shows by default.
    expect(
      await screen.findByText("alpha_prompt"),
    ).toBeInTheDocument();
    expect(screen.getByText("beta_prompt")).toBeInTheDocument();
    // Tab counts surface in the buttons.
    expect(screen.getByRole("button", { name: /prompts/i })).toHaveTextContent(
      String(promptListFixture.prompts.length),
    );

    // 3. Tab switch — pipelines list reveals.
    await userEvent.click(screen.getByRole("button", { name: /pipelines/i }));
    expect(
      await screen.findByText("test pipeline one"),
    ).toBeInTheDocument();
    // Pipeline meta line includes node/edge counts.
    expect(
      screen.getByText(/2 nodes · 1 edges/i),
    ).toBeInTheDocument();
  });

  it("renders ErrorState when /api/prompts fails and recovers on retry", async () => {
    // First load — force /api/prompts to fail; pipelines succeeds.
    server.use(
      http.get(
        "/api/prompts",
        () => new HttpResponse(null, { status: 500 }),
        { once: true },
      ),
    );

    renderWithProviders(<Inventory />);

    // Error UI renders the title we passed to <ErrorState>.
    expect(
      await screen.findByText(/couldn't load prompts/i),
    ).toBeInTheDocument();
    const retry = screen.getByRole("button", { name: /retry/i });
    expect(retry).toBeInTheDocument();

    // After retry, the default handler (success) kicks in.
    await userEvent.click(retry);
    await waitFor(() => {
      expect(screen.queryByText(/couldn't load prompts/i)).not.toBeInTheDocument();
    });
    expect(await screen.findByText("alpha_prompt")).toBeInTheDocument();
  });
});
