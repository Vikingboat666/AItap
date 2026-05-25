import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { formatError } from "../api/format-error";
import { Card } from "./primitives";

/**
 * Error block with a retry button, used by every page that fetches
 * from the FastAPI backend. Decoding the error into a human string
 * lives in `../api/format-error` so this file stays
 * components-only (Fast Refresh requirement).
 */

export function ErrorState({
  title,
  error,
  onRetry,
}: {
  title: ReactNode;
  error: unknown;
  onRetry?: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Card className="space-y-3 p-6" role="alert">
      <div className="text-sm font-semibold text-rose-700">{title}</div>
      <pre className="overflow-x-auto whitespace-pre-wrap rounded-md bg-rose-50 px-3 py-2 font-mono text-xs text-rose-700">
        {formatError(error)}
      </pre>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="rounded-md bg-brand-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-700"
        >
          {t("common.retry")}
        </button>
      )}
    </Card>
  );
}
