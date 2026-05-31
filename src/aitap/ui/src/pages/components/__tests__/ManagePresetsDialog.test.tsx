/**
 * ManagePresetsDialog — preset editor tests.
 *
 * Covers:
 *
 * - Renders the current list as editable rows + Save / Reset / Close.
 * - Add row → Save sends the whole new list to PUT /api/profile-presets
 *   and propagates the saved list back via onChanged.
 * - Remove row → Save sends the shortened list.
 * - Reset → confirm dialog → DELETE /api/profile-presets → onChanged
 *   gets the seeded list back.
 * - Failure path: PUT 500 → the role="status" line renders the plain
 *   language error and onClose was NOT called (the dialog stays open).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import { ManagePresetsDialog } from "../ManagePresetsDialog";
import { renderWithProviders, screen, waitFor } from "../../../test-utils/render";
import { server } from "../../../setupTests";
import type { ProfilePreset } from "../../../api/profiles";

const seededFour: ProfilePreset[] = [
  {
    name: "Anthropic",
    base_url: "https://api.anthropic.com",
    protocol: "anthropic",
    model_id: "claude-sonnet-4-6",
  },
  {
    name: "OpenAI",
    base_url: "https://api.openai.com/v1",
    protocol: "openai-compat",
    model_id: "gpt-4o-mini",
  },
  {
    name: "DeepSeek",
    base_url: "https://api.deepseek.com/v1",
    protocol: "openai-compat",
    model_id: "deepseek-chat",
  },
  {
    name: "Groq",
    base_url: "https://api.groq.com/openai/v1",
    protocol: "openai-compat",
    model_id: "llama-3.1-70b-versatile",
  },
];

describe("ManagePresetsDialog", () => {
  afterEach(() => {
    server.resetHandlers();
  });

  it("renders one row per preset with editable fields", () => {
    renderWithProviders(
      <ManagePresetsDialog
        presets={seededFour}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    expect(screen.getByDisplayValue("Anthropic")).toBeInTheDocument();
    expect(
      screen.getByDisplayValue("https://api.openai.com/v1"),
    ).toBeInTheDocument();
    expect(screen.getByDisplayValue("deepseek-chat")).toBeInTheDocument();
  });

  it("adds a row, saves, and PUTs the new list to /api/profile-presets", async () => {
    const seenPuts: Array<Record<string, unknown>> = [];
    server.use(
      http.put("/api/profile-presets", async ({ request }) => {
        const body = (await request.json()) as {
          presets: ProfilePreset[];
        };
        seenPuts.push(body);
        return HttpResponse.json(body.presets);
      }),
    );

    const onChanged = vi.fn();
    renderWithProviders(
      <ManagePresetsDialog
        presets={seededFour}
        onClose={() => {}}
        onChanged={onChanged}
      />,
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /\+ add template/i }));

    // The newly-appended row is empty; fill its three text inputs.
    // We can find them by their label spans → last row's inputs.
    const nameInputs = screen.getAllByRole("textbox").filter((el) => {
      const span = el.previousSibling;
      return span instanceof HTMLElement && /name/i.test(span.textContent ?? "");
    });
    // The last "Name" textbox is the new row.
    await user.type(nameInputs[nameInputs.length - 1], "Internal");

    await user.click(screen.getByRole("button", { name: /save templates/i }));

    await waitFor(() => {
      expect(seenPuts).toHaveLength(1);
    });
    const sentRows = (seenPuts[0] as { presets: ProfilePreset[] }).presets;
    expect(sentRows).toHaveLength(5);
    expect(sentRows[4].name).toBe("Internal");

    // Parent gets the saved list.
    expect(onChanged).toHaveBeenCalledWith(sentRows);

    // Status line confirms.
    expect(screen.getByText(/templates saved/i)).toBeInTheDocument();
  });

  it("removes a row and saves the shortened list", async () => {
    const seenPuts: Array<Record<string, unknown>> = [];
    server.use(
      http.put("/api/profile-presets", async ({ request }) => {
        const body = (await request.json()) as {
          presets: ProfilePreset[];
        };
        seenPuts.push(body);
        return HttpResponse.json(body.presets);
      }),
    );

    renderWithProviders(
      <ManagePresetsDialog
        presets={seededFour}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    const user = userEvent.setup();

    // Remove the OpenAI row by its aria-label.
    await user.click(
      screen.getByRole("button", { name: /remove openai/i }),
    );
    expect(screen.queryByDisplayValue("OpenAI")).toBeNull();

    await user.click(screen.getByRole("button", { name: /save templates/i }));
    await waitFor(() => {
      expect(seenPuts).toHaveLength(1);
    });
    const sentRows = (seenPuts[0] as { presets: ProfilePreset[] }).presets;
    expect(sentRows).toHaveLength(3);
    expect(sentRows.map((r) => r.name)).not.toContain("OpenAI");
  });

  it("Reset to defaults: confirm → DELETE → onChanged with seeded list", async () => {
    let deleteCalled = false;
    server.use(
      http.delete("/api/profile-presets", () => {
        deleteCalled = true;
        return HttpResponse.json(seededFour);
      }),
    );

    const onChanged = vi.fn();
    renderWithProviders(
      <ManagePresetsDialog
        presets={[
          {
            name: "Only one",
            base_url: "https://x/v1",
            protocol: "openai-compat",
            model_id: "m",
          },
        ]}
        onClose={() => {}}
        onChanged={onChanged}
      />,
    );
    const user = userEvent.setup();

    await user.click(
      screen.getByRole("button", { name: /reset to defaults/i }),
    );

    // Confirm dialog renders.
    const confirmTitle = await screen.findByText(
      /restore the default templates\?/i,
    );
    expect(confirmTitle).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: /^restore defaults$/i }),
    );

    await waitFor(() => {
      expect(deleteCalled).toBe(true);
    });
    expect(onChanged).toHaveBeenCalledWith(seededFour);

    // The seeded list is now in the editor (Anthropic row visible).
    expect(screen.getByDisplayValue("Anthropic")).toBeInTheDocument();
  });

  it("save failure keeps the dialog open and renders a plain-language error", async () => {
    server.use(
      http.put("/api/profile-presets", () =>
        HttpResponse.json({ detail: "internal error" }, { status: 500 }),
      ),
    );

    const onClose = vi.fn();
    renderWithProviders(
      <ManagePresetsDialog
        presets={seededFour}
        onClose={onClose}
        onChanged={() => {}}
      />,
    );
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: /save templates/i }));

    expect(
      await screen.findByText(/couldn't save the templates/i),
    ).toBeInTheDocument();
    // onClose was not called — the user can correct and retry.
    expect(onClose).not.toHaveBeenCalled();
  });
});
