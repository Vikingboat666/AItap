/**
 * MissingKeyBanner — top-of-page hint that no provider has a key yet.
 *
 * Surfaces on the Inventory page (and any other landing surface that
 * wants to nudge the user toward Settings). Reads `/api/settings.keys`
 * and renders nothing when at least one provider is configured.
 *
 * Why a banner, not a hard block: a developer might be exploring the
 * scanned prompt inventory without intending to run anything yet —
 * blocking the whole page would be hostile. The banner is dismissable
 * locally per session (collapsed via React state) but reappears on
 * reload until the user actually adds a key.
 *
 * Copy follows the plain-language rule: it states the cause + the
 * next action ("…until you add one in Settings."), no jargon, no
 * status code, no stack trace.
 */
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

import type { ProviderKeyStatus } from "../api/settings-keys";

type SettingsWithKeys = { keys?: ProviderKeyStatus[] };

async function fetchSettings(): Promise<SettingsWithKeys> {
  const res = await fetch("/api/settings", {
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as SettingsWithKeys;
}

export function MissingKeyBanner() {
  const { t } = useTranslation();
  const settingsQ = useQuery({
    queryKey: ["settings"],
    queryFn: fetchSettings,
  });

  if (settingsQ.isLoading || settingsQ.isError) {
    // Stay silent on error — the Inventory page already renders its
    // own error state for the prompts query, and we don't want two
    // failure banners stacking when the dev server is down.
    return null;
  }

  const keys = settingsQ.data?.keys ?? [];
  // If we don't have a `keys` array yet (old client / old server), we
  // can't reliably tell whether a key is set — render nothing so we
  // don't false-alarm a user mid-migration.
  if (keys.length === 0) return null;

  const anyConfigured = keys.some((k) => k.configured);
  if (anyConfigured) return null;

  return (
    <div
      role="alert"
      className="flex items-center justify-between gap-3 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800"
    >
      <span>{t("settings.missingKeyBanner")}</span>
      <Link
        to="/settings"
        className="rounded-md bg-white px-3 py-1 text-xs font-medium text-amber-800 ring-1 ring-amber-200 hover:bg-amber-100"
      >
        {t("settings.openSettings")}
      </Link>
    </div>
  );
}
