/**
 * ManagePresetsDialog — the editor for the chip-row templates.
 *
 * Opened from the Add Profile form via the "Manage templates" link.
 * Lets the user add / edit / delete preset rows and Save the whole
 * list back to ``PUT /api/profile-presets``. The Reset button calls
 * ``DELETE /api/profile-presets`` after a confirmation dialog — same
 * a11y pattern as PR #35's keyring-fallback confirm.
 *
 * Local state model:
 *
 * - ``draft`` holds the in-flight edits the user is making — this is
 *   what the table renders. We seed it from the parent's ``presets``
 *   prop on open and never mutate the parent's list directly so
 *   "Cancel" really cancels.
 * - ``feedback`` is the inline ``role="status"`` line under the table.
 * - ``confirmReset`` is the boolean that drives the reset confirm
 *   sub-dialog.
 *
 * Plain-language copy + i18n discipline (CLAUDE.md):
 *
 * - Every label / button / message goes through ``t()``.
 * - Failure copy names the next action ("Try again"), never a status
 *   code or SDK string.
 * - Confirm-dialog wording explains the consequence in everyday terms.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type ProfilePreset,
  replacePresets,
  resetPresets,
} from "../../api/profiles";
import { clsx } from "../../lib/clsx";

interface ManagePresetsDialogProps {
  /** Current preset list as the parent has it. We snapshot into local state. */
  presets: ProfilePreset[];
  /** Close handler — called on Save / Cancel / Close. */
  onClose: () => void;
  /** Fired after Save / Reset so the parent can refetch its own copy. */
  onChanged: (updated: ProfilePreset[]) => void;
}

export function ManagePresetsDialog({
  presets,
  onClose,
  onChanged,
}: ManagePresetsDialogProps) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<ProfilePreset[]>(presets);
  const [busy, setBusy] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);
  const [feedback, setFeedback] = useState<{
    tone: "ok" | "err";
    text: string;
  } | null>(null);

  // Re-seed local draft if the parent hot-swaps the prop. Happens after
  // a Reset round-trip — the parent calls ``onChanged`` with the new
  // server state and we want the editor to render it immediately.
  useEffect(() => {
    setDraft(presets);
  }, [presets]);

  function updateRow(index: number, patch: Partial<ProfilePreset>): void {
    setDraft((rows) =>
      rows.map((row, i) => (i === index ? { ...row, ...patch } : row)),
    );
  }

  function removeRow(index: number): void {
    setDraft((rows) => rows.filter((_, i) => i !== index));
  }

  function addRow(): void {
    setDraft((rows) => [
      ...rows,
      {
        name: "",
        base_url: "",
        protocol: "openai-compat",
        model_id: "",
      },
    ]);
  }

  async function handleSave(): Promise<void> {
    setBusy(true);
    setFeedback(null);
    try {
      const saved = await replacePresets(draft);
      setFeedback({ tone: "ok", text: t("settings.presetsManageSaved") });
      onChanged(saved);
    } catch {
      setFeedback({ tone: "err", text: t("settings.presetsManageFailure") });
    } finally {
      setBusy(false);
    }
  }

  async function handleReset(): Promise<void> {
    setBusy(true);
    setFeedback(null);
    try {
      const seeded = await resetPresets();
      setDraft(seeded);
      onChanged(seeded);
      setConfirmReset(false);
      setFeedback({
        tone: "ok",
        text: t("settings.presetsManageResetSuccess"),
      });
    } catch {
      setFeedback({ tone: "err", text: t("settings.presetsManageFailure") });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="manage-presets-title"
      className="fixed inset-0 z-30 flex items-start justify-center overflow-y-auto bg-black/30 px-4 py-8"
    >
      <div className="w-full max-w-2xl rounded-lg border border-ink-200 bg-white shadow-xl">
        <div className="flex items-start justify-between border-b border-ink-100 px-4 py-3">
          <div>
            <div
              id="manage-presets-title"
              className="text-sm font-semibold text-ink-800"
            >
              {t("settings.presetsManageTitle")}
            </div>
            <div className="mt-0.5 text-xs text-ink-500">
              {t("settings.presetsManageSubtitle")}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="rounded-md border border-ink-200 px-2 py-1 text-xs text-ink-700 hover:bg-ink-50 disabled:opacity-60"
          >
            {t("settings.presetsManageClose")}
          </button>
        </div>

        <div className="space-y-3 px-4 py-3">
          {draft.length === 0 && (
            <div className="rounded-md border border-dashed border-ink-200 bg-ink-50 px-3 py-4 text-center text-xs text-ink-600">
              {t("settings.presetsManageEmpty")}
            </div>
          )}

          {draft.map((row, index) => (
            <div
              key={index}
              className="grid grid-cols-1 gap-2 rounded-md border border-ink-100 bg-white p-2 md:grid-cols-12"
            >
              <label className="md:col-span-3">
                <span className="block text-[10px] font-medium uppercase text-ink-500">
                  {t("settings.presetsManageNameField")}
                </span>
                <input
                  type="text"
                  value={row.name}
                  onChange={(e) => updateRow(index, { name: e.target.value })}
                  className="mt-0.5 w-full rounded-md border border-ink-200 px-2 py-1 text-xs focus:border-brand-500 focus:outline-none"
                />
              </label>
              <label className="md:col-span-4">
                <span className="block text-[10px] font-medium uppercase text-ink-500">
                  {t("settings.presetsManageBaseUrlField")}
                </span>
                <input
                  type="text"
                  value={row.base_url}
                  onChange={(e) =>
                    updateRow(index, { base_url: e.target.value })
                  }
                  spellCheck={false}
                  autoComplete="off"
                  className="mt-0.5 w-full rounded-md border border-ink-200 px-2 py-1 font-mono text-[11px] focus:border-brand-500 focus:outline-none"
                />
              </label>
              <label className="md:col-span-2">
                <span className="block text-[10px] font-medium uppercase text-ink-500">
                  {t("settings.presetsManageProtocolField")}
                </span>
                <select
                  value={row.protocol}
                  onChange={(e) =>
                    updateRow(index, {
                      protocol: e.target.value as ProfilePreset["protocol"],
                    })
                  }
                  className="mt-0.5 w-full rounded-md border border-ink-200 px-2 py-1 text-xs focus:border-brand-500 focus:outline-none"
                >
                  <option value="openai-compat">
                    {t("settings.profilesCreateProtocolOpenAI")}
                  </option>
                  <option value="anthropic">
                    {t("settings.profilesCreateProtocolAnthropic")}
                  </option>
                </select>
              </label>
              <label className="md:col-span-2">
                <span className="block text-[10px] font-medium uppercase text-ink-500">
                  {t("settings.presetsManageModelField")}
                </span>
                <input
                  type="text"
                  value={row.model_id}
                  onChange={(e) =>
                    updateRow(index, { model_id: e.target.value })
                  }
                  spellCheck={false}
                  autoComplete="off"
                  className="mt-0.5 w-full rounded-md border border-ink-200 px-2 py-1 font-mono text-[11px] focus:border-brand-500 focus:outline-none"
                />
              </label>
              <div className="flex items-end md:col-span-1">
                <button
                  type="button"
                  onClick={() => removeRow(index)}
                  aria-label={t("settings.presetsManageRemoveAria", {
                    name: row.name || "—",
                  })}
                  disabled={busy}
                  className="w-full rounded-md border border-rose-200 px-2 py-1 text-[11px] text-rose-700 hover:bg-rose-50 disabled:opacity-60"
                >
                  {t("settings.presetsManageRemoveRow")}
                </button>
              </div>
            </div>
          ))}

          <button
            type="button"
            onClick={addRow}
            disabled={busy}
            className="w-full rounded-md border border-dashed border-ink-300 px-3 py-2 text-xs text-ink-700 hover:bg-ink-50 disabled:opacity-60"
          >
            {t("settings.presetsManageAddRow")}
          </button>

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
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-ink-100 px-4 py-3">
          <button
            type="button"
            onClick={() => setConfirmReset(true)}
            disabled={busy}
            className="rounded-md border border-ink-200 px-3 py-1.5 text-xs text-ink-700 hover:bg-ink-50 disabled:opacity-60"
          >
            {t("settings.presetsManageReset")}
          </button>
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={busy}
            className={clsx(
              "rounded-md px-3 py-1.5 text-xs font-medium text-white",
              busy
                ? "cursor-not-allowed bg-ink-300"
                : "bg-brand-600 hover:bg-brand-700",
            )}
          >
            {busy
              ? t("settings.presetsManageSaving")
              : t("settings.presetsManageSave")}
          </button>
        </div>
      </div>

      {confirmReset && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="reset-presets-title"
          className="absolute inset-0 z-40 flex items-center justify-center bg-black/40 px-4"
        >
          <div className="w-full max-w-md rounded-lg border border-amber-300 bg-amber-50 px-4 py-4 text-xs text-amber-900 shadow-xl">
            <div
              id="reset-presets-title"
              className="mb-1 text-sm font-semibold"
            >
              {t("settings.presetsManageResetConfirmTitle")}
            </div>
            <div className="mb-3">
              {t("settings.presetsManageResetConfirmBody")}
            </div>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setConfirmReset(false)}
                disabled={busy}
                className="rounded-md border border-amber-400 bg-white px-3 py-1 text-[11px] font-medium text-amber-900 disabled:opacity-60"
              >
                {t("settings.presetsManageResetConfirmNo")}
              </button>
              <button
                type="button"
                onClick={() => void handleReset()}
                disabled={busy}
                className="rounded-md bg-amber-700 px-3 py-1 text-[11px] font-medium text-white disabled:opacity-60"
              >
                {t("settings.presetsManageResetConfirmYes")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
