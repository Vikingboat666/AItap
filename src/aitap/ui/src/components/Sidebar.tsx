import { NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { clsx } from "../lib/clsx";

const items = [
  { to: "/", labelKey: "nav.inventory", end: true },
  { to: "/playground", labelKey: "nav.playground" },
  { to: "/history", labelKey: "nav.history" },
  { to: "/audit", labelKey: "nav.audit" },
  { to: "/settings", labelKey: "nav.settings" },
];

export function Sidebar() {
  const { t } = useTranslation();
  return (
    <aside className="flex w-56 flex-col border-r border-ink-200 bg-white">
      <div className="px-5 py-5">
        <div className="text-lg font-semibold tracking-tight text-ink-900">
          {t("sidebar.brand")}
        </div>
        <div className="text-xs text-ink-500">{t("sidebar.tagline")}</div>
      </div>
      <nav className="flex-1 px-2">
        <ul className="space-y-1">
          {items.map((item) => (
            <li key={item.to}>
              <NavLink
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  clsx(
                    "block rounded-md px-3 py-2 text-sm transition-colors",
                    isActive
                      ? "bg-brand-50 font-medium text-brand-700"
                      : "text-ink-600 hover:bg-ink-50 hover:text-ink-900",
                  )
                }
              >
                {t(item.labelKey)}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
      <div className="px-5 py-4 text-[11px] text-ink-400">
        {t("sidebar.footer")}
      </div>
    </aside>
  );
}
