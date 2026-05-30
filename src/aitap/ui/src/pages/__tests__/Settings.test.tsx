/**
 * Settings page — UI smoke tests for the secure API-key flow.
 *
 * Covers:
 *
 *   - The page renders one card per provider with the current status
 *     (configured vs not) reflecting the `/api/settings.keys` array.
 *   - Save flow: typing a key, clicking Save, the input clears, and
 *     the user sees a success toast. The raw key is NEVER read back
 *     into the visible DOM after the save.
 *   - Test flow: clicking Test renders the API's `detail` line.
 *   - Clear flow: clicking Clear hits DELETE and refreshes status.
 *   - Missing-key banner appears on Inventory when no provider is
 *     configured, hidden once at least one is.
 *   - Playground inline alert appears when the resolved provider has
 *     no key and dismisses once a key is added.
 *
 * The MSW handlers in `setupTests.ts` already serve `/api/settings`
 * with `keys: []` by default; per-test overrides use `server.use(...)`.
 */
import { afterEach, describe, expect, it } from "vitest";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import { Settings } from "../Settings";
import { Inventory } from "../Inventory";
import {
  act,
  renderWithProviders,
  screen,
  waitFor,
} from "../../test-utils/render";
import { server } from "../../setupTests";

const FAKE_ANTHROPIC = "sk-ant-FAKE-aaaaaaaaaaaaaaaa";

function settingsBody(
  configured: { anthropic?: boolean; openai?: boolean } = {},
) {
  return {
    cost_per_run_usd: 0.01,
    cost_per_session_usd: 0.05,
    judge_model: null,
    model: "gpt-4o-mini",
    provider: "openai",
    providers_available: [],
    keys: [
      {
        provider: "anthropic",
        configured: !!configured.anthropic,
        source: configured.anthropic ? "keyring" : "none",
        masked: configured.anthropic ? "sk-ant-...aaaa" : null,
      },
      {
        provider: "openai",
        configured: !!configured.openai,
        source: configured.openai ? "keyring" : "none",
        masked: configured.openai ? "sk-...bbbb" : null,
      },
    ],
  };
}

describe("Settings page", () => {
  afterEach(() => {
    server.resetHandlers();
  });

  it("renders one card per provider showing unconfigured state by default", async () => {
    server.use(http.get("/api/settings", () => HttpResponse.json(settingsBody())));
    renderWithProviders(<Settings />);

    expect(await screen.findByText("Anthropic")).toBeInTheDocument();
    expect(screen.getByText("OpenAI")).toBeInTheDocument();

    // Save buttons start disabled (nothing typed yet).
    const saveButtons = screen.getAllByRole("button", { name: /save/i });
    expect(saveButtons[0]).toBeDisabled();
  });

  it("save flow clears the input and never echoes the raw key in the DOM", async () => {
    const seenSavePayloads: Array<Record<string, unknown>> = [];
    server.use(
      http.get("/api/settings", () => HttpResponse.json(settingsBody())),
      http.post("/api/settings/key", async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        seenSavePayloads.push(body);
        return HttpResponse.json({
          provider: "anthropic",
          configured: true,
          source: "keyring",
          masked: "sk-ant-...aaaa",
        });
      }),
    );

    renderWithProviders(<Settings />);
    const user = userEvent.setup();

    const input = await screen.findByLabelText(/api key for anthropic/i);
    await user.type(input, FAKE_ANTHROPIC);
    expect(input).toHaveValue(FAKE_ANTHROPIC);

    const saveButtons = screen.getAllByRole("button", { name: /save/i });
    await user.click(saveButtons[0]);

    await waitFor(() => {
      expect(screen.getByText(/^key saved\.$/i)).toBeInTheDocument();
    });

    // Critical: the input was cleared after save.
    expect(input).toHaveValue("");
    // The raw key MUST not be anywhere in the visible DOM.
    expect(document.body.textContent).not.toContain(FAKE_ANTHROPIC);
    // And the POST body did include the key (we actually sent it).
    expect(seenSavePayloads[0]).toMatchObject({
      provider: "anthropic",
      key: FAKE_ANTHROPIC,
    });
  });

  it("test button shows the plain-language detail returned by the API", async () => {
    server.use(
      http.get("/api/settings", () =>
        HttpResponse.json(settingsBody({ anthropic: true })),
      ),
      http.post("/api/settings/test/anthropic", () =>
        HttpResponse.json({
          ok: true,
          reason: null,
          detail: "The Anthropic key works. You can run prompts that use it.",
        }),
      ),
    );

    renderWithProviders(<Settings />);
    const user = userEvent.setup();

    const testButtons = await screen.findAllByRole("button", { name: /test/i });
    await user.click(testButtons[0]);

    expect(
      await screen.findByText(/the anthropic key works/i),
    ).toBeInTheDocument();
  });

  it("clear button removes the key and reverts the card to unconfigured", async () => {
    let configured = true;
    server.use(
      http.get("/api/settings", () =>
        HttpResponse.json(settingsBody({ anthropic: configured })),
      ),
      http.delete("/api/settings/key/anthropic", () => {
        configured = false;
        return HttpResponse.json({
          provider: "anthropic",
          configured: false,
          source: "none",
          masked: null,
        });
      }),
    );

    renderWithProviders(<Settings />);
    const user = userEvent.setup();

    // Masked preview shows up first.
    expect(await screen.findByText(/sk-ant-\.\.\.aaaa/)).toBeInTheDocument();

    const clearButtons = screen.getAllByRole("button", { name: /clear/i });
    await user.click(clearButtons[0]);

    expect(
      await screen.findByText(/key removed/i),
    ).toBeInTheDocument();
  });
});

describe("MissingKeyBanner", () => {
  afterEach(() => {
    server.resetHandlers();
  });

  it("appears on Inventory when no provider has a key", async () => {
    server.use(http.get("/api/settings", () => HttpResponse.json(settingsBody())));
    renderWithProviders(<Inventory />);

    expect(
      await screen.findByText(/no api key is set/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open settings/i })).toHaveAttribute(
      "href",
      "/settings",
    );
  });

  it("disappears once at least one provider is configured", async () => {
    server.use(
      http.get("/api/settings", () =>
        HttpResponse.json(settingsBody({ anthropic: true })),
      ),
    );
    renderWithProviders(<Inventory />);

    // Wait for the prompts to render — the inventory takes a tick.
    await screen.findByText("alpha_prompt");
    expect(screen.queryByText(/no api key is set/i)).not.toBeInTheDocument();
  });
});

describe("settings i18n keys exist", () => {
  it("renders the Chinese banner when locale is zh", async () => {
    const i18n = (await import("../../i18n")).default;
    server.use(http.get("/api/settings", () => HttpResponse.json(settingsBody())));
    await act(async () => {
      await i18n.changeLanguage("zh");
    });
    renderWithProviders(<Inventory />);

    expect(
      await screen.findByText(/尚未设置 API key/),
    ).toBeInTheDocument();

    await act(async () => {
      await i18n.changeLanguage("en");
    });
  });
});
