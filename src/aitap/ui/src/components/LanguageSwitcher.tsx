/**
 * Header language switcher — a two-button EN / 中文 toggle.
 *
 * Reads the active language off the i18next singleton (via
 * `useTranslation`) and flips it with `changeLanguage`. The detector's
 * `localStorage` cache (configured in `i18n/index.ts`) persists the pick
 * automatically, so there's no explicit write here — selecting a button
 * is enough to survive a reload.
 *
 * Accessibility: each button carries an `aria-label` (the switch action,
 * not the bare glyph) and `aria-pressed` reflecting whether it is the
 * current language, so screen readers announce the active state.
 */
import { useTranslation } from "react-i18next";

import { SUPPORTED_LANGUAGES, type SupportedLanguage } from "../i18n";
import { clsx } from "../lib/clsx";

const OPTIONS: ReadonlyArray<{
  lng: SupportedLanguage;
  labelKey: string;
  ariaKey: string;
}> = [
  { lng: "en", labelKey: "languageSwitcher.en", ariaKey: "languageSwitcher.switchToEnglish" },
  { lng: "zh", labelKey: "languageSwitcher.zh", ariaKey: "languageSwitcher.switchToChinese" },
];

export function LanguageSwitcher() {
  const { t, i18n } = useTranslation();

  // `i18n.language` can carry a region subtag (e.g. "zh-CN"); normalise to
  // the base language so the active-state comparison matches our enum.
  const active = (i18n.language?.split("-")[0] ?? "en") as SupportedLanguage;
  const current: SupportedLanguage = SUPPORTED_LANGUAGES.includes(active)
    ? active
    : "en";

  return (
    <div
      role="group"
      aria-label={t("languageSwitcher.label")}
      className="flex items-center overflow-hidden rounded-full border border-ink-200"
    >
      {OPTIONS.map(({ lng, labelKey, ariaKey }) => {
        const isActive = current === lng;
        return (
          <button
            key={lng}
            type="button"
            aria-label={t(ariaKey)}
            aria-pressed={isActive}
            onClick={() => {
              if (!isActive) void i18n.changeLanguage(lng);
            }}
            className={clsx(
              "px-2 py-0.5 text-[11px] font-medium transition-colors",
              isActive
                ? "bg-brand-600 text-white"
                : "bg-white text-ink-500 hover:bg-ink-100",
            )}
          >
            {t(labelKey)}
          </button>
        );
      })}
    </div>
  );
}
