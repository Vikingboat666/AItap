/**
 * PromptDetail — loading skeleton, versions list, diff button affordance.
 *
 * The diff button is disabled when a version has no parent_version, and
 * enabled when it does. Asserting both states catches off-by-one bugs
 * in the lookup map (`versionByNumber.get(parent_version)`).
 */
import { describe, expect, it } from "vitest";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import { PromptDetail } from "../PromptDetail";
import { renderWithProviders, screen } from "../../test-utils/render";
import { server } from "../../setupTests";

describe("PromptDetail", () => {
  it("shows skeleton, then renders the version list with a clickable diff button", async () => {
    renderWithProviders(<PromptDetail />, {
      route: "/prompts/p_test_alpha",
      path: "/prompts/:id",
    });

    // 1. Loading skeleton is up before MSW resolves.
    expect(document.querySelector('[aria-busy="true"]')).not.toBeNull();

    // 2. Success — site name + both versions render.
    expect(await screen.findByText("alpha_prompt")).toBeInTheDocument();
    expect(screen.getByText("v1")).toBeInTheDocument();
    expect(screen.getByText("v2")).toBeInTheDocument();

    // v1 has no parent_version => diff button disabled.
    // v2 has parent_version=1 => diff button enabled and labels v1.
    const diffButtons = screen.getAllByRole("button", { name: /^diff/i });
    expect(diffButtons).toHaveLength(2);

    // The button rendered next to v1 has the title hinting "no parent".
    const disabledDiff = diffButtons.find((b) =>
      (b.getAttribute("title") ?? "").includes("no parent"),
    );
    expect(disabledDiff).toBeDefined();
    expect(disabledDiff).toBeDisabled();

    // The button next to v2 references v1 in its label/title.
    const enabledDiff = diffButtons.find((b) =>
      (b.getAttribute("title") ?? "").startsWith("diff v1"),
    );
    expect(enabledDiff).toBeDefined();
    expect(enabledDiff).not.toBeDisabled();

    // 3. Click the enabled diff button — modal opens with the CLI hint.
    await userEvent.click(enabledDiff!);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(
      screen.getByText(/aitap diff p_test_alpha 1 2/),
    ).toBeInTheDocument();
  });

  it("shows a deep-scan hint instead of an empty <pre> when template_text is empty", async () => {
    server.use(
      http.get(
        "/api/prompts/:promptId",
        () =>
          HttpResponse.json({
            site: {
              id: "p_empty",
              name: "empty_text_prompt",
              provider: "openai",
              location: {
                file: "src/agents/runner.py",
                line_start: 10,
                line_end: 15,
                col_start: 0,
                col_end: 0,
              },
              messages: [
                {
                  role: "user",
                  template_text: "",
                  template_kind: "unresolved",
                  variables: [],
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
              confidence: "medium",
              tags: ["openai>=1.0 chat completion"],
            },
            versions: [],
          }),
        { once: true },
      ),
    );

    renderWithProviders(<PromptDetail />, {
      route: "/prompts/p_empty",
      path: "/prompts/:id",
    });

    // Page title renders.
    expect(await screen.findByText("empty_text_prompt")).toBeInTheDocument();

    // Plain-language placeholder is up — title + body + CLI hint.
    expect(
      screen.getByText(/no prompt text resolved/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/deep scan/i),
    ).toBeInTheDocument();
    // The CLI hint is rendered as a code block; the file path is in there.
    expect(
      screen.getByText(/aitap scan --deep src\/agents\/runner\.py/i),
    ).toBeInTheDocument();
  });

  it("swaps the unresolved fallback for an L2-aware message when purpose is filled", async () => {
    // The cc-project eval surfaced this: after the user ran
    // ``aitap scan --deep`` and L2 filled ``purpose`` on a dispatcher
    // call site whose ``messages`` parameter is opaque at L1 (e.g.
    // ``call_openai(messages)`` in client.py), the unresolved message
    // fallback still told them to run deep scan again. The body now
    // branches on ``site.purpose``: if it's set, we point at the
    // existing summary instead of repeating the CLI hint.
    server.use(
      http.get(
        "/api/prompts/:promptId",
        () =>
          HttpResponse.json({
            site: {
              id: "p_dispatcher",
              name: "call_openai",
              provider: "openai",
              location: {
                file: "backend/app/llm/client.py",
                line_start: 266,
                line_end: 271,
                col_start: 0,
                col_end: 0,
              },
              messages: [
                {
                  role: "user",
                  template_text: "",
                  template_kind: "unresolved",
                  variables: [],
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
              purpose:
                "Sends a chat completion request to OpenAI with a user message; expects a user message string as input.",
              confidence: "medium",
              tags: ["openai>=1.0 chat completion"],
            },
            versions: [],
          }),
        { once: true },
      ),
    );

    renderWithProviders(<PromptDetail />, {
      route: "/prompts/p_dispatcher",
      path: "/prompts/:id",
    });

    expect(await screen.findByText("call_openai")).toBeInTheDocument();

    // New L2-aware fallback fires: title says "built at runtime", body
    // points at purpose at the top of the page.
    expect(
      screen.getByText(/built at runtime/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/purpose summary at the top/i),
    ).toBeInTheDocument();

    // The CLI hint is gone — the user already ran deep scan; we
    // don't tell them to run it again. queryByText returns null when
    // the element is absent.
    expect(
      screen.queryByText(/aitap scan --deep backend\/app\/llm\/client\.py/i),
    ).toBeNull();
    // And the original "Run deep scan" hint copy is gone too.
    expect(
      screen.queryByText(/run deep scan to fill in the body/i),
    ).toBeNull();
  });

  it("renders ErrorState and offers retry when the detail endpoint fails", async () => {
    server.use(
      http.get(
        "/api/prompts/:promptId",
        () => new HttpResponse(null, { status: 500 }),
        { once: true },
      ),
    );

    renderWithProviders(<PromptDetail />, {
      route: "/prompts/p_test_alpha",
      path: "/prompts/:id",
    });

    expect(
      await screen.findByText(/couldn't load prompt/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /retry/i }),
    ).toBeInTheDocument();
  });
});
