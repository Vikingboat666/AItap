/**
 * Vitest setup: jest-dom matchers + a per-test MSW server.
 *
 * - `@testing-library/jest-dom` extends `expect` with DOM-aware
 *   matchers (toBeInTheDocument, toBeDisabled, ...).
 * - `setupServer` boots a node-side request interceptor that the
 *   generated API client transparently hits because its `OpenAPI.BASE`
 *   is the empty string. Tests can call `server.use(...)` to override
 *   a single endpoint without recreating the server.
 *
 * `onUnhandledRequest: "error"` is loud on purpose — if a test forgets
 * to register a handler we want it to fail rather than silently 404.
 *
 * We also stub two jsdom gaps that ReactFlow expects: `ResizeObserver`
 * (used to compute pane size) and `DOMRect.fromRect` (used by the
 * internal viewport math). Both are no-ops; the DagView tests assert
 * on edge styles in the rendered DOM, not on actual layout positions.
 */
/* eslint-disable @typescript-eslint/no-explicit-any */
import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll } from "vitest";
import { cleanup } from "@testing-library/react";
import { setupServer } from "msw/node";

import { handlers } from "./test-utils/handlers";

export const server = setupServer(...handlers);

beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });

  // MSW's `server.listen()` replaces `globalThis.fetch` with a proxy
  // that forwards into Node's undici. Undici then validates the
  // request's AbortSignal against its own internal class — and under
  // `environment: jsdom`, the signal comes from jsdom's polyfill, so
  // the request explodes with "Expected signal to be an instance of
  // AbortSignal" before MSW can answer it.
  //
  // The signal is only used by react-query for request cancellation,
  // which is irrelevant in tests (we disable retries and the suite
  // finishes within ms). Wrap MSW's proxy with a thin signal-stripper
  // so undici sees plain RequestInit and answers from MSW handlers.
  const mswFetch = (globalThis as any).fetch as typeof fetch;
  const stripSignal: typeof fetch = ((input: any, init?: any) => {
    if (init && "signal" in init) {
      const { signal: _drop, ...rest } = init;
      void _drop;
      return mswFetch(input, rest);
    }
    return mswFetch(input, init);
  }) as typeof fetch;
  (globalThis as any).fetch = stripSignal;
  if (typeof window !== "undefined") {
    (window as any).fetch = stripSignal;
  }
});

afterEach(() => {
  cleanup();
  server.resetHandlers();
});

afterAll(() => {
  server.close();
});

// ---- jsdom gap fills for ReactFlow ----------------------------------
// ReactFlow installs a ResizeObserver and queries getBoundingClientRect
// on its root. jsdom ships neither, so stub the bare minimum.
if (typeof globalThis.ResizeObserver === "undefined") {
  class ResizeObserverStub {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  (globalThis as any).ResizeObserver = ResizeObserverStub;
}

if (typeof DOMRect === "undefined") {
  (globalThis as any).DOMRect = class {
    static fromRect() {
      return { x: 0, y: 0, width: 0, height: 0, top: 0, left: 0, right: 0, bottom: 0 };
    }
  };
}

// Some ReactFlow internals call `scrollIntoView`; jsdom doesn't define
// it on HTMLElement.prototype, so direct-assign a no-op.
if (typeof HTMLElement.prototype.scrollIntoView !== "function") {
  (HTMLElement.prototype as any).scrollIntoView = () => {};
}
