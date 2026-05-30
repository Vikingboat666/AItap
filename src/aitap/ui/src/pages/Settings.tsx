/**
 * Settings page — per-provider API-key management.
 *
 * Renders one card per supported provider with:
 *
 *   - The current `{configured, source, masked}` state from the
 *     `/api/settings.keys` field.
 *   - A password input (`autoComplete="new-password"`) for entering a
 *     new key, with Save / Test / Clear buttons.
 *   - Plain-language status copy (both en + zh) — see `i18n/*.json`
 *     under the `settings.*` namespace.
 *
 * Security discipline (CLAUDE.md + design doc):
 *
 *   - Input is `type="password"` + `autoComplete="new-password"` so the
 *     browser never autofills it from a saved login and any password
 *     manager treats it as "new".
 *   - After a successful save, the React state holding the typed key
 *     is **immediately cleared** — only the masked preview from the
 *     server's response stays on screen.
 *   - The mutation's response is never logged to console (we never
 *     call `console.*` from this file).
 *   - Test detail strings come back from the API already in
 *     plain-language form; we surface them as-is.
 *
 * The page reads `/api/settings` for the per-provider status and the
 * save / test / clear endpoints from `../api/settings-keys.ts` (which
 * shadows the not-yet-regenerated client; see that file's docstring).
 */
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { apiClient } from "../api/client";
import { ApiError } from "../api/generated";
import { SettingsService } from "../api/generated/services/SettingsService";
import type { SettingsResponse } from "../api/generated/models/SettingsResponse";
import type { SettingsUpdate } from "../api/generated/models/SettingsUpdate";
import { Badge, Card, CardHeader, EmptyState } from "../components/primitives";
import { ErrorState } from "../components/feedback";
import { ListSkeleton } from "../components/skeletons";
import { clsx } from "../lib/clsx";
import {
  type ProviderKeyStatus,
  type ProviderName,
  type TestKeyResponse,
  clearProviderKey,
  saveProviderKey,
  testProviderKey,
} from "../api/settings-keys";

const PROVIDERS: ProviderName[] = ["anthropic", "openai"];

const ENV_VAR_NAMES: Record<ProviderName, string> = {
  anthropic: "ANTHROPIC_API_KEY",
  openai: "OPENAI_API_KEY",
};

function fetchSettings(): Promise<SettingsResponse> {
  return apiClient.settings.getSettingsEndpointApiSettingsGet();
}

export function Settings() {
  const { t } = useTranslation();
  const settingsQ = useQuery({
    queryKey: ["settings"],
    queryFn: fetchSettings,
  });

  if (settingsQ.isLoading) {
    return <ListSkeleton label={t("settings.loading")} rows={2} />;
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

  const keys = settingsQ.data?.keys ?? [];
  const keyMap = new Map<ProviderName, ProviderKeyStatus>(
    keys.map((k) => [k.provider, k]),
  );

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title={t("settings.title")}
          subtitle={t("settings.subtitle")}
        />
      </Card>

      <DefaultsCard
        current={settingsQ.data}
        onSaved={() => void settingsQ.refetch()}
      />

      {PROVIDERS.length === 0 ? (
        <EmptyState
          title={t("settings.title")}
          hint={t("settings.subtitle")}
        />
      ) : (
        <ul className="space-y-4">
          {PROVIDERS.map((provider) => {
            const status: ProviderKeyStatus = keyMap.get(provider) ?? {
              provider,
              configured: false,
              source: "none",
              masked: null,
            };
            return (
              <li key={provider}>
                <ProviderCard
                  status={status}
                  onChanged={() => void settingsQ.refetch()}
                />
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/**
 * Known model ids per provider — clickable hints in the input, not a hard
 * allow-list. Free-text input wins so a new model from either vendor works
 * without needing an aitap release.
 */
const MODEL_HINTS: Record<ProviderName, readonly string[]> = {
  anthropic: ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5"],
  openai: ["gpt-4o", "gpt-4o-mini"],
};

function DefaultsCard({
  current,
  onSaved,
}: {
  current: SettingsResponse | undefined;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  // The form's controlled fields. We initialise from `current` and
  // re-sync whenever the settings query reports new data — so a save
  // (which triggers a refetch) keeps the inputs aligned with the
  // server's authoritative state.
  const [provider, setProvider] = useState<ProviderName>(
    (current?.provider as ProviderName | undefined) ?? "anthropic",
  );
  const [model, setModel] = useState<string>(current?.model ?? "");
  const [judgeModel, setJudgeModel] = useState<string>(current?.judge_model ?? "");
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{
    tone: "ok" | "err";
    text: string;
  } | null>(null);

  // Reset the form when the server-side settings change beneath us.
  // Using JSON.stringify lets the effect react to deep field changes
  // without manual three-field comparison.
  const currentKey = current
    ? `${current.provider}|${current.model}|${current.judge_model ?? ""}`
    : "";
  useEffect(() => {
    if (!current) return;
    setProvider(current.provider as ProviderName);
    setModel(current.model);
    setJudgeModel(current.judge_model ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentKey]);

  const hints = MODEL_HINTS[provider];

  async function handleSave() {
    setBusy(true);
    setFeedback(null);
    const payload: SettingsUpdate = {
      provider,
      model: model.trim() || undefined,
      // Empty string is intentional — the backend normalises it to
      // ``null`` (fall back to ``model``). Send it through so a user
      // can clear a previously-set judge model.
      judge_model: judgeModel,
    };
    try {
      await SettingsService.putSettingsApiSettingsPut({ requestBody: payload });
      setFeedback({ tone: "ok", text: t("settings.defaultsSaved") });
      await queryClient.invalidateQueries({ queryKey: ["settings"] });
      onSaved();
    } catch (err) {
      const message =
        err instanceof Error ? err.message : t("settings.defaultsSaveFailure");
      setFeedback({ tone: "err", text: message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title={t("settings.defaultsTitle")}
        subtitle={t("settings.defaultsSubtitle")}
      />
      <div className="space-y-4 px-4 py-4">
        {/* Provider radio group */}
        <fieldset>
          <legend className="mb-2 text-xs font-medium text-ink-700">
            {t("settings.defaultsProviderLabel")}
          </legend>
          <div className="flex gap-2">
            {(["anthropic", "openai"] as const).map((p) => (
              <label
                key={p}
                className={clsx(
                  "cursor-pointer rounded-md border px-3 py-1.5 text-xs",
                  provider === p
                    ? "border-brand-500 bg-brand-50 text-brand-700"
                    : "border-ink-200 text-ink-700 hover:bg-ink-50",
                )}
              >
                <input
                  type="radio"
                  name="provider"
                  value={p}
                  className="sr-only"
                  checked={provider === p}
                  onChange={() => {
                    // Switching provider also clears the model and
                    // judge-model inputs — keeping a Claude id selected
                    // after switching to OpenAI (or vice-versa) would
                    // let the user save a combination that can't run.
                    // Same shape of footgun as the segment-ui target
                    // switch in M5; we close it the same way.
                    if (p === provider) return;
                    setProvider(p);
                    setModel("");
                    setJudgeModel("");
                  }}
                />
                {p === "anthropic" ? "Anthropic" : "OpenAI"}
              </label>
            ))}
          </div>
        </fieldset>

        {/* Default model */}
        <div>
          <label
            htmlFor="defaults-model"
            className="mb-1 block text-xs font-medium text-ink-700"
          >
            {t("settings.defaultsModelLabel")}
          </label>
          <input
            id="defaults-model"
            type="text"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder={hints[0]}
            spellCheck={false}
            autoComplete="off"
            className="w-full rounded-md border border-ink-200 px-2 py-1 text-xs focus:border-brand-500 focus:outline-none"
          />
          <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-ink-500">
            <span>{t("settings.defaultsCommonHint")}</span>
            {hints.map((h) => (
              <button
                key={h}
                type="button"
                onClick={() => setModel(h)}
                aria-label={t("settings.defaultsModelHintAria", { model: h })}
                className="rounded border border-ink-200 px-1.5 py-0.5 font-mono text-[10px] text-ink-700 hover:bg-ink-50"
              >
                {h}
              </button>
            ))}
          </div>
        </div>

        {/* Judge model */}
        <div>
          <label
            htmlFor="defaults-judge"
            className="mb-1 block text-xs font-medium text-ink-700"
          >
            {t("settings.defaultsJudgeLabel")}
          </label>
          <input
            id="defaults-judge"
            type="text"
            value={judgeModel}
            onChange={(e) => setJudgeModel(e.target.value)}
            placeholder={hints[0]}
            spellCheck={false}
            autoComplete="off"
            className="w-full rounded-md border border-ink-200 px-2 py-1 text-xs focus:border-brand-500 focus:outline-none"
          />
          <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-ink-500">
            <span>{t("settings.defaultsJudgeBlankHint")}</span>
            {hints.map((h) => (
              <button
                key={h}
                type="button"
                onClick={() => setJudgeModel(h)}
                aria-label={t("settings.defaultsJudgeHintAria", { model: h })}
                className="rounded border border-ink-200 px-1.5 py-0.5 font-mono text-[10px] text-ink-700 hover:bg-ink-50"
              >
                {h}
              </button>
            ))}
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
            disabled={busy}
            className={clsx(
              "rounded-md px-3 py-1.5 text-xs font-medium text-white",
              busy
                ? "cursor-not-allowed bg-ink-300"
                : "bg-brand-600 hover:bg-brand-700",
            )}
          >
            {busy ? t("settings.defaultsSaving") : t("settings.defaultsSave")}
          </button>
        </div>
      </div>
    </Card>
  );
}

function ProviderCard({
  status,
  onChanged,
}: {
  status: ProviderKeyStatus;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const provider = status.provider;
  const pretty = provider === "anthropic" ? "Anthropic" : "OpenAI";

  const [typedKey, setTypedKey] = useState("");
  const [saveBusy, setSaveBusy] = useState(false);
  const [testBusy, setTestBusy] = useState(false);
  const [clearBusy, setClearBusy] = useState(false);
  const [feedback, setFeedback] = useState<{
    tone: "ok" | "err" | "warn";
    text: string;
  } | null>(null);
  const [testResult, setTestResult] = useState<TestKeyResponse | null>(null);
  // When the backend responds 409 (OS keyring unreachable on this
  // machine), we show an explicit confirmation before falling back to
  // the local file — silent fallback would violate the security model.
  const [fallbackOpen, setFallbackOpen] = useState(false);

  async function handleSave(useFallback = false): Promise<void> {
    if (!typedKey.trim()) return;
    setSaveBusy(true);
    setFeedback(null);
    try {
      await saveProviderKey({
        provider,
        key: typedKey,
        use_fallback: useFallback,
      });
      // SECURITY: clear the typed key from React state the instant the
      // save returns. Only the masked preview remains, from the refetch.
      setTypedKey("");
      setFallbackOpen(false);
      setFeedback({ tone: "ok", text: t("settings.saveSuccess") });
      await queryClient.invalidateQueries({ queryKey: ["settings"] });
      onChanged();
    } catch (err) {
      // The OS keyring is unreachable on this machine and the user
      // hasn't opted into the file fallback yet. Surface a confirm
      // dialog rather than a silent file-write. The typed key stays in
      // React state until the user picks Cancel or Save-to-file; on
      // cancel they can clear it manually by editing the field.
      if (
        !useFallback &&
        err instanceof ApiError &&
        err.status === 409
      ) {
        setFallbackOpen(true);
        return;
      }
      const message =
        err instanceof Error ? err.message : t("settings.saveFailure");
      setFeedback({ tone: "err", text: message });
    } finally {
      setSaveBusy(false);
    }
  }

  async function handleTest() {
    setTestBusy(true);
    setTestResult(null);
    setFeedback(null);
    try {
      const res = await testProviderKey(provider);
      setTestResult(res);
    } catch (err) {
      const message =
        err instanceof Error ? err.message : t("settings.saveFailure");
      setFeedback({ tone: "err", text: message });
    } finally {
      setTestBusy(false);
    }
  }

  async function handleClear() {
    setClearBusy(true);
    setFeedback(null);
    try {
      await clearProviderKey(provider);
      setTypedKey("");
      setTestResult(null);
      setFeedback({ tone: "ok", text: t("settings.clearSuccess") });
      await queryClient.invalidateQueries({ queryKey: ["settings"] });
      onChanged();
    } catch (err) {
      const message =
        err instanceof Error ? err.message : t("settings.clearFailure");
      setFeedback({ tone: "err", text: message });
    } finally {
      setClearBusy(false);
    }
  }

  const subtitle = describeSource(status, t);

  return (
    <Card>
      <CardHeader
        title={pretty}
        subtitle={subtitle}
        action={
          <Badge tone={status.configured ? "ok" : "warn"}>
            {status.configured
              ? t(`settings.source${capitalize(status.source)}`)
              : t("settings.sourceNone")}
          </Badge>
        }
      />
      <div className="space-y-3 px-4 py-3">
        {status.configured && status.masked && (
          <div className="text-xs text-ink-500">
            {t("settings.maskedPreview", { masked: status.masked })}
          </div>
        )}
        {!status.configured && (
          <div className="text-xs text-ink-500">
            {t("settings.noKeyHint", { provider: pretty })}
          </div>
        )}

        <label className="block">
          <span className="sr-only">
            {t("settings.keyInputLabel", { provider: pretty })}
          </span>
          <input
            type="password"
            autoComplete="new-password"
            spellCheck={false}
            value={typedKey}
            onChange={(e) => setTypedKey(e.target.value)}
            placeholder={t("settings.keyInputPlaceholder", {
              provider: pretty,
            })}
            aria-label={t("settings.keyInputLabel", { provider: pretty })}
            className="w-full rounded-md border border-ink-200 bg-white px-3 py-2 font-mono text-xs text-ink-800 shadow-sm focus:border-brand-400 focus:outline-none"
          />
        </label>

        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={saveBusy || typedKey.trim().length === 0}
            className={clsx(
              "rounded-md px-3 py-1.5 text-sm font-medium",
              saveBusy || typedKey.trim().length === 0
                ? "bg-ink-100 text-ink-400"
                : "bg-brand-600 text-white hover:bg-brand-700",
            )}
          >
            {saveBusy ? t("settings.saving") : t("settings.saveButton")}
          </button>
          <button
            type="button"
            onClick={() => void handleTest()}
            disabled={testBusy}
            className={clsx(
              "rounded-md px-3 py-1.5 text-sm",
              testBusy
                ? "bg-ink-100 text-ink-400"
                : "bg-white text-ink-700 ring-1 ring-ink-200 hover:bg-ink-50",
            )}
          >
            {testBusy ? t("settings.testing") : t("settings.testButton")}
          </button>
          <button
            type="button"
            onClick={() => void handleClear()}
            disabled={clearBusy || !status.configured}
            className={clsx(
              "rounded-md px-3 py-1.5 text-sm",
              clearBusy || !status.configured
                ? "bg-ink-100 text-ink-400"
                : "bg-white text-rose-700 ring-1 ring-rose-200 hover:bg-rose-50",
            )}
          >
            {clearBusy ? t("settings.clearing") : t("settings.clearButton")}
          </button>
        </div>

        {feedback && (
          <div
            role="status"
            className={clsx(
              "rounded-md px-3 py-2 text-xs",
              feedback.tone === "ok"
                ? "bg-emerald-50 text-emerald-700"
                : feedback.tone === "warn"
                  ? "bg-amber-50 text-amber-700"
                  : "bg-rose-50 text-rose-700",
            )}
          >
            {feedback.text}
          </div>
        )}

        {testResult && (
          <div
            role="status"
            className={clsx(
              "rounded-md px-3 py-2 text-xs",
              testResult.ok
                ? "bg-emerald-50 text-emerald-700"
                : "bg-rose-50 text-rose-700",
            )}
          >
            {testResult.detail ??
              (testResult.ok
                ? t("settings.testOk")
                : t("settings.testFail"))}
          </div>
        )}

        {fallbackOpen && (
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby={`fb-title-${provider}`}
            className="rounded-md border border-amber-300 bg-amber-50 px-3 py-3 text-xs text-amber-900"
          >
            <div
              id={`fb-title-${provider}`}
              className="mb-1 font-semibold"
            >
              {t("settings.fallbackConfirmTitle")}
            </div>
            <div className="mb-2">
              {t("settings.fallbackConfirmBody")}
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => void handleSave(true)}
                disabled={saveBusy}
                className="rounded-md bg-amber-700 px-3 py-1 text-[11px] font-medium text-white disabled:opacity-60"
              >
                {t("settings.fallbackConfirmYes")}
              </button>
              <button
                type="button"
                onClick={() => setFallbackOpen(false)}
                disabled={saveBusy}
                className="rounded-md border border-amber-400 bg-white px-3 py-1 text-[11px] font-medium text-amber-900 disabled:opacity-60"
              >
                {t("settings.fallbackConfirmNo")}
              </button>
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

function describeSource(
  status: ProviderKeyStatus,
  t: (key: string, opts?: Record<string, unknown>) => string,
): string {
  if (!status.configured) {
    return t("settings.providerCardSubtitleNone");
  }
  if (status.source === "env") {
    return t("settings.providerCardSubtitleEnv", {
      envVar: ENV_VAR_NAMES[status.provider],
    });
  }
  if (status.source === "fallback") {
    return t("settings.providerCardSubtitleFallback");
  }
  if (status.source === "keyring") {
    return t("settings.providerCardSubtitleKeyring");
  }
  return "";
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}
