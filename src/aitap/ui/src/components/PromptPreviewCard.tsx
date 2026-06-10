/**
 * Show the actual prompt template a Playground user is about to test.
 *
 * Why this exists
 * ---------------
 * Before this card landed, opening a prompt in the Playground dropped
 * the user onto a `CaseEditor` with variable-name placeholders, but
 * nothing on the page told them what the system/user template said.
 * Real-project eval (cc-project, 2026-06): "点击道试验台后,都是空的啊。
 * 原始 prompt 看不到。" — the user could see the prompt text on the
 * Inventory page, click "Open in Playground", and lose all visibility
 * into what they were filling variables for.
 *
 * Design
 * ------
 * One `<Card>` with a header (prompt name + file location + a
 * Hide/Show toggle + an "Open in Inventory" link that jumps to the
 * full `PromptDetail` view for versions / diff / history) and a body
 * that's collapsible by default — the card starts **expanded** so
 * first-time users see the whole template, and collapses to a
 * one-line preview after the user clicks Hide. Persisting that
 * choice across navigations is intentionally NOT done: each prompt is
 * a fresh context and the "I want to see the template" signal is too
 * cheap to gate on prior intent.
 *
 * The expanded body deliberately mirrors `PromptDetail.tsx`'s message
 * list (`<pre>` per message + variables badge + PR #53's
 * "unresolved → CLI hint" fallback) so a user moving between the two
 * pages sees the same shape twice. We don't share the JSX yet — the
 * Detail page has additional version/diff affordances and lifting just
 * the message-list block into a shared component (when it's stable
 * across two callers) is the cleaner refactor than premature DRY.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { Badge, Card, CardHeader } from "./primitives";
import type { PromptSite } from "../api/generated";

export interface PromptPreviewCardProps {
  site: PromptSite;
  /**
   * Controlled-expanded override for testing / parent-driven UX. When
   * omitted, the card manages its own expanded/collapsed state and
   * starts expanded so first-time users see the template.
   */
  defaultExpanded?: boolean;
}

export function PromptPreviewCard({
  site,
  defaultExpanded = true,
}: PromptPreviewCardProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState<boolean>(defaultExpanded);

  // Plain-language one-liner for the collapsed state: first system
  // message's first ~80 chars, otherwise first user message. Empty
  // template_text (UNRESOLVED) falls back to a localised hint so the
  // collapsed state never reads as "broken".
  const collapsedPreview = pickCollapsedPreview(site, t);

  return (
    <Card data-testid="prompt-preview-card">
      <CardHeader
        title={t("promptPreview.title", { name: site.name })}
        subtitle={
          <>
            <span className="font-mono">{site.location.file}</span>
            {":"}
            <span className="font-mono">{site.location.line_start}</span>
            {" · "}
            {t("promptPreview.subtitle")}
          </>
        }
        action={
          <div className="flex items-center gap-2">
            <Link
              to={`/prompts/${encodeURIComponent(site.id)}`}
              className="rounded-md border border-ink-200 bg-white px-2 py-1 text-[11px] font-medium text-ink-700 hover:bg-ink-50"
            >
              {t("promptPreview.openInInventory")}
            </Link>
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              aria-expanded={expanded}
              aria-controls={`prompt-preview-body-${site.id}`}
              className="rounded-md bg-ink-100 px-2 py-1 text-[11px] font-medium text-ink-700 hover:bg-ink-200"
            >
              {expanded ? t("promptPreview.hide") : t("promptPreview.show")}
            </button>
          </div>
        }
      />

      {expanded ? (
        <ul
          id={`prompt-preview-body-${site.id}`}
          className="divide-y divide-ink-100"
        >
          {site.messages.map((m, i) => (
            <li key={i} className="px-4 py-3">
              <div className="mb-2 flex items-center gap-2">
                <Badge>{m.role}</Badge>
                <span className="text-[11px] text-ink-400">
                  {m.template_kind ?? t("prompt.literal")}
                </span>
                {m.variables?.length ? (
                  <span className="text-[11px] text-ink-400">
                    {t("prompt.vars", {
                      names: m.variables.map((v) => v.name).join(", "),
                    })}
                  </span>
                ) : null}
              </div>
              {m.template_text ? (
                <pre className="whitespace-pre-wrap rounded-md bg-ink-50 px-3 py-2 font-mono text-xs text-ink-700">
                  {m.template_text}
                </pre>
              ) : site.purpose ? (
                // Mirror of the PromptDetail branch (see comment there):
                // L2 already filled `purpose`, so don't tell the user to
                // re-run deep scan. Point them at the existing summary.
                <div
                  className="rounded-md border border-dashed border-ink-200 bg-ink-50 px-3 py-3 text-xs text-ink-500"
                  role="note"
                >
                  <p className="mb-1 font-medium text-ink-600">
                    {t("prompt.unresolvedAfterL2Title")}
                  </p>
                  <p>{t("prompt.unresolvedAfterL2Body")}</p>
                </div>
              ) : (
                <div
                  className="rounded-md border border-dashed border-ink-200 bg-ink-50 px-3 py-3 text-xs text-ink-500"
                  role="note"
                >
                  <p className="mb-1 font-medium text-ink-600">
                    {t("prompt.unresolvedTitle")}
                  </p>
                  <p>{t("prompt.unresolvedBody")}</p>
                  <pre className="mt-2 inline-block rounded bg-ink-100 px-2 py-1 font-mono text-[11px] text-ink-700">
                    aitap scan --deep {site.location.file}
                  </pre>
                </div>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <div
          id={`prompt-preview-body-${site.id}`}
          className="px-4 py-3 text-xs text-ink-600"
        >
          <span className="line-clamp-2">{collapsedPreview}</span>
        </div>
      )}
    </Card>
  );
}

/**
 * Pick a one-line collapsed preview from the site's messages. Priority:
 *
 *   1. First non-empty system message.
 *   2. First non-empty user / assistant / tool message (in that order).
 *   3. First non-empty message of any role we don't know about yet — a
 *      forward-compatibility tail so a future scanner that emits e.g.
 *      ``function`` / ``developer`` roles still shows real text instead
 *      of falling through to the "no resolved text" hint.
 *   4. Localised "no resolved text" hint.
 *
 * We trim and truncate so the collapsed row stays a single visual line.
 */
function pickCollapsedPreview(
  site: PromptSite,
  t: (key: string) => string,
): string {
  const MAX = 120;
  const trimmed = (msg: { template_text: string }) =>
    (msg.template_text ?? "").trim().length > 0;
  const format = (text: string) => {
    const single = text.replace(/\s+/g, " ").trim();
    return single.length > MAX ? `${single.slice(0, MAX)}…` : single;
  };

  // Prefer the canonical role order. Source-order first within a role.
  const order = ["system", "user", "assistant", "tool"];
  for (const role of order) {
    const m = site.messages.find((msg) => msg.role === role && trimmed(msg));
    if (m) return format(m.template_text);
  }

  // Role-agnostic fallback: any message with text, regardless of role.
  const any = site.messages.find(trimmed);
  if (any) return format(any.template_text);

  return t("promptPreview.noResolvedText");
}
