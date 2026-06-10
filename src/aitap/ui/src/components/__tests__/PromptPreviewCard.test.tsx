/**
 * PromptPreviewCard — show the actual prompt template in the Playground.
 *
 * Six behaviours we pin here:
 *  1. Expanded by default — first-time users see system + user text.
 *  2. Variables badge surfaces the template's `{var}` names so the user
 *     knows what to type into the cases below.
 *  3. The PR #53 unresolved-text fallback fires per message when the
 *     scanner couldn't read literal text from source.
 *  4. The Hide/Show toggle flips the body without losing the header.
 *  5. The collapsed body shows a single-line preview, NOT an empty box
 *     (the cc-project eval bug this card was built to close).
 *  6. The header subtitle carries file path + line number for the
 *     reader who clicked through from Inventory.
 */
import { describe, expect, it } from "vitest";
import userEvent from "@testing-library/user-event";

import { PromptPreviewCard } from "../PromptPreviewCard";
import type { PromptSite } from "../../api/generated";
import { renderWithProviders, screen } from "../../test-utils/render";

function site(overrides: Partial<PromptSite> = {}): PromptSite {
  return {
    id: "p_alpha",
    name: "alpha_prompt",
    provider: "openai",
    confidence: "high",
    location: {
      file: "backend/app/llm/prompt_templates.py",
      line_start: 547,
      line_end: 688,
      col_start: 0,
      col_end: 0,
    },
    messages: [
      {
        role: "system",
        template_text: "You are a helpful storytelling assistant.",
        template_kind: "literal",
        variables: [],
      },
      {
        role: "user",
        template_text: "Write today's entry for {pet_name}.",
        template_kind: "fstring",
        variables: [{ name: "pet_name" }],
      },
    ],
    parameters: {
      model: null,
      temperature: null,
      max_tokens: null,
      top_p: null,
      response_format: null,
      extra: {},
    },
    purpose: null,
    tags: ["template-definition", "builder-function"],
    ...overrides,
  };
}

describe("PromptPreviewCard", () => {
  it("renders both messages with text + roles when expanded by default", () => {
    renderWithProviders(<PromptPreviewCard site={site()} />);

    expect(
      screen.getByText("You are a helpful storytelling assistant."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Write today's entry for {pet_name}."),
    ).toBeInTheDocument();
    // Each message renders a role badge — at least system + user visible.
    expect(screen.getByText("system")).toBeInTheDocument();
    expect(screen.getByText("user")).toBeInTheDocument();
  });

  it("surfaces the template variable names so users know what to fill in", () => {
    renderWithProviders(<PromptPreviewCard site={site()} />);

    // Vars badge from i18n: en `vars: {{names}}` / zh `变量: {{names}}`.
    // The variable name appears in the rendered span.
    expect(
      screen.getByText(/pet_name/, { selector: "span" }),
    ).toBeInTheDocument();
  });

  it("shows the PR #53 unresolved fallback per message instead of an empty <pre>", () => {
    const s = site({
      purpose: null,
      messages: [
        {
          role: "user",
          template_text: "",
          template_kind: "unresolved",
          variables: [],
        },
      ],
    });
    renderWithProviders(<PromptPreviewCard site={s} />);

    // The PR #53 per-message fallback renders the CLI hint in a `<pre>`.
    // Match the pre's exact text: `aitap scan --deep <file>`.
    const hint = screen.getByText(
      "aitap scan --deep backend/app/llm/prompt_templates.py",
      { selector: "pre" },
    );
    expect(hint).toBeInTheDocument();
  });

  it("swaps the unresolved fallback for an L2-aware message when purpose is filled", async () => {
    // Mirror of the PromptDetail.tsx behaviour: once deep scan has run
    // and filled `purpose`, the per-message empty-text fallback must
    // stop telling the user to re-run deep scan. The cc-project eval
    // surfaced this on `call_openai` in client.py — purpose was set
    // but the message body still said "Run deep scan to fill it in",
    // which contradicted the L2 summary already on screen.
    const s = site({
      purpose:
        "Sends a chat completion request to OpenAI with a user message; expects a user message string as input.",
      messages: [
        {
          role: "user",
          template_text: "",
          template_kind: "unresolved",
          variables: [],
        },
      ],
    });
    renderWithProviders(<PromptPreviewCard site={s} />);

    // New L2-aware fallback fires: title says "built at runtime",
    // body points at purpose at the top of the page.
    expect(
      screen.getByText(/built at runtime/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/purpose summary at the top/i),
    ).toBeInTheDocument();

    // The CLI hint is gone — the user already ran deep scan.
    expect(
      screen.queryByText(
        "aitap scan --deep backend/app/llm/prompt_templates.py",
        { selector: "pre" },
      ),
    ).toBeNull();
    expect(
      screen.queryByText(/run deep scan to fill in the body/i),
    ).toBeNull();
  });

  it("collapses to a one-line preview when the user clicks Hide, then re-expands", async () => {
    // Use a long-enough system message that the 120-char collapsed
    // preview clearly truncates with an ellipsis, so the collapsed
    // body's text is provably distinct from the expanded text.
    const long =
      "You are a helpful storytelling assistant. " +
      "Stay warm, gentle, and concrete. Always reply in two short " +
      "paragraphs. Never break character. Never mention training data.";
    const s = site({
      messages: [
        {
          role: "system",
          template_text: long,
          template_kind: "literal",
          variables: [],
        },
        {
          role: "user",
          template_text: "Write today's entry for {pet_name}.",
          template_kind: "fstring",
          variables: [{ name: "pet_name" }],
        },
      ],
    });
    renderWithProviders(<PromptPreviewCard site={s} />);

    // Expanded state — full system text is rendered in a `<pre>`.
    expect(
      screen.getByText(long, { selector: "pre" }),
    ).toBeInTheDocument();

    // Click the hide toggle.
    const toggle = screen.getByRole("button", { name: /hide template/i });
    await userEvent.click(toggle);

    // Collapsed — the `<pre>` is gone, replaced by a truncated `<span>`
    // that starts with the same text and ends with the ellipsis.
    expect(
      screen.queryByText(long, { selector: "pre" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(/You are a helpful storytelling assistant\..*…$/, {
        selector: "span",
      }),
    ).toBeInTheDocument();

    // Toggle label flips to Show, click it to re-expand.
    const reExpand = screen.getByRole("button", {
      name: /show full template/i,
    });
    await userEvent.click(reExpand);
    expect(
      screen.getByText(long, { selector: "pre" }),
    ).toBeInTheDocument();
  });

  it("collapsed state shows the no-resolved-text hint when every message is empty", async () => {
    const s = site({
      messages: [
        {
          role: "user",
          template_text: "",
          template_kind: "unresolved",
          variables: [],
        },
      ],
    });
    renderWithProviders(<PromptPreviewCard site={s} />);

    // Collapse — the CLI-hint `<pre>` should disappear from view.
    const toggle = screen.getByRole("button", { name: /hide template/i });
    await userEvent.click(toggle);

    expect(
      screen.queryByText(
        "aitap scan --deep backend/app/llm/prompt_templates.py",
        { selector: "pre" },
      ),
    ).toBeNull();

    // The collapsed body falls back to `promptPreview.noResolvedText`
    // which mentions the deep-scan hint inside a regular `<span>`.
    expect(
      screen.getByText(/aitap scan --deep/, { selector: "span" }),
    ).toBeInTheDocument();
  });

  it("renders the file path + line number in the header subtitle", () => {
    renderWithProviders(<PromptPreviewCard site={site()} />);

    // file path lives in its own `<span class="font-mono">`.
    expect(
      screen.getByText("backend/app/llm/prompt_templates.py", {
        selector: "span",
      }),
    ).toBeInTheDocument();
    // line_start is in a sibling `<span class="font-mono">547</span>`.
    expect(
      screen.getByText("547", { selector: "span" }),
    ).toBeInTheDocument();
  });

  it("collapsed preview prefers system over user even when system appears later in the array", async () => {
    // System after user in source order — pickCollapsedPreview should
    // still surface the system message in the collapsed body. This
    // pins the canonical role-priority documented in the helper.
    const longUser =
      "This user message is intentionally long so we can tell which " +
      "string ended up in the collapsed preview if priority broke. " +
      "Plenty of filler text here to push us over the truncation cap.";
    const longSystem =
      "Distinct system content — the canonical role priority must " +
      "pick this string for the collapsed preview regardless of the " +
      "source-order position of the system message in the array.";
    const s = site({
      messages: [
        {
          role: "user",
          template_text: longUser,
          template_kind: "literal",
          variables: [],
        },
        {
          role: "system",
          template_text: longSystem,
          template_kind: "literal",
          variables: [],
        },
      ],
    });
    renderWithProviders(<PromptPreviewCard site={s} />);

    const toggle = screen.getByRole("button", { name: /hide template/i });
    await userEvent.click(toggle);

    // Collapsed body shows the system string (truncated), not the user one.
    expect(
      screen.getByText(/^Distinct system content/, { selector: "span" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/^This user message/, { selector: "span" }),
    ).toBeNull();
  });

  it("collapsed preview falls back to non-canonical roles when no system/user/assistant/tool is present", async () => {
    // Pretend a future scanner introduced a `developer` role we don't
    // know about yet. The role-agnostic tail should still surface the
    // text instead of dropping to the no-resolved-text hint.
    const s = site({
      messages: [
        {
          role: "developer" as unknown as "user",
          template_text: "Developer-role text that should still appear.",
          template_kind: "literal",
          variables: [],
        },
      ],
    });
    renderWithProviders(<PromptPreviewCard site={s} />);

    const toggle = screen.getByRole("button", { name: /hide template/i });
    await userEvent.click(toggle);

    expect(
      screen.getByText(/^Developer-role text/, { selector: "span" }),
    ).toBeInTheDocument();
    // The no-resolved-text fallback must NOT have fired — there's
    // actual text to show.
    expect(
      screen.queryByText(/Prompt text couldn't be read/),
    ).toBeNull();
  });

  it("renders header + no-resolved-text hint when the messages array is empty", () => {
    // Zero-message site shouldn't crash and should never read as a
    // hard-broken card. The expanded body's `<ul>` is empty, but the
    // header still names the prompt and the user can take a next step.
    const s = site({ messages: [] });
    const { container } = renderWithProviders(<PromptPreviewCard site={s} />);

    // No `<li>` rows in the message list.
    expect(container.querySelectorAll("li")).toHaveLength(0);
    // Header still has the prompt name + open-detail link.
    expect(
      screen.getByText(/alpha_prompt/, { selector: "span,div,h2,h3" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /open in inventory/i }),
    ).toBeInTheDocument();
  });

  it("header carries an open-in-inventory link to /prompts/{id}", () => {
    renderWithProviders(<PromptPreviewCard site={site()} />);

    const link = screen.getByRole("link", { name: /open in inventory/i });
    expect(link.getAttribute("href")).toBe("/prompts/p_alpha");
  });
});
