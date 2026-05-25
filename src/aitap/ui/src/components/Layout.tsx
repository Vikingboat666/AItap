import { useEffect } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Header } from "./Header";
import { Sidebar } from "./Sidebar";

export function Layout({ children }: { children: ReactNode }) {
  const { i18n } = useTranslation();

  // Keep `<html lang>` in step with the active UI language so assistive
  // tech and the browser's own heuristics (hyphenation, spellcheck) treat
  // the page content as the right locale. Re-runs on every languageChanged
  // because `i18n.language` is part of the dependency list.
  useEffect(() => {
    document.documentElement.lang = i18n.language?.split("-")[0] ?? "en";
  }, [i18n.language]);

  return (
    <div className="flex h-full min-h-screen bg-ink-50">
      <Sidebar />
      <div className="flex flex-1 flex-col">
        <Header />
        <main className="flex-1 overflow-auto px-6 py-6">{children}</main>
      </div>
    </div>
  );
}
