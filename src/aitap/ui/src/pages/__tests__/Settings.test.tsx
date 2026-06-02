/**
 * Settings page — multi-provider profile UI smoke tests.
 *
 * Covers the 3-section page (DefaultsCard / ProfilesList / AddProfileForm)
 * and its integration with the MissingKeyBanner on Inventory.
 *
 *   1. Empty state — no profiles yet: the DefaultsCard surfaces the
 *      "add a profile below" hint, the ProfilesList renders its empty
 *      copy, and the AddProfileForm is reachable.
 *   2. Add flow — typing label/url/key/model + clicking Add POSTs
 *      /api/profiles with the typed key and clears the input field.
 *      The masked preview from the next /api/profiles GET shows up.
 *   3. Test flow — clicking Test on a row POSTs the test endpoint and
 *      surfaces the API's plain-language ``detail`` in a role="status"
 *      strip.
 *   4. Delete flow — opens the confirm dialog, Yes hits DELETE, and
 *      the row disappears.
 *   5. Set-as-default — picking from the row menu PUTs /api/settings/
 *      defaults with the new model_profile_id.
 *   6. Manage presets — opening the dialog, adding a row, saving, and
 *      seeing the chip appear in the AddProfileForm.
 *   7. CANARY — plant a known raw key into the POST body; assert that
 *      after Add + Test + Delete the raw key never appears anywhere
 *      in the rendered DOM. (Backend canary lives in
 *      tests/integration/test_profiles_e2e.py.)
 *
 * Plus a MissingKeyBanner check on Inventory: when /api/profiles is
 * empty, the banner appears.
 *
 * All tests use MSW handlers and the renderWithProviders helper from
 * the test-utils — same pattern as PR #35.
 */
import { afterEach, describe, expect, it } from "vitest";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import { Settings } from "../Settings";
import { Inventory } from "../Inventory";
import {
  renderWithProviders,
  screen,
  waitFor,
} from "../../test-utils/render";
import { server } from "../../setupTests";
import {
  presetsFixture,
  profilesFixture,
  settingsFixture,
} from "../../test-utils/handlers";
import type { Profile } from "../../api/profiles";

// --- helpers ---------------------------------------------------------

/**
 * Build a SettingsResponse-shaped object with a ``defaults`` field.
 * The generated ``SettingsResponse`` type is still stale (cleanup
 * worktree regenerates it), so we widen with a permissive shape and
 * cast at the boundary.
 */
function settingsBodyWithDefaults(defaults?: {
  model_profile_id?: string | null;
  judge_profile_id?: string | null;
}): Record<string, unknown> {
  return {
    ...settingsFixture,
    defaults: {
      model_profile_id: defaults?.model_profile_id ?? null,
      judge_profile_id: defaults?.judge_profile_id ?? null,
    },
  };
}

/** Build a Profile row for the /api/profiles fixture. */
function makeProfile(over: Partial<Profile> & { id: string }): Profile {
  return {
    label: "Test profile",
    base_url: "https://api.example.com/v1",
    protocol: "openai-compat",
    model_id: "gpt-4o-mini",
    notes: "",
    key_configured: true,
    key_source: "keyring",
    key_masked: "sk-...zzzz",
    ...over,
  };
}

// --- tests -----------------------------------------------------------

describe("Settings page — multi-provider profile UI", () => {
  afterEach(() => {
    server.resetHandlers();
  });

  it("renders the empty state when no profiles exist", async () => {
    server.use(
      http.get("/api/profiles", () => HttpResponse.json([])),
      http.get("/api/settings", () =>
        HttpResponse.json(settingsBodyWithDefaults()),
      ),
    );

    renderWithProviders(<Settings />);

    // Page header copy from the new title.
    expect(
      await screen.findByText(
        /pick your default model, manage profiles/i,
      ),
    ).toBeInTheDocument();

    // DefaultsCard renders its empty-profile hint.
    expect(
      screen.getByText(/no profiles configured yet/i),
    ).toBeInTheDocument();

    // ProfilesList shows its empty copy.
    expect(
      screen.getByText(/no profiles yet\. add one below/i),
    ).toBeInTheDocument();

    // AddProfileForm reachable — its submit button is rendered.
    expect(screen.getByRole("button", { name: /add profile/i })).toBeInTheDocument();
  });

  it("add flow POSTs the typed key, clears the input, and shows the masked preview", async () => {
    let postBody: { label?: string; api_key?: string; base_url?: string } | null =
      null;
    let profilesAfterAdd: Profile[] = [];

    server.use(
      http.get("/api/profiles", () => HttpResponse.json(profilesAfterAdd)),
      http.get("/api/settings", () =>
        HttpResponse.json(settingsBodyWithDefaults()),
      ),
      http.post("/api/profiles", async ({ request }) => {
        postBody = (await request.json()) as typeof postBody;
        const created = makeProfile({
          id: "prof_new",
          label: postBody?.label ?? "—",
          base_url: postBody?.base_url ?? "—",
          model_id: "gpt-4o-mini",
          key_masked: "sk-...rrrr",
        });
        profilesAfterAdd = [created];
        return HttpResponse.json(created);
      }),
    );

    renderWithProviders(<Settings />);

    // Wait for the form to render.
    const addButton = await screen.findByRole("button", { name: /add profile/i });

    const labelInput = screen.getByPlaceholderText(/anthropic prod/i);
    await userEvent.type(labelInput, "My profile");
    const baseInput = screen.getByPlaceholderText(/api\.anthropic\.com/i);
    await userEvent.type(baseInput, "https://api.openai.com/v1");
    const modelInput = screen.getByPlaceholderText(/claude-sonnet/i);
    await userEvent.type(modelInput, "gpt-4o-mini");
    const keyInput = screen.getByPlaceholderText(/paste your key/i);
    await userEvent.type(keyInput, "sk-typed-key-VVVVVVVVVVVV");

    await userEvent.click(addButton);

    await waitFor(() => {
      expect(postBody?.label).toBe("My profile");
      expect(postBody?.api_key).toBe("sk-typed-key-VVVVVVVVVVVV");
    });

    // The typed key input is cleared after the POST.
    await waitFor(() => {
      expect((keyInput as HTMLInputElement).value).toBe("");
    });

    // The masked preview from the refetched /api/profiles shows up.
    expect(await screen.findByText(/sk-\.\.\.rrrr/)).toBeInTheDocument();

    // The raw typed key never re-renders into the visible DOM.
    expect(document.body.textContent ?? "").not.toContain(
      "sk-typed-key-VVVVVVVVVVVV",
    );
  });

  it("Test button surfaces the API's plain-language detail", async () => {
    server.use(
      http.get("/api/profiles", () =>
        HttpResponse.json([
          makeProfile({ id: "prof_test", label: "Probe me" }),
        ]),
      ),
      http.get("/api/settings", () =>
        HttpResponse.json(settingsBodyWithDefaults()),
      ),
      http.post("/api/profiles/prof_test/test", () =>
        HttpResponse.json({
          ok: true,
          reason: null,
          detail: "The key works.",
        }),
      ),
    );

    renderWithProviders(<Settings />);

    // Pick the row's Test button (the DefaultsCard also has a Save).
    const testButton = await screen.findByRole("button", { name: /^test$/i });
    await userEvent.click(testButton);

    expect(
      await screen.findByText(/the key works\./i),
    ).toBeInTheDocument();
  });

  it("delete flow opens confirm, Yes hits DELETE, and the row disappears", async () => {
    let profilesNow: Profile[] = [
      makeProfile({ id: "prof_del", label: "Trash me" }),
    ];
    let deleteCalled = false;
    server.use(
      http.get("/api/profiles", () => HttpResponse.json(profilesNow)),
      http.get("/api/settings", () =>
        HttpResponse.json(settingsBodyWithDefaults()),
      ),
      http.delete("/api/profiles/prof_del", () => {
        deleteCalled = true;
        profilesNow = [];
        return HttpResponse.json(profilesNow[0] ?? null);
      }),
    );

    renderWithProviders(<Settings />);

    expect(await screen.findByText("Trash me")).toBeInTheDocument();

    // Open the per-row menu (aria-label includes the label).
    await userEvent.click(
      screen.getByRole("button", { name: /more actions for trash me/i }),
    );
    await userEvent.click(screen.getByRole("menuitem", { name: /^delete$/i }));

    // Confirm dialog appears; clicking Yes triggers DELETE.
    const yesButton = await screen.findByRole("button", {
      name: /delete profile/i,
    });
    await userEvent.click(yesButton);

    await waitFor(() => expect(deleteCalled).toBe(true));
    await waitFor(() => {
      expect(screen.queryByText("Trash me")).not.toBeInTheDocument();
    });
  });

  it("Set as default model PUTs /api/settings/defaults with the chosen id", async () => {
    let putBody: {
      model_profile_id?: string | null;
      judge_profile_id?: string | null;
    } | null = null;

    server.use(
      http.get("/api/profiles", () =>
        HttpResponse.json([
          makeProfile({ id: "prof_default_me", label: "Set me default" }),
        ]),
      ),
      http.get("/api/settings", () =>
        HttpResponse.json(settingsBodyWithDefaults()),
      ),
      http.put("/api/settings/defaults", async ({ request }) => {
        putBody = (await request.json()) as typeof putBody;
        return HttpResponse.json(
          settingsBodyWithDefaults({
            model_profile_id: putBody?.model_profile_id ?? null,
          }),
        );
      }),
    );

    renderWithProviders(<Settings />);

    await userEvent.click(
      await screen.findByRole("button", {
        name: /more actions for set me default/i,
      }),
    );
    await userEvent.click(
      screen.getByRole("menuitem", { name: /set as default model/i }),
    );

    await waitFor(() => {
      expect(putBody?.model_profile_id).toBe("prof_default_me");
    });
  });

  it("Manage presets dialog adds a row, saves, and the chip appears", async () => {
    let savedPresets: Array<{ name: string }> = [];
    server.use(
      http.get("/api/profiles", () => HttpResponse.json(profilesFixture)),
      http.get("/api/settings", () =>
        HttpResponse.json(settingsBodyWithDefaults()),
      ),
      http.get("/api/profile-presets", () =>
        HttpResponse.json(savedPresets.length === 0 ? presetsFixture : savedPresets),
      ),
      http.put("/api/profile-presets", async ({ request }) => {
        const body = (await request.json()) as {
          presets: Array<{ name: string; base_url: string; protocol: string; model_id: string }>;
        };
        savedPresets = body.presets;
        return HttpResponse.json(body.presets);
      }),
    );

    renderWithProviders(<Settings />);

    // Open the editor.
    const manageLink = await screen.findByRole("button", {
      name: /manage templates/i,
    });
    await userEvent.click(manageLink);

    // Add a new row.
    await userEvent.click(
      await screen.findByRole("button", { name: /\+ add template/i }),
    );

    // Fill the new row's name field — the first empty Name input
    // belongs to the new row (the default seed is empty in handlers).
    const nameFields = screen.getAllByLabelText(/^name$/i);
    const newRow = nameFields[nameFields.length - 1];
    await userEvent.type(newRow, "MyChip");

    // Save the templates.
    await userEvent.click(
      screen.getByRole("button", { name: /save templates/i }),
    );

    await waitFor(() => {
      expect(savedPresets.some((p) => p.name === "MyChip")).toBe(true);
    });

    // Close the dialog so the underlying chip row re-renders. The
    // dialog Close button at the top.
    await userEvent.click(
      screen.getByRole("button", { name: /^close$/i }),
    );

    // The chip should now appear in the AddProfileForm row.
    expect(
      await screen.findByRole("button", { name: /^mychip$/i }),
    ).toBeInTheDocument();
  });

  it("CANARY: the typed raw API key never reaches the DOM through Add → Test → Delete", async () => {
    const CANARY = "sk-fake-profile-canary-XXXXXXXXXX";

    let profilesNow: Profile[] = [];
    let postSeen: { api_key?: string } | null = null;
    let testCalled = false;
    let deleteCalled = false;

    server.use(
      http.get("/api/profiles", () => HttpResponse.json(profilesNow)),
      http.get("/api/settings", () =>
        HttpResponse.json(settingsBodyWithDefaults()),
      ),
      http.post("/api/profiles", async ({ request }) => {
        postSeen = (await request.json()) as typeof postSeen;
        const created = makeProfile({
          id: "prof_canary",
          label: "Canary",
          // The masked preview is what the UI gets back — the raw
          // CANARY must not appear on any response surface.
          key_masked: "sk-...yyyy",
        });
        profilesNow = [created];
        return HttpResponse.json(created);
      }),
      http.post("/api/profiles/prof_canary/test", () => {
        testCalled = true;
        return HttpResponse.json({
          ok: true,
          reason: null,
          detail: "The key works.",
        });
      }),
      http.delete("/api/profiles/prof_canary", () => {
        deleteCalled = true;
        const removed = profilesNow[0] ?? null;
        profilesNow = [];
        return HttpResponse.json(removed);
      }),
    );

    renderWithProviders(<Settings />);

    // 1. Fill + Add.
    const addButton = await screen.findByRole("button", { name: /add profile/i });
    await userEvent.type(
      screen.getByPlaceholderText(/anthropic prod/i),
      "Canary",
    );
    await userEvent.type(
      screen.getByPlaceholderText(/api\.anthropic\.com/i),
      "https://api.example.com/v1",
    );
    await userEvent.type(
      screen.getByPlaceholderText(/claude-sonnet/i),
      "gpt-4o-mini",
    );
    await userEvent.type(
      screen.getByPlaceholderText(/paste your key/i),
      CANARY,
    );
    await userEvent.click(addButton);

    await waitFor(() => {
      expect(postSeen?.api_key).toBe(CANARY);
    });
    // The row appears.
    expect(await screen.findByText("Canary")).toBeInTheDocument();

    // 2. Test the new profile.
    await userEvent.click(screen.getByRole("button", { name: /^test$/i }));
    await waitFor(() => expect(testCalled).toBe(true));

    // 3. Delete it.
    await userEvent.click(
      screen.getByRole("button", { name: /more actions for canary/i }),
    );
    await userEvent.click(screen.getByRole("menuitem", { name: /^delete$/i }));
    await userEvent.click(
      await screen.findByRole("button", { name: /delete profile/i }),
    );
    await waitFor(() => expect(deleteCalled).toBe(true));

    // CANARY assertion — the raw key never made it into any rendered
    // surface. The masked preview ("sk-...yyyy") is fine; the raw
    // ``sk-fake-profile-canary-XXXXXXXXXX`` string must be absent.
    expect(document.body.textContent ?? "").not.toContain(CANARY);
    // The <input value> is also part of the DOM; double-check.
    const inputs = document.querySelectorAll("input");
    inputs.forEach((input) => {
      expect((input as HTMLInputElement).value).not.toContain(CANARY);
    });
  });
});

describe("MissingKeyBanner on Inventory", () => {
  afterEach(() => {
    server.resetHandlers();
  });

  it("shows the missing-profiles banner when /api/profiles is empty", async () => {
    server.use(http.get("/api/profiles", () => HttpResponse.json([])));

    renderWithProviders(<Inventory />);

    expect(
      await screen.findByText(/no model profiles yet\. add one in settings/i),
    ).toBeInTheDocument();
  });

  it("shows the missing-keys banner when no profile has a key", async () => {
    server.use(
      http.get("/api/profiles", () =>
        HttpResponse.json([
          makeProfile({
            id: "prof_nokey",
            label: "No key here",
            key_configured: false,
            key_source: "none",
            key_masked: null,
          }),
        ]),
      ),
    );

    renderWithProviders(<Inventory />);

    expect(
      await screen.findByText(
        /none of your profiles have an api key\. open settings to add one/i,
      ),
    ).toBeInTheDocument();
  });

  it("stays silent when at least one profile has a key", async () => {
    // Default handlers already serve one configured profile.
    renderWithProviders(<Inventory />);

    // Wait for Inventory's own data to load, then assert no banner.
    await screen.findByText(/alpha_prompt/i);
    expect(
      screen.queryByText(/no model profiles yet/i),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/none of your profiles have an api key/i),
    ).not.toBeInTheDocument();
  });
});
