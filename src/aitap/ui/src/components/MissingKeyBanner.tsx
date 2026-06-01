/**
 * MissingKeyBanner — top-of-page hint that the user can't run anything yet.
 *
 * Surfaces on the Inventory page (and any other landing surface that
 * wants to nudge the user toward Settings). Reads ``GET /api/profiles``
 * and renders nothing once at least one profile has a key configured.
 *
 * Two states, two messages — both plain language, both name the next
 * action ("Add one in Settings…", "Open Settings to add one."):
 *
 *  - No profiles at all → ``missingProfilesBanner``
 *  - Profiles exist but every one has ``key_configured: false`` →
 *    ``missingProfileKeysBanner``
 *
 * Why a banner, not a hard block: a developer might be exploring the
 * scanned prompt inventory without intending to run anything yet —
 * blocking the whole page would be hostile. The banner reappears on
 * reload until the user actually adds a profile with a key.
 *
 * Note: this switched from ``/api/settings.keys`` (legacy
 * provider-keyed shape) to ``/api/profiles`` in the multi-provider
 * redesign. The legacy server route survives until ``wt/profile-
 * cleanup`` retires it, but no UI surface reads it any more.
 */
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { listProfiles } from "../api/profiles";

export function MissingKeyBanner() {
  const { t } = useTranslation();
  const profilesQ = useQuery({
    queryKey: ["profiles"],
    queryFn: listProfiles,
  });

  if (profilesQ.isLoading || profilesQ.isError) {
    // Stay silent on error — the Inventory page already renders its
    // own error state for the prompts query, and we don't want two
    // failure banners stacking when the dev server is down.
    return null;
  }

  const profiles = profilesQ.data ?? [];
  const anyKeyConfigured = profiles.some((p) => p.key_configured);
  if (anyKeyConfigured) return null;

  const message =
    profiles.length === 0
      ? t("settings.missingProfilesBanner")
      : t("settings.missingProfileKeysBanner");

  return (
    <div
      role="alert"
      className="flex items-center justify-between gap-3 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800"
    >
      <span>{message}</span>
      <Link
        to="/settings"
        className="rounded-md bg-white px-3 py-1 text-xs font-medium text-amber-800 ring-1 ring-amber-200 hover:bg-amber-100"
      >
        {t("settings.openSettings")}
      </Link>
    </div>
  );
}
