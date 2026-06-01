/**
 * ProfilesList — the middle section of the Settings page.
 *
 * One ``<ProfileRow>`` per profile. Each row shows:
 *
 * - The profile's label + protocol + model_id.
 * - The key status: masked preview + source (system keychain / file).
 * - "Test" — calls ``POST /api/profiles/{id}/test`` and renders the
 *   plain-language detail inline (``role="status"``).
 * - "..." menu — Edit / Delete / Set as default / Set as judge.
 *
 * Edit opens an inline form (re-using AddProfileForm in edit mode would
 * be tempting, but keeping the row's edit form local avoids a heavy
 * lift-state path; the form is small enough). Delete shows a confirm
 * dialog (PR #35's pattern).
 *
 * Empty state: a friendly "No profiles yet" sentence + a pointer at
 * the Add form below.
 */
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Card, CardHeader } from "../../components/primitives";
import { clsx } from "../../lib/clsx";
import {
  type Profile,
  type ProfileTestResponse,
  type ProfileUpsertRequest,
  deleteProfile,
  testProfile,
  updateProfile,
} from "../../api/profiles";

export interface ProfilesListProps {
  profiles: Profile[];
  onChanged: () => void;
  onSetDefaultModel: (profileId: string) => void;
  onSetDefaultJudge: (profileId: string) => void;
}

export function ProfilesList({
  profiles,
  onChanged,
  onSetDefaultModel,
  onSetDefaultJudge,
}: ProfilesListProps) {
  const { t } = useTranslation();

  if (profiles.length === 0) {
    return (
      <Card>
        <CardHeader title={t("settings.profilesTitle")} subtitle={t("settings.profilesSubtitle")} />
        <div className="px-4 py-6 text-center text-xs text-ink-500">
          {t("settings.profilesEmpty")}
        </div>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader title={t("settings.profilesTitle")} subtitle={t("settings.profilesSubtitle")} />
      <ul className="divide-y divide-ink-100">
        {profiles.map((profile) => (
          <li key={profile.id}>
            <ProfileRow
              profile={profile}
              onChanged={onChanged}
              onSetDefaultModel={() => onSetDefaultModel(profile.id)}
              onSetDefaultJudge={() => onSetDefaultJudge(profile.id)}
            />
          </li>
        ))}
      </ul>
    </Card>
  );
}

interface ProfileRowProps {
  profile: Profile;
  onChanged: () => void;
  onSetDefaultModel: () => void;
  onSetDefaultJudge: () => void;
}

function ProfileRow({
  profile,
  onChanged,
  onSetDefaultModel,
  onSetDefaultJudge,
}: ProfileRowProps) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busyAction, setBusyAction] = useState<"test" | "delete" | null>(null);
  const [testResult, setTestResult] = useState<ProfileTestResponse | null>(null);

  // Esc closes the delete-confirm dialog. Initial focus lands on the
  // Cancel button — the less destructive choice gets keyboard default,
  // same a11y rule as PR #35's keyring confirm (Reviewer N-UI-2).
  const deleteCancelRef = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    if (!confirmDelete) return;
    deleteCancelRef.current?.focus();
    function onKey(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        setConfirmDelete(false);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [confirmDelete]);

  async function handleTest(): Promise<void> {
    setBusyAction("test");
    setTestResult(null);
    try {
      const result = await testProfile(profile.id);
      setTestResult(result);
    } catch {
      setTestResult({
        ok: false,
        reason: "other",
        detail: t("settings.profilesEditFailure"),
      });
    } finally {
      setBusyAction(null);
    }
  }

  async function handleDelete(): Promise<void> {
    setBusyAction("delete");
    try {
      await deleteProfile(profile.id);
      setConfirmDelete(false);
      onChanged();
    } catch {
      // The confirm dialog stays open; the user can retry. The error
      // surfaces via the inline test result line (re-used as a status
      // strip — the consequence is harmless).
      setTestResult({
        ok: false,
        reason: "other",
        detail: t("settings.profilesEditFailure"),
      });
      setConfirmDelete(false);
    } finally {
      setBusyAction(null);
    }
  }

  const keyStatus = profile.key_configured
    ? t("settings.profilesMaskedKey", { masked: profile.key_masked ?? "" })
    : t("settings.profilesKeyMissing");

  const sourceLabel = profile.key_configured
    ? profile.key_source === "keyring"
      ? t("settings.profilesKeyFromKeyring")
      : profile.key_source === "fallback"
        ? t("settings.profilesKeyFromFallback")
        : ""
    : "";

  return (
    <div className="space-y-2 px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <button
            type="button"
            onClick={() => setEditing((s) => !s)}
            className="text-left text-sm font-medium text-ink-800 hover:text-brand-700"
          >
            {profile.label}
          </button>
          <div className="mt-0.5 truncate font-mono text-[11px] text-ink-500">
            {profile.protocol} · {profile.model_id}
          </div>
          <div className="mt-0.5 text-[11px] text-ink-500">
            {keyStatus}
            {sourceLabel && <span className="ml-1 text-ink-400">({sourceLabel})</span>}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void handleTest()}
            disabled={busyAction !== null}
            className={clsx(
              "rounded-md px-2 py-1 text-[11px]",
              busyAction === "test"
                ? "bg-ink-100 text-ink-400"
                : "bg-white text-ink-700 ring-1 ring-ink-200 hover:bg-ink-50",
            )}
          >
            {busyAction === "test"
              ? t("settings.profilesTesting")
              : t("settings.profilesTestButton")}
          </button>
          <div className="relative">
            <button
              type="button"
              onClick={() => setMenuOpen((s) => !s)}
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              aria-label={t("settings.profilesMenuLabel", { label: profile.label })}
              className="rounded-md px-2 py-1 text-xs text-ink-700 ring-1 ring-ink-200 hover:bg-ink-50"
            >
              ⋯
            </button>
            {menuOpen && (
              <div
                role="menu"
                className="absolute right-0 z-10 mt-1 w-44 rounded-md border border-ink-200 bg-white py-1 text-xs shadow-md"
              >
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false);
                    setEditing(true);
                  }}
                  className="block w-full px-3 py-1.5 text-left text-ink-700 hover:bg-ink-50"
                >
                  {t("settings.profilesMenuEdit")}
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false);
                    onSetDefaultModel();
                  }}
                  className="block w-full px-3 py-1.5 text-left text-ink-700 hover:bg-ink-50"
                >
                  {t("settings.profilesMenuSetDefault")}
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false);
                    onSetDefaultJudge();
                  }}
                  className="block w-full px-3 py-1.5 text-left text-ink-700 hover:bg-ink-50"
                >
                  {t("settings.profilesMenuSetJudge")}
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false);
                    setConfirmDelete(true);
                  }}
                  className="block w-full px-3 py-1.5 text-left text-rose-700 hover:bg-rose-50"
                >
                  {t("settings.profilesMenuDelete")}
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      {testResult && (
        <div
          role="status"
          className={clsx(
            "rounded-md px-3 py-2 text-xs",
            testResult.ok ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-700",
          )}
        >
          {testResult.detail ?? ""}
        </div>
      )}

      {editing && (
        <EditProfileForm
          profile={profile}
          onCancel={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            onChanged();
          }}
        />
      )}

      {confirmDelete && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby={`del-title-${profile.id}`}
          className="rounded-md border border-rose-300 bg-rose-50 px-3 py-3 text-xs text-rose-900"
        >
          <div id={`del-title-${profile.id}`} className="mb-1 font-semibold">
            {t("settings.profilesDeleteConfirmTitle")}
          </div>
          <div className="mb-2">
            {t("settings.profilesDeleteConfirmBody", { label: profile.label })}
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => void handleDelete()}
              disabled={busyAction !== null}
              className="rounded-md bg-rose-700 px-3 py-1 text-[11px] font-medium text-white disabled:opacity-60"
            >
              {t("settings.profilesDeleteConfirmYes")}
            </button>
            <button
              ref={deleteCancelRef}
              type="button"
              onClick={() => setConfirmDelete(false)}
              disabled={busyAction !== null}
              className="rounded-md border border-rose-400 bg-white px-3 py-1 text-[11px] font-medium text-rose-900 disabled:opacity-60"
            >
              {t("settings.profilesDeleteConfirmNo")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

interface EditProfileFormProps {
  profile: Profile;
  onCancel: () => void;
  onSaved: () => void;
}

function EditProfileForm({ profile, onCancel, onSaved }: EditProfileFormProps) {
  const { t } = useTranslation();
  const [label, setLabel] = useState(profile.label);
  const [baseUrl, setBaseUrl] = useState(profile.base_url);
  const [modelId, setModelId] = useState(profile.model_id);
  const [protocol, setProtocol] = useState<Profile["protocol"]>(profile.protocol);
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{
    tone: "ok" | "err";
    text: string;
  } | null>(null);

  async function handleSave(): Promise<void> {
    setBusy(true);
    setFeedback(null);
    const payload: ProfileUpsertRequest = {
      label,
      base_url: baseUrl,
      protocol,
      model_id: modelId,
    };
    if (apiKey.trim()) {
      payload.api_key = apiKey;
    }
    try {
      await updateProfile(profile.id, payload);
      setFeedback({ tone: "ok", text: t("settings.profilesEditSaved") });
      onSaved();
    } catch {
      setFeedback({ tone: "err", text: t("settings.profilesEditFailure") });
    } finally {
      // SECURITY: clear the typed key from React state regardless of
      // success/failure (Reviewer N-UI-3). On failure the user can
      // retype; we never want a rejected key sitting in component
      // state where a later re-render could leak it.
      setApiKey("");
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2 rounded-md border border-ink-200 bg-ink-50 px-3 py-3">
      <div className="text-xs font-semibold text-ink-800">
        {t("settings.profilesEditTitle")}
      </div>
      <div className="text-[11px] text-ink-500">
        {t("settings.profilesEditSubtitle")}
      </div>
      <label className="block">
        <span className="block text-[10px] font-medium uppercase text-ink-500">
          {t("settings.profilesCreateLabelField")}
        </span>
        <input
          type="text"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          className="mt-0.5 w-full rounded-md border border-ink-200 px-2 py-1 text-xs focus:border-brand-500 focus:outline-none"
        />
      </label>
      <label className="block">
        <span className="block text-[10px] font-medium uppercase text-ink-500">
          {t("settings.profilesCreateBaseUrlField")}
        </span>
        <input
          type="text"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          spellCheck={false}
          autoComplete="off"
          className="mt-0.5 w-full rounded-md border border-ink-200 px-2 py-1 font-mono text-[11px] focus:border-brand-500 focus:outline-none"
        />
      </label>
      <label className="block">
        <span className="block text-[10px] font-medium uppercase text-ink-500">
          {t("settings.profilesCreateModelField")}
        </span>
        <input
          type="text"
          value={modelId}
          onChange={(e) => setModelId(e.target.value)}
          spellCheck={false}
          autoComplete="off"
          className="mt-0.5 w-full rounded-md border border-ink-200 px-2 py-1 font-mono text-[11px] focus:border-brand-500 focus:outline-none"
        />
      </label>
      <fieldset>
        <legend className="text-[10px] font-medium uppercase text-ink-500">
          {t("settings.profilesCreateProtocolField")}
        </legend>
        <div className="mt-0.5 flex gap-3 text-xs">
          <label className="flex items-center gap-1">
            <input
              type="radio"
              name={`edit-proto-${profile.id}`}
              checked={protocol === "openai-compat"}
              onChange={() => setProtocol("openai-compat")}
            />
            {t("settings.profilesCreateProtocolOpenAI")}
          </label>
          <label className="flex items-center gap-1">
            <input
              type="radio"
              name={`edit-proto-${profile.id}`}
              checked={protocol === "anthropic"}
              onChange={() => setProtocol("anthropic")}
            />
            {t("settings.profilesCreateProtocolAnthropic")}
          </label>
        </div>
      </fieldset>
      <label className="block">
        <span className="block text-[10px] font-medium uppercase text-ink-500">
          {t("settings.profilesCreateKeyField")}
        </span>
        <input
          type="password"
          autoComplete="new-password"
          spellCheck={false}
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder={t("settings.profilesCreateKeyPlaceholder")}
          className="mt-0.5 w-full rounded-md border border-ink-200 px-2 py-1 font-mono text-[11px] focus:border-brand-500 focus:outline-none"
        />
      </label>
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
      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          className="rounded-md border border-ink-200 px-3 py-1 text-[11px] text-ink-700 hover:bg-ink-50 disabled:opacity-60"
        >
          {t("settings.profilesEditCancel")}
        </button>
        <button
          type="button"
          onClick={() => void handleSave()}
          disabled={busy}
          className={clsx(
            "rounded-md px-3 py-1 text-[11px] font-medium text-white",
            busy ? "cursor-not-allowed bg-ink-300" : "bg-brand-600 hover:bg-brand-700",
          )}
        >
          {busy
            ? t("settings.profilesEditSaving")
            : t("settings.profilesEditSave")}
        </button>
      </div>
    </div>
  );
}
