/**
 * MissingKeyInlineAlert — Playground-side hint when the resolved
 * provider has no key configured.
 *
 * Renders a non-blocking amber strip just above the Run button. We
 * deliberately do **not** disable the Run button: a user editing a
 * prompt body wants to iterate quickly, and an immediate "your run
 * will fail" warning is more honest than a disabled button + a hover
 * tooltip explaining why.
 *
 * Implementation note: this component reuses the `/api/settings`
 * query that the parent page already pulls (react-query dedupes on
 * the cache key, so we don't fire a second request). When the
 * provider is unset (the settings query hasn't landed yet, or the
 * field is null), the alert renders nothing — better silent than
 * yelling about a state we can't actually evaluate.
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

export function MissingKeyInlineAlert({
  provider,
}: {
  provider: string | undefined;
}) {
  const { t } = useTranslation();
  const settingsQ = useQuery({
    queryKey: ["settings"],
    queryFn: fetchSettings,
  });

  if (!provider) return null;
  if (settingsQ.isLoading || settingsQ.isError) return null;

  const keys = settingsQ.data?.keys ?? [];
  // Without the new `keys` field, we can't tell either way — silent.
  if (keys.length === 0) return null;

  const match = keys.find((k) => k.provider === provider);
  if (!match) return null;
  if (match.configured) return null;

  const pretty = provider === "anthropic" ? "Anthropic" : provider === "openai" ? "OpenAI" : provider;

  return (
    <div
      role="alert"
      className="flex items-center justify-between gap-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800"
    >
      <span>
        {t("settings.playgroundMissingKey", { provider: pretty })}
      </span>
      <Link
        to="/settings"
        className="rounded-md bg-white px-2 py-1 text-[11px] font-medium text-amber-800 ring-1 ring-amber-200 hover:bg-amber-100"
      >
        {t("settings.openSettings")}
      </Link>
    </div>
  );
}
