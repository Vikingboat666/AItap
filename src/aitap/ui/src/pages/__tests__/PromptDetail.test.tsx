/**
 * PromptDetail — loading skeleton, versions list, diff button affordance.
 *
 * The diff button is disabled when a version has no parent_version, and
 * enabled when it does. Asserting both states catches off-by-one bugs
 * in the lookup map (`versionByNumber.get(parent_version)`).
 */
import { describe, expect, it } from "vitest";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import { PromptDetail } from "../PromptDetail";
import { renderWithProviders, screen } from "../../test-utils/render";
import { server } from "../../setupTests";

describe("PromptDetail", () => {
  it("shows skeleton, then renders the version list with a clickable diff button", async () => {
    renderWithProviders(<PromptDetail />, {
      route: "/prompts/p_test_alpha",
      path: "/prompts/:id",
    });

    // 1. Loading skeleton is up before MSW resolves.
    expect(document.querySelector('[aria-busy="true"]')).not.toBeNull();

    // 2. Success — site name + both versions render.
    expect(await screen.findByText("alpha_prompt")).toBeInTheDocument();
    expect(screen.getByText("v1")).toBeInTheDocument();
    expect(screen.getByText("v2")).toBeInTheDocument();

    // v1 has no parent_version => diff button disabled.
    // v2 has parent_version=1 => diff button enabled and labels v1.
    const diffButtons = screen.getAllByRole("button", { name: /^diff/i });
    expect(diffButtons).toHaveLength(2);

    // The button rendered next to v1 has the title hinting "no parent".
    const disabledDiff = diffButtons.find((b) =>
      (b.getAttribute("title") ?? "").includes("no parent"),
    );
    expect(disabledDiff).toBeDefined();
    expect(disabledDiff).toBeDisabled();

    // The button next to v2 references v1 in its label/title.
    const enabledDiff = diffButtons.find((b) =>
      (b.getAttribute("title") ?? "").startsWith("diff v1"),
    );
    expect(enabledDiff).toBeDefined();
    expect(enabledDiff).not.toBeDisabled();

    // 3. Click the enabled diff button — modal opens with the CLI hint.
    await userEvent.click(enabledDiff!);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(
      screen.getByText(/aitap diff p_test_alpha 1 2/),
    ).toBeInTheDocument();
  });

  it("renders ErrorState and offers retry when the detail endpoint fails", async () => {
    server.use(
      http.get(
        "/api/prompts/:promptId",
        () => new HttpResponse(null, { status: 500 }),
        { once: true },
      ),
    );

    renderWithProviders(<PromptDetail />, {
      route: "/prompts/p_test_alpha",
      path: "/prompts/:id",
    });

    expect(
      await screen.findByText(/couldn't load prompt/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /retry/i }),
    ).toBeInTheDocument();
  });
});
