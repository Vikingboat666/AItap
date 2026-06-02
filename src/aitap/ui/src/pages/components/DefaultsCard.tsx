/**
 * DefaultsCard — the two-select picker at the top of the Settings page.
 *
 * Sources both dropdowns from the live ``/api/profiles`` list. The
 * "Default model" select is required when ≥1 profile exists; the
 * "Judge model" select is optional — blank means "reuse the default".
 *
 * Save calls ``PUT /api/settings/defaults`` with the two profile ids.
 * The backend validates the references (422 + plain-language detail
 * when an id doesn't exist); we surface that detail verbatim under the
 * card so the user knows what went wrong.
 *
 * UX details:
 *
 * - The selects render ``{label} · {model_id}`` so a user with two
 *   "DeepSeek" profiles can tell which one is the cheap one.
 * - When ``profiles`` is empty, the card explains the next action
 *   (add a profile below) and disables Save — the backend would 422
 *   on a non-null id pointing at nothing.
 * - Feedback is a ``role="status"`` line — same a11y pattern as the
 *   Profiles list test result.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Card, CardHeader } from "../../components/primitives";
import { clsx } from "../../lib/clsx";
import { putDefaults, type Profile } from "../../api/profiles";

export interface CurrentDefaults {
  model_profile_id: string | null;
  judge_profile_id: string | null;
}

export interface DefaultsCardProps {
  profiles: Profile[];
  current: CurrentDefaults;
  onSaved: () => void;
}

export function DefaultsCard({ profiles, current, onSaved }: DefaultsCardProps) {
  const { t } = useTranslation();
  const [modelId, setModelId] = useState<string>(current.model_profile_id ?? "");
  const [judgeId, setJudgeId] = useState<string>(current.judge_profile_id ?? "");
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{
    tone: "ok" | "err";
    text: string;
  } | null>(null);

  // Re-sync local state when the parent refetches defaults after a save.
  const currentKey = `${current.model_profile_id ?? ""}|${current.judge_profile_id ?? ""}`;
  useEffect(() => {
    setModelId(current.model_profile_id ?? "");
    setJudgeId(current.judge_profile_id ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentKey]);

  const noProfiles = profiles.length === 0;

  async function handleSave(): Promise<void> {
    setBusy(true);
    setFeedback(null);
    try {
      await putDefaults({
        model_profile_id: modelId === "" ? null : modelId,
        judge_profile_id: judgeId === "" ? null : judgeId,
      });
      setFeedback({ tone: "ok", text: t("settings.defaultsCardSaved") });
      onSaved();
    } catch (err) {
      // The server's plain-language ``detail`` is what we render — it
      // already names the next action ("Open Settings and pick a
      // default model…"). Only fall back to a generic message when
      // the error wasn't a structured HttpError.
      const message =
        err instanceof Error && err.message
          ? err.message
          : t("settings.defaultsCardFailure");
      setFeedback({ tone: "err", text: message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title={t("settings.defaultsCardTitle")}
        subtitle={t("settings.defaultsCardSubtitle")}
      />
      <div className="space-y-4 px-4 py-4">
        {noProfiles && (
          <div className="rounded-md border border-dashed border-ink-200 bg-ink-50 px-3 py-2 text-xs text-ink-600">
            {t("settings.defaultsCardNoProfilesHint")}
          </div>
        )}

        {/* Default model select */}
        <div>
          <label
            htmlFor="defaults-model-select"
            className="mb-1 block text-xs font-medium text-ink-700"
          >
            {t("settings.defaultsCardModelLabel")}
          </label>
          <select
            id="defaults-model-select"
            value={modelId}
            onChange={(e) => setModelId(e.target.value)}
            disabled={noProfiles}
            className="w-full rounded-md border border-ink-200 px-2 py-1 text-xs focus:border-brand-500 focus:outline-none disabled:bg-ink-50 disabled:text-ink-400"
          >
            <option value="">{t("settings.defaultsCardNoneOption")}</option>
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label} · {p.model_id}
              </option>
            ))}
          </select>
          <div className="mt-1 text-[11px] text-ink-500">
            {t("settings.defaultsCardModelHint")}
          </div>
        </div>

        {/* Judge model select */}
        <div>
          <label
            htmlFor="defaults-judge-select"
            className="mb-1 block text-xs font-medium text-ink-700"
          >
            {t("settings.defaultsCardJudgeLabel")}
          </label>
          <select
            id="defaults-judge-select"
            value={judgeId}
            onChange={(e) => setJudgeId(e.target.value)}
            disabled={noProfiles}
            className="w-full rounded-md border border-ink-200 px-2 py-1 text-xs focus:border-brand-500 focus:outline-none disabled:bg-ink-50 disabled:text-ink-400"
          >
            <option value="">{t("settings.defaultsCardReuseDefault")}</option>
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label} · {p.model_id}
              </option>
            ))}
          </select>
          <div className="mt-1 text-[11px] text-ink-500">
            {t("settings.defaultsCardJudgeHint")}
          </div>
        </div>

        {/* Save */}
        <div className="flex items-center justify-end gap-3">
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
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={busy || noProfiles}
            className={clsx(
              "rounded-md px-3 py-1.5 text-xs font-medium text-white",
              busy || noProfiles
                ? "cursor-not-allowed bg-ink-300"
                : "bg-brand-600 hover:bg-brand-700",
            )}
          >
            {busy
              ? t("settings.defaultsCardSaving")
              : t("settings.defaultsCardSave")}
          </button>
        </div>
      </div>
    </Card>
  );
}
