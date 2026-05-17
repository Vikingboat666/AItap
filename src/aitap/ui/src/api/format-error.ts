/**
 * Coerce an unknown thrown value into a short, displayable string.
 *
 * Kept in its own module (not in `feedback.tsx`) so it can be reused
 * by non-component code and so the React Fast Refresh linter is happy
 * with the components file only exporting components.
 *
 * The shape we care about is FastAPI's standard error envelope:
 * `{ "detail": "..." }`. Falls back to the raw status/message when the
 * body isn't there or doesn't include a string detail.
 */

import { ApiError } from "./client";

export function formatError(error: unknown): string {
  if (error instanceof ApiError) {
    const body = error.body as { detail?: unknown } | null | undefined;
    const detail = body && typeof body === "object" ? body.detail : undefined;
    if (typeof detail === "string" && detail) {
      return `${error.status} — ${detail}`;
    }
    return `${error.status} ${error.statusText || error.message}`;
  }
  if (error instanceof Error) {
    return error.message || "unknown error";
  }
  if (typeof error === "string") {
    return error;
  }
  return "unknown error";
}
