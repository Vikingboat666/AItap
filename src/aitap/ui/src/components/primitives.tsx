import type { HTMLAttributes, ReactNode } from "react";
import { clsx } from "../lib/clsx";

export function Card({
  className,
  ...rest
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={clsx(
        "rounded-lg border border-ink-200 bg-white shadow-sm",
        className,
      )}
      {...rest}
    />
  );
}

export function CardHeader({
  title,
  subtitle,
  action,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between border-b border-ink-100 px-4 py-3">
      <div>
        <div className="text-sm font-semibold text-ink-800">{title}</div>
        {subtitle && (
          <div className="mt-0.5 text-xs text-ink-500">{subtitle}</div>
        )}
      </div>
      {action}
    </div>
  );
}

export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "brand" | "warn" | "ok" | "err";
}) {
  // `err` is the rose palette — kept in lockstep with the FailureBanner
  // (rose-50 bg / rose-200 border / rose-700 text). Use it for terminal
  // failure surfaces so a small inline badge reads the same severity as
  // the larger `role="alert"` banner it sits next to.
  const palette: Record<string, string> = {
    neutral: "bg-ink-100 text-ink-700",
    brand: "bg-brand-50 text-brand-700",
    warn: "bg-amber-50 text-amber-700",
    ok: "bg-emerald-50 text-emerald-700",
    err: "bg-rose-50 text-rose-700",
  };
  return (
    <span
      className={clsx(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium",
        palette[tone],
      )}
    >
      {children}
    </span>
  );
}

export function EmptyState({
  title,
  hint,
}: {
  title: ReactNode;
  hint?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-ink-200 bg-white px-8 py-12 text-center">
      <div className="text-sm font-medium text-ink-800">{title}</div>
      {hint && <div className="mt-2 text-xs text-ink-500">{hint}</div>}
    </div>
  );
}
