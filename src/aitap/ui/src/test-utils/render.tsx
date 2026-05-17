/**
 * Custom test render wrapper.
 *
 * Every page under test needs a `QueryClientProvider` (for `useQuery`)
 * and a router (for `<Link>` and `useParams`). Pulling them into a
 * helper keeps individual tests short and ensures we never accidentally
 * share react-query cache state across tests — `createTestQueryClient`
 * makes a fresh client per call with retries disabled.
 *
 * Test-only helpers — not used by production code paths.
 */
import type { ReactElement, ReactNode } from "react";
import { render, type RenderOptions } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      // Disable retries so an erroring handler shows the `ErrorState`
      // immediately, rather than the test waiting for the default
      // backoff. `gcTime: 0` ensures no fixture leaks between tests.
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
}

interface RenderWithProvidersOptions extends Omit<RenderOptions, "wrapper"> {
  /** Initial entry for `MemoryRouter`. Defaults to `/`. */
  route?: string;
  /** Optional path pattern when a route needs URL params (eg `/prompts/:id`). */
  path?: string;
  /** Reuse an existing client (rare — most tests should let us build one). */
  queryClient?: QueryClient;
}

export function renderWithProviders(
  ui: ReactElement,
  { route = "/", path, queryClient, ...options }: RenderWithProvidersOptions = {},
) {
  const client = queryClient ?? createTestQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => {
    // When the caller supplied a `path`, mount the UI behind that
    // pattern so `useParams` resolves correctly (`/prompts/:id`).
    // Otherwise just hand the children to the router.
    const tree = path ? (
      <Routes>
        <Route path={path} element={children} />
      </Routes>
    ) : (
      children
    );
    return (
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[route]}>{tree}</MemoryRouter>
      </QueryClientProvider>
    );
  };
  return {
    queryClient: client,
    ...render(ui, { wrapper, ...options }),
  };
}

// eslint-disable-next-line react-refresh/only-export-components
export * from "@testing-library/react";
