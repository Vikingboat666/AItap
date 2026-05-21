/**
 * AutoIterateModal — mode toggle, mode-specific input validation,
 * and the happy POST /api/iterate path.
 *
 * The modal is the user-facing gate to a Wave 4 session. We assert:
 *   1. The three modes (auto / guided / manual) toggle the inputs that
 *      gate the Start button.
 *   2. Empty guided instruction => Start disabled.
 *   3. Empty manual text => Start disabled.
 *   4. Auto mode + valid prompt/dataset + click Start => POST issued
 *      with the right payload + onStart fires with the new session.
 */
import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import { AutoIterateModal } from "../components/AutoIterateModal";
import { renderWithProviders, screen, waitFor } from "../test-utils/render";
import { server } from "../setupTests";
import { iterateSessionRunningFixture } from "../test-utils/handlers";

describe("AutoIterateModal", () => {
  it("toggles between auto / guided / manual modes and reveals the right input", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <AutoIterateModal
        promptId="p_test_alpha"
        datasetId="ds_alpha"
        onClose={() => {}}
        onStart={() => {}}
      />,
    );

    // Initial state — auto is the default; neither input is rendered.
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.queryByLabelText(/instruction/i)).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText(/round 2 prompt text/i),
    ).not.toBeInTheDocument();

    // Switch to guided — the instruction input appears.
    await user.click(screen.getByRole("button", { name: "guided" }));
    expect(screen.getByLabelText(/instruction/i)).toBeInTheDocument();

    // Switch to manual — the textarea appears, the instruction input
    // disappears (only one mode-specific input is rendered at a time).
    await user.click(screen.getByRole("button", { name: "manual" }));
    expect(screen.queryByLabelText(/instruction/i)).not.toBeInTheDocument();
    expect(
      screen.getByLabelText(/round 2 prompt text/i),
    ).toBeInTheDocument();
  });

  it("disables Start when guided mode lacks a non-empty instruction", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <AutoIterateModal
        promptId="p_test_alpha"
        datasetId="ds_alpha"
        onClose={() => {}}
        onStart={() => {}}
      />,
    );

    await user.click(screen.getByRole("button", { name: "guided" }));

    const start = screen.getByRole("button", {
      name: /start auto-iterate/i,
    });
    expect(start).toBeDisabled();

    // Typing makes Start clickable again.
    await user.type(
      screen.getByLabelText(/instruction/i),
      "make it more professional",
    );
    expect(start).not.toBeDisabled();
  });

  it("disables Start when manual mode lacks a non-empty body", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <AutoIterateModal
        promptId="p_test_alpha"
        datasetId="ds_alpha"
        onClose={() => {}}
        onStart={() => {}}
      />,
    );

    await user.click(screen.getByRole("button", { name: "manual" }));
    const start = screen.getByRole("button", {
      name: /start auto-iterate/i,
    });
    expect(start).toBeDisabled();

    await user.type(
      screen.getByLabelText(/round 2 prompt text/i),
      "You are a precise assistant. Answer in two sentences.",
    );
    expect(start).not.toBeDisabled();
  });

  it("POSTs /api/iterate on Start and fires onStart with the session", async () => {
    const user = userEvent.setup();
    const onStart = vi.fn();

    // Intercept the POST so we can assert the payload + return a 202
    // shape the client unwraps successfully.
    let captured: Record<string, unknown> | null = null;
    server.use(
      http.post("/api/iterate", async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(iterateSessionRunningFixture, { status: 202 });
      }),
    );

    renderWithProviders(
      <AutoIterateModal
        promptId="p_test_alpha"
        datasetId="ds_alpha"
        onClose={() => {}}
        onStart={onStart}
      />,
    );

    await user.click(
      screen.getByRole("button", { name: /start auto-iterate/i }),
    );

    await waitFor(() => {
      expect(onStart).toHaveBeenCalledTimes(1);
    });
    expect(onStart).toHaveBeenCalledWith(iterateSessionRunningFixture);
    expect(captured).toMatchObject({
      prompt_id: "p_test_alpha",
      dataset_id: "ds_alpha",
      mode: "auto",
    });
  });
});
