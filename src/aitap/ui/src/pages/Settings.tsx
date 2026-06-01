/**
 * Settings page — 3-section profile management.
 *
 * The page renders three stacked cards, top-to-bottom:
 *
 *   1. ``<DefaultsCard>`` — Default model + Judge model dropdowns,
 *      sourced from ``GET /api/profiles`` and persisted via
 *      ``PUT /api/settings/defaults``.
 *   2. ``<ProfilesList>``  — One row per configured profile with
 *      Test / Edit / Delete / Set-as-default / Set-as-judge actions.
 *   3. ``<AddProfileForm>`` — Preset chip-row + free-form fields to
 *      POST a new profile (and key) in one shot.
 *
 * Data flow:
 *
 *   - ``listProfiles`` (queryKey ``["profiles"]``) feeds all three
 *     cards. Mutations (create / update / delete / test) live in the
 *     child components and call back into ``profilesQ.refetch()`` so
 *     the parent re-renders with the new list.
 *   - ``fetchSettings`` (queryKey ``["settings"]``) is only here for
 *     the ``defaults`` field — the codegen hasn't been re-run with the
 *     new server shape yet (the cleanup worktree regenerates it), so
 *     we type-assert the field off the response. The generated
 *     ``SettingsResponse`` already shadows everything else.
 *
 * Security + plain-language discipline carries through to the child
 * components — see their docstrings.
 *
 * Legacy ``/api/settings/key*`` endpoints are NOT touched from this
 * file any more — they survive on the server until the cleanup
 * worktree retires them. The ``../api/settings-keys.ts`` client also
 * lingers (no import here); cleanup removes it.
 */
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { apiClient } from "../api/client";
import { Card, CardHeader } from "../components/primitives";
import { ErrorState } from "../components/feedback";
import { ListSkeleton } from "../components/skeletons";
import type { SettingsResponse } from "../api/generated/models/SettingsResponse";
import { listProfiles, putDefaults } from "../api/profiles";
import { AddProfileForm } from "./components/AddProfileForm";
import {
  type CurrentDefaults,
  DefaultsCard,
} from "./components/DefaultsCard";
import { ProfilesList } from "./components/ProfilesList";

/**
 * Shape of the ``defaults`` field as the server now returns it.
 * The generated ``SettingsResponse`` is stale (no ``defaults``); the
 * cleanup worktree regenerates it. Until then we read it through a
 * narrow type-assert.
 */
interface SettingsResponseWithDefaults extends SettingsResponse {
  defaults?: CurrentDefaults;
}

function fetchSettings(): Promise<SettingsResponseWithDefaults> {
  return apiClient.settings.getSettingsEndpointApiSettingsGet() as Promise<SettingsResponseWithDefaults>;
}

export function Settings() {
  const { t } = useTranslation();
  const profilesQ = useQuery({
    queryKey: ["profiles"],
    queryFn: listProfiles,
  });
  const settingsQ = useQuery({
    queryKey: ["settings"],
    queryFn: fetchSettings,
  });

  if (profilesQ.isLoading || settingsQ.isLoading) {
    return <ListSkeleton label={t("settings.loading")} rows={3} />;
  }

  if (profilesQ.isError) {
    return (
      <ErrorState
        title={t("settings.profilesCouldntLoad")}
        error={profilesQ.error}
        onRetry={() => void profilesQ.refetch()}
      />
    );
  }
  if (settingsQ.isError) {
    return (
      <ErrorState
        title={t("settings.couldntLoad")}
        error={settingsQ.error}
        onRetry={() => void settingsQ.refetch()}
      />
    );
  }

  const profiles = profilesQ.data ?? [];
  const currentDefaults: CurrentDefaults = settingsQ.data?.defaults ?? {
    model_profile_id: null,
    judge_profile_id: null,
  };

  async function setDefaultModel(profileId: string): Promise<void> {
    await putDefaults({
      model_profile_id: profileId,
      judge_profile_id: currentDefaults.judge_profile_id,
    });
    await Promise.all([profilesQ.refetch(), settingsQ.refetch()]);
  }

  async function setDefaultJudge(profileId: string): Promise<void> {
    await putDefaults({
      model_profile_id: currentDefaults.model_profile_id,
      judge_profile_id: profileId,
    });
    await Promise.all([profilesQ.refetch(), settingsQ.refetch()]);
  }

  function refetchAll(): void {
    void profilesQ.refetch();
    void settingsQ.refetch();
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title={t("settings.title")}
          subtitle={t("settings.subtitle")}
        />
      </Card>

      <DefaultsCard
        profiles={profiles}
        current={currentDefaults}
        onSaved={refetchAll}
      />

      <ProfilesList
        profiles={profiles}
        onChanged={refetchAll}
        onSetDefaultModel={(id) => void setDefaultModel(id)}
        onSetDefaultJudge={(id) => void setDefaultJudge(id)}
      />

      <AddProfileForm onAdded={refetchAll} />
    </div>
  );
}
