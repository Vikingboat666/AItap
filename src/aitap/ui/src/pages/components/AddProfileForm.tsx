/**
 * AddProfileForm — the bottom card on the Settings page.
 *
 * Shape:
 *
 * 1. A chip row of preset templates ("Start from a template") with a
 *    "Manage templates" link that opens the ManagePresetsDialog editor.
 * 2. Label / Base URL / API key / Default model fields.
 * 3. Protocol radio (openai-compat | anthropic).
 * 4. Add profile button + role="status" feedback.
 *
 * Clicking a chip pre-fills base_url + protocol + model_id; the user
 * still types a label + key (chip rows don't carry secrets).
 *
 * Security discipline (PR #35):
 *
 * - API key input is ``type="password"`` + ``autoComplete="new-password"``.
 * - After a successful POST, the typed key is immediately cleared from
 *   React state — only the masked preview from the next ``/api/profiles``
 *   GET stays in the DOM.
 * - The mutation response is never logged to ``console.*``.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { Card, CardHeader } from "../../components/primitives";
import { clsx } from "../../lib/clsx";
import {
  type Profile,
  type ProfilePreset,
  type ProfileUpsertRequest,
  createProfile,
  listPresets,
} from "../../api/profiles";
import { ManagePresetsDialog } from "./ManagePresetsDialog";

export interface AddProfileFormProps {
  onAdded: (created: Profile) => void;
}

export function AddProfileForm({ onAdded }: AddProfileFormProps) {
  const { t } = useTranslation();
  const [label, setLabel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [modelId, setModelId] = useState("");
  const [protocol, setProtocol] = useState<Profile["protocol"]>("openai-compat");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{
    tone: "ok" | "err";
    text: string;
  } | null>(null);
  const [presetEditorOpen, setPresetEditorOpen] = useState(false);

  // Presets feed the chip row. We don't cache them in react-query's
  // settings query because the editor mutates them under us — a fresh
  // ``listPresets`` call after each ManagePresets save gives the chip
  // row a consistent view without crosstalk against ``/api/profiles``.
  const presetsQ = useQuery({
    queryKey: ["profile-presets"],
    queryFn: listPresets,
  });

  function pickPreset(preset: ProfilePreset): void {
    setBaseUrl(preset.base_url);
    setProtocol(preset.protocol);
    setModelId(preset.model_id);
  }

  async function handleAdd(): Promise<void> {
    // Validate locally first so the user gets a plain-language hint
    // rather than the FastAPI 422 envelope.
    if (!label.trim()) {
      setFeedback({ tone: "err", text: t("settings.profilesCreateLabelRequired") });
      return;
    }
    if (!baseUrl.trim()) {
      setFeedback({ tone: "err", text: t("settings.profilesCreateBaseUrlRequired") });
      return;
    }
    if (!modelId.trim()) {
      setFeedback({ tone: "err", text: t("settings.profilesCreateModelRequired") });
      return;
    }

    setBusy(true);
    setFeedback(null);
    const payload: ProfileUpsertRequest = {
      label: label.trim(),
      base_url: baseUrl.trim(),
      protocol,
      model_id: modelId.trim(),
    };
    if (apiKey.trim()) {
      payload.api_key = apiKey;
    }
    try {
      const created = await createProfile(payload);
      // Reset the rest of the form so the user can add another.
      setLabel("");
      setBaseUrl("");
      setModelId("");
      setProtocol("openai-compat");
      setFeedback({ tone: "ok", text: t("settings.profilesCreateSuccess") });
      onAdded(created);
    } catch {
      setFeedback({ tone: "err", text: t("settings.profilesCreateFailure") });
    } finally {
      // SECURITY: clear the typed key from React state regardless of
      // success/failure (Reviewer N-UI-3). On failure the user can
      // retype; we never want a rejected key lingering in component
      // state where a later re-render could leak it.
      setApiKey("");
      setBusy(false);
    }
  }

  const presets = presetsQ.data ?? [];

  return (
    <Card>
      <CardHeader
        title={t("settings.profilesCreateTitle")}
        subtitle={t("settings.profilesCreateSubtitle")}
      />
      <div className="space-y-3 px-4 py-4">
        {/* Chip row */}
        <div className="flex flex-wrap items-center gap-2 text-[11px] text-ink-500">
          <span>{t("settings.profilesCreatePresetsLabel")}</span>
          {presets.length === 0 && (
            <span>{t("settings.profilesCreateNoPresets")}</span>
          )}
          {presets.map((preset) => (
            <button
              key={preset.name}
              type="button"
              onClick={() => pickPreset(preset)}
              className="rounded-full border border-ink-200 px-2 py-0.5 text-[11px] text-ink-700 hover:bg-ink-50"
            >
              {preset.name}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setPresetEditorOpen(true)}
            className="ml-1 text-[11px] text-brand-600 underline hover:text-brand-700"
          >
            {t("settings.profilesCreateManagePresets")}
          </button>
        </div>

        {/* Form fields */}
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-ink-700">
            {t("settings.profilesCreateLabelField")}
          </span>
          <input
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder={t("settings.profilesCreateLabelPlaceholder")}
            className="w-full rounded-md border border-ink-200 px-2 py-1 text-xs focus:border-brand-500 focus:outline-none"
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-ink-700">
            {t("settings.profilesCreateBaseUrlField")}
          </span>
          <input
            type="text"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder={t("settings.profilesCreateBaseUrlPlaceholder")}
            spellCheck={false}
            autoComplete="off"
            className="w-full rounded-md border border-ink-200 px-2 py-1 font-mono text-[11px] focus:border-brand-500 focus:outline-none"
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-ink-700">
            {t("settings.profilesCreateKeyField")}
          </span>
          <input
            type="password"
            autoComplete="new-password"
            spellCheck={false}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={t("settings.profilesCreateKeyPlaceholder")}
            className="w-full rounded-md border border-ink-200 px-2 py-1 font-mono text-[11px] focus:border-brand-500 focus:outline-none"
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-ink-700">
            {t("settings.profilesCreateModelField")}
          </span>
          <input
            type="text"
            value={modelId}
            onChange={(e) => setModelId(e.target.value)}
            placeholder={t("settings.profilesCreateModelPlaceholder")}
            spellCheck={false}
            autoComplete="off"
            className="w-full rounded-md border border-ink-200 px-2 py-1 font-mono text-[11px] focus:border-brand-500 focus:outline-none"
          />
        </label>
        <fieldset>
          <legend className="mb-1 text-xs font-medium text-ink-700">
            {t("settings.profilesCreateProtocolField")}
          </legend>
          <div className="flex gap-3 text-xs">
            <label className="flex items-center gap-1">
              <input
                type="radio"
                name="new-protocol"
                checked={protocol === "openai-compat"}
                onChange={() => setProtocol("openai-compat")}
              />
              {t("settings.profilesCreateProtocolOpenAI")}
            </label>
            <label className="flex items-center gap-1">
              <input
                type="radio"
                name="new-protocol"
                checked={protocol === "anthropic"}
                onChange={() => setProtocol("anthropic")}
              />
              {t("settings.profilesCreateProtocolAnthropic")}
            </label>
          </div>
        </fieldset>

        {feedback && (
          <div
            role="status"
            className={clsx(
              "rounded-md px-2 py-1 text-[11px]",
              feedback.tone === "ok"
                ? "bg-emerald-50 text-emerald-700"
                : "bg-rose-50 text-rose-700",
            )}
          >
            {feedback.text}
          </div>
        )}

        <div className="flex justify-end">
          <button
            type="button"
            onClick={() => void handleAdd()}
            disabled={busy}
            className={clsx(
              "rounded-md px-3 py-1.5 text-xs font-medium text-white",
              busy
                ? "cursor-not-allowed bg-ink-300"
                : "bg-brand-600 hover:bg-brand-700",
            )}
          >
            {busy
              ? t("settings.profilesCreateSubmitting")
              : t("settings.profilesCreateSubmit")}
          </button>
        </div>
      </div>

      {presetEditorOpen && (
        <ManagePresetsDialog
          presets={presets}
          onClose={() => setPresetEditorOpen(false)}
          onChanged={() => {
            // Refetch the presets so the chip row reflects edits.
            void presetsQ.refetch();
          }}
        />
      )}
    </Card>
  );
}
