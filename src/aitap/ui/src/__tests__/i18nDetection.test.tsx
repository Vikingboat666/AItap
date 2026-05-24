/**
 * Initial-language detection — navigator Chinese → zh, otherwise en.
 *
 * We can't re-run the shared singleton's `init` (it's already booted by
 * setupTests and re-init would throw / race the legacy suite). Instead we
 * spin up a *fresh* i18next instance with the exact same detection config
 * and resources, then drive `navigator.language` / localStorage to assert
 * the resolution order:
 *
 *   1. localStorage cache wins over navigator (manual pick is sticky).
 *   2. With no cache, a Chinese navigator resolves to zh.
 *   3. With no cache, a non-Chinese navigator falls back to en.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createInstance } from "i18next";
import { initReactI18next } from "react-i18next";
import LanguageDetector from "i18next-browser-languagedetector";

import { resources, SUPPORTED_LANGUAGES } from "../i18n";

function buildInstance() {
  const instance = createInstance();
  instance.use(LanguageDetector).use(initReactI18next);
  return instance.init({
    resources,
    fallbackLng: "en",
    supportedLngs: [...SUPPORTED_LANGUAGES],
    nonExplicitSupportedLngs: true,
    load: "languageOnly",
    interpolation: { escapeValue: false },
    detection: {
      order: ["localStorage", "navigator"],
      caches: ["localStorage"],
    },
    react: { useSuspense: false },
  }).then(() => instance);
}

describe("initial language detection", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("resolves to zh when navigator language is Chinese and no cache exists", async () => {
    vi.spyOn(navigator, "language", "get").mockReturnValue("zh-CN");
    vi.spyOn(navigator, "languages", "get").mockReturnValue(["zh-CN"]);

    const instance = await buildInstance();
    expect(instance.resolvedLanguage).toBe("zh");
  });

  it("falls back to en when navigator language is not supported", async () => {
    vi.spyOn(navigator, "language", "get").mockReturnValue("fr-FR");
    vi.spyOn(navigator, "languages", "get").mockReturnValue(["fr-FR"]);

    const instance = await buildInstance();
    expect(instance.resolvedLanguage).toBe("en");
  });

  it("resolves to en when navigator language is English", async () => {
    vi.spyOn(navigator, "language", "get").mockReturnValue("en-US");
    vi.spyOn(navigator, "languages", "get").mockReturnValue(["en-US"]);

    const instance = await buildInstance();
    expect(instance.resolvedLanguage).toBe("en");
  });

  it("prefers the localStorage cache over a Chinese navigator", async () => {
    // Manual pick persisted as English — must win over the browser's zh.
    localStorage.setItem("i18nextLng", "en");
    vi.spyOn(navigator, "language", "get").mockReturnValue("zh-CN");
    vi.spyOn(navigator, "languages", "get").mockReturnValue(["zh-CN"]);

    const instance = await buildInstance();
    expect(instance.resolvedLanguage).toBe("en");
  });
});
