import { Card } from "./primitives";
import { clsx } from "../lib/clsx";

/**
 * Animated loading placeholders used while react-query is fetching.
 *
 * We intentionally keep these dumb (no per-row randomness, no fade-in
 * delay) so snapshot/RTL tests stay deterministic.
 */

export function ListSkeleton({
  label,
  rows = 3,
}: {
  label: string;
  rows?: number;
}) {
  return (
    <Card aria-busy="true" aria-live="polite">
      <div className="border-b border-ink-100 px-4 py-3 text-xs text-ink-500">
        {label}
      </div>
      <ul className="divide-y divide-ink-100">
        {Array.from({ length: rows }).map((_, i) => (
          <li key={i} className="flex items-center justify-between gap-4 px-4 py-3">
            <div className="min-w-0 flex-1 space-y-2">
              <SkeletonBar className="w-1/3" />
              <SkeletonBar className="w-2/3" />
            </div>
            <SkeletonBar className="w-16" />
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function BlockSkeleton({ label }: { label: string }) {
  return (
    <Card aria-busy="true" aria-live="polite" className="space-y-3 p-4">
      <div className="text-xs text-ink-500">{label}</div>
      <SkeletonBar className="w-1/2" />
      <SkeletonBar className="w-full" />
      <SkeletonBar className="w-3/4" />
    </Card>
  );
}

function SkeletonBar({ className }: { className?: string }) {
  return (
    <div
      className={clsx(
        "h-3 animate-pulse rounded bg-ink-100",
        className,
      )}
    />
  );
}
