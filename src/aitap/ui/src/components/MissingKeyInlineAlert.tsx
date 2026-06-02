/**
 * MissingKeyInlineAlert — Playground-side hint when the default model
 * profile has no API key configured.
 *
 * Renders a non-blocking amber strip just above the Run button. We
 * deliberately do **not** disable the Run button: a user editing a
 * prompt body wants to iterate quickly, and an immediate "your run
 * will fail" warning is more honest than a disabled button + a hover
 * tooltip explaining why.
 *
 * Implementation note: this component reads ``/api/profiles`` directly
 * (the parent's react-query cache dedupes on the same key, so we don't
 * fire a second request). It surfaces the alert whenever the default
 * model profile is unset, missing, or has no key — three states the
 * user should know about before clicking Run. Until a follow-up worktree
 * migrates ``POST /api/runs`` off the legacy ``provider`` enum, the
 * ``provider`` prop is kept on the surface so the existing call sites
 * compile unchanged; the prop is unused inside the component.
 */
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { apiClient } from "../api/client";
import type { Profile } from "../api/generated/models/Profile";
import type { SettingsResponse } from "../api/generated/models/SettingsResponse";

function fetchSettings(): Promise<SettingsResponse> {
  return apiClient.settings.getSettingsEndpointApiSettingsGet();
}

function fetchProfiles(): Promise<Array<Profile>> {
  return apiClient.profiles.listProfilesApiProfilesGet();
}

export function MissingKeyInlineAlert({
  // Kept on the prop surface so existing Playground call sites compile
  // unchanged; ignored by the new logic which reads defaults + profiles.
  // Will be removed once the runs path migrates off the provider enum.
  provider: _provider,
}: {
  provider: string | undefined;
}) {
  const { t } = useTranslation();
  const settingsQ = useQuery({
    queryKey: ["settings"],
    queryFn: fetchSettings,
  });
  const profilesQ = useQuery({
    queryKey: ["profiles"],
    queryFn: fetchProfiles,
  });

  if (
    settingsQ.isLoading ||
    settingsQ.isError ||
    profilesQ.isLoading ||
    profilesQ.isError
  ) {
    return null;
  }

  const defaultProfileId = settingsQ.data?.defaults?.model_profile_id ?? null;
  const profiles = profilesQ.data ?? [];

  // No default model profile picked yet → nudge the user.
  if (!defaultProfileId) {
    return (
      <AlertStrip>{t("settings.playgroundMissingProfileDefault")}</AlertStrip>
    );
  }

  const match = profiles.find((p) => p.id === defaultProfileId);
  // Default points at a profile that doesn't exist (race / config edit).
  if (!match) {
    return (
      <AlertStrip>{t("settings.playgroundMissingProfileDefault")}</AlertStrip>
    );
  }
  if (match.key_configured) return null;

  // Default profile is set but has no key.
  return (
    <AlertStrip>
      {t("settings.playgroundMissingProfileKey", { label: match.label })}
    </AlertStrip>
  );
}

function AlertStrip({ children }: { children: React.ReactNode }) {
  const { t } = useTranslation();
  return (
    <div
      role="alert"
      className="flex items-center justify-between gap-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800"
    >
      <span>{children}</span>
      <Link
        to="/settings"
        className="rounded-md bg-white px-2 py-1 text-[11px] font-medium text-amber-800 ring-1 ring-amber-200 hover:bg-amber-100"
      >
        {t("settings.openSettings")}
      </Link>
    </div>
  );
}
