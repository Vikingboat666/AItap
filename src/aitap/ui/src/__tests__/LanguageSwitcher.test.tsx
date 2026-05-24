/**
 * LanguageSwitcher — render, toggle, persistence + a Sidebar smoke test.
 *
 * These tests deliberately drive the locale away from "en" (the default
 * the suite is pinned to in setupTests). The shared `afterEach` restores
 * "en" and clears the persisted language, so they never leak a Chinese
 * locale into the English-asserting legacy suite. We also restore inside
 * each test for clarity.
 */
import { afterEach, describe, expect, it } from "vitest";
import userEvent from "@testing-library/user-event";

import { LanguageSwitcher } from "../components/LanguageSwitcher";
import { Sidebar } from "../components/Sidebar";
import { act, renderWithProviders, screen } from "../test-utils/render";
import i18n from "../i18n";

describe("LanguageSwitcher", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
    if (typeof localStorage !== "undefined") {
      localStorage.removeItem("i18nextLng");
    }
  });

  it("renders EN + 中文 buttons with the active one pressed", async () => {
    await i18n.changeLanguage("en");
    renderWithProviders(<LanguageSwitcher />);

    const en = screen.getByRole("button", { name: /switch to english/i });
    const zh = screen.getByRole("button", { name: /切换到中文/ });

    expect(en).toHaveAttribute("aria-pressed", "true");
    expect(zh).toHaveAttribute("aria-pressed", "false");
  });

  it("switches the active language to zh on click", async () => {
    const user = userEvent.setup();
    await i18n.changeLanguage("en");
    renderWithProviders(<LanguageSwitcher />);

    await user.click(screen.getByRole("button", { name: /切换到中文/ }));

    expect(i18n.language).toBe("zh");
    expect(
      screen.getByRole("button", { name: /切换到中文/ }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(
      screen.getByRole("button", { name: /switch to english/i }),
    ).toHaveAttribute("aria-pressed", "false");
  });

  it("persists the picked language to localStorage", async () => {
    const user = userEvent.setup();
    await i18n.changeLanguage("en");
    renderWithProviders(<LanguageSwitcher />);

    await user.click(screen.getByRole("button", { name: /切换到中文/ }));

    // The detector's localStorage cache writes under "i18nextLng".
    expect(localStorage.getItem("i18nextLng")).toBe("zh");
  });

  it("renders Sidebar nav in English by default and Chinese after switch", async () => {
    await i18n.changeLanguage("en");
    const { rerender } = renderWithProviders(<Sidebar />);

    expect(screen.getByText("Inventory")).toBeInTheDocument();
    expect(screen.getByText("Playground")).toBeInTheDocument();

    // changeLanguage fires a state update inside react-i18next subscribers;
    // wrap it in act so the re-render is flushed before we assert.
    await act(async () => {
      await i18n.changeLanguage("zh");
    });
    rerender(<Sidebar />);

    expect(screen.getByText("清单")).toBeInTheDocument();
    expect(screen.getByText("实验台")).toBeInTheDocument();
    expect(screen.queryByText("Inventory")).not.toBeInTheDocument();
  });
});
