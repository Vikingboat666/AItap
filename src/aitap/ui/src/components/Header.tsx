import { useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { LanguageSwitcher } from "./LanguageSwitcher";

/**
 * Map each route to the i18n key for its header title. Static routes use
 * an exact-path lookup; the dynamic detail/section routes are matched by
 * prefix below.
 */
const TITLE_KEYS: Record<string, string> = {
  "/": "header.inventory",
  "/playground": "header.playground",
  "/history": "header.history",
  "/audit": "header.audit",
};

function deriveTitleKey(pathname: string): string {
  if (TITLE_KEYS[pathname]) return TITLE_KEYS[pathname];
  if (pathname.startsWith("/prompts/")) return "header.promptDetail";
  if (pathname.startsWith("/pipelines/")) return "header.pipelineDetail";
  if (pathname.startsWith("/playground")) return "header.playground";
  if (pathname.startsWith("/history")) return "header.history";
  return "header.fallback";
}

export function Header() {
  const { pathname } = useLocation();
  const { t } = useTranslation();
  return (
    <header className="flex h-14 items-center justify-between border-b border-ink-200 bg-white px-6">
      <h1 className="text-base font-medium text-ink-800">
        {t(deriveTitleKey(pathname))}
      </h1>
      <div className="flex items-center gap-3 text-xs text-ink-500">
        <LanguageSwitcher />
        <span className="rounded-full bg-ink-100 px-2 py-0.5 font-mono text-[11px]">
          {t("header.mock")}
        </span>
        <span>{t("header.user")}</span>
      </div>
    </header>
  );
}
