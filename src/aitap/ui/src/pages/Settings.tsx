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
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { apiClient } from "../api/client";
import type { SettingsResponse } from "../api/generated/models/SettingsResponse";
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

  async function handleSave() {
    if (!typedKey.trim()) return;
    setSaveBusy(true);
    setFeedback(null);
    try {
      await saveProviderKey({ provider, key: typedKey });
      // SECURITY: clear the typed key from React state the instant the
      // save returns. Only the masked preview remains, from the refetch.
      setTypedKey("");
      setFeedback({ tone: "ok", text: t("settings.saveSuccess") });
      await queryClient.invalidateQueries({ queryKey: ["settings"] });
      onChanged();
    } catch (err) {
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
                ? t("settings.testOk", { detail: "" })
                : t("settings.testFail", { detail: "" }))}
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
