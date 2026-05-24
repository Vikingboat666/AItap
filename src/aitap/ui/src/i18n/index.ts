/**
 * i18n bootstrap for the aitap web UI.
 *
 * Design choices that the rest of the app (and the test setup) depend on:
 *
 *   - Resources are bundled inline (imported, not lazily fetched) so the
 *     very first synchronous render already has every string. Tests can
 *     assert on translated text immediately after render with no `await`.
 *   - `react: { useSuspense: false }` keeps `useTranslation()` from
 *     throwing a promise during the first paint — there's nothing async
 *     to suspend on anyway, and Suspense would complicate the test
 *     harness for zero benefit.
 *   - Language detection order is `localStorage` → `navigator`: a manual
 *     pick (persisted to localStorage by the detector's `caches`) always
 *     wins over the browser default. `fallbackLng: "en"` guarantees the
 *     English strings render when a key is missing in another locale.
 *
 * This module is imported for its side effect (it `init`s the singleton)
 * from both `main.tsx` (production) and `setupTests.ts` (tests).
 */
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import LanguageDetector from "i18next-browser-languagedetector";

import en from "./en.json";
import zh from "./zh.json";

export const SUPPORTED_LANGUAGES = ["en", "zh"] as const;
export type SupportedLanguage = (typeof SUPPORTED_LANGUAGES)[number];

export const resources = {
  en: { translation: en },
  zh: { translation: zh },
} as const;

// Guard against double-init when both `main.tsx` and `setupTests.ts`
// pull this module in the same process — i18next throws if `init` runs
// twice on the same instance.
if (!i18n.isInitialized) {
  void i18n
    .use(LanguageDetector)
    .use(initReactI18next)
    .init({
      resources,
      fallbackLng: "en",
      supportedLngs: [...SUPPORTED_LANGUAGES],
      // Treat "zh-CN", "zh-Hans", etc. as "zh" rather than falling back
      // to English when the browser reports a regional Chinese tag.
      nonExplicitSupportedLngs: true,
      load: "languageOnly",
      interpolation: {
        // React already escapes rendered values, so i18next's own HTML
        // escaping would double-encode interpolated strings.
        escapeValue: false,
      },
      detection: {
        order: ["localStorage", "navigator"],
        caches: ["localStorage"],
      },
      react: {
        useSuspense: false,
      },
    });
}

export default i18n;
