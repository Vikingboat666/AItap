/**
 * DownstreamImpactBanner — render / dismiss behaviour.
 *
 * The banner is pure-presentational (no fetch), so tests poke at the
 * three states directly:
 *   1. No downstream status → null render (banner absent).
 *   2. Some unverified nodes → headline + three action buttons.
 *   3. Skip click → calls onDismiss; if the parent flips `dismissed`,
 *      the banner unmounts on the next render.
 */
import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";

import { DownstreamImpactBanner } from "../components/DownstreamImpactBanner";
import { renderWithProviders, screen } from "../test-utils/render";

describe("DownstreamImpactBanner", () => {
  it("renders nothing when downstreamStatus is null or all nodes resolved", () => {
    const { rerender } = renderWithProviders(
      <DownstreamImpactBanner downstreamStatus={null} />,
    );
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    // All nodes verified — also nothing to surface.
    rerender(
      <DownstreamImpactBanner
        downstreamStatus={{ draft: "verified", polish: "verified" }}
      />,
    );
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("renders the unverified headline and three action buttons", () => {
    renderWithProviders(
      <DownstreamImpactBanner
        downstreamStatus={{ draft: "unverified", polish: "unverified" }}
      />,
    );
    expect(
      screen.getByText(/2 downstream nodes unverified/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/draft, polish/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /skip/i })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /re-run all/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /re-run selected/i }),
    ).toBeInTheDocument();
    // M5 placeholders are disabled with the tooltip.
    expect(screen.getByRole("button", { name: /re-run all/i })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: /re-run selected/i }),
    ).toBeDisabled();
  });

  it("fires onDismiss when Skip is clicked", async () => {
    const user = userEvent.setup();
    const onDismiss = vi.fn();
    renderWithProviders(
      <DownstreamImpactBanner
        downstreamStatus={{ draft: "unverified" }}
        onDismiss={onDismiss}
      />,
    );

    await user.click(screen.getByRole("button", { name: /skip/i }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("self-hides when dismissed=true even if unverified nodes remain", () => {
    renderWithProviders(
      <DownstreamImpactBanner
        downstreamStatus={{ draft: "unverified" }}
        dismissed
      />,
    );
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(
      screen.queryByText(/downstream node/i),
    ).not.toBeInTheDocument();
  });
});
