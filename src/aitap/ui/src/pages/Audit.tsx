import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { Card, CardHeader } from "../components/primitives";

export function Audit() {
  const { t } = useTranslation();
  const [target, setTarget] = useState("gh:simonw/llm");
  const m = useMutation({
    mutationFn: () => api.triggerScan({ path: target, deep: false }),
  });

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader
          title={t("audit.auditRemoteRepo")}
          subtitle={t("audit.auditRemoteRepoSubtitle")}
        />
        <form
          className="space-y-3 px-4 py-3"
          onSubmit={(e) => {
            e.preventDefault();
            m.mutate();
          }}
        >
          <label className="block text-xs text-ink-500">
            {t("audit.repoTarget")}
          </label>
          <input
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            className="w-full rounded-md border border-ink-200 px-3 py-2 font-mono text-xs focus:border-brand-500 focus:outline-none"
            placeholder={t("audit.repoPlaceholder")}
          />
          <button
            type="submit"
            disabled={m.isPending || !target}
            className="rounded-md bg-brand-600 px-3 py-1.5 text-xs font-medium text-white disabled:cursor-not-allowed disabled:bg-ink-200"
          >
            {m.isPending ? t("audit.scanning") : t("audit.audit")}
          </button>
        </form>
      </Card>

      <Card>
        <CardHeader title={t("audit.result")} />
        <div className="px-4 py-3 text-xs">
          {m.data ? (
            <dl className="grid grid-cols-2 gap-2">
              <dt className="text-ink-500">{t("audit.filesScanned")}</dt>
              <dd className="font-mono text-ink-700">{m.data.files_scanned}</dd>
              <dt className="text-ink-500">{t("audit.promptsFound")}</dt>
              <dd className="font-mono text-ink-700">{m.data.prompt_count}</dd>
              <dt className="text-ink-500">{t("audit.pipelinesFound")}</dt>
              <dd className="font-mono text-ink-700">
                {m.data.pipeline_count}
              </dd>
              <dt className="text-ink-500">{t("audit.warnings")}</dt>
              <dd className="text-ink-700">
                {m.data.warnings.length === 0 ? (
                  <span className="italic text-ink-400">{t("audit.none")}</span>
                ) : (
                  <ul>
                    {m.data.warnings.map((w, i) => (
                      <li key={i}>{w}</li>
                    ))}
                  </ul>
                )}
              </dd>
            </dl>
          ) : (
            <span className="italic text-ink-400">
              {t("audit.noAuditYet")}
            </span>
          )}
        </div>
      </Card>
    </div>
  );
}
