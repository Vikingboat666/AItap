/**
 * Vitest configuration for the aitap web UI.
 *
 * We keep this separate from `vite.config.ts` so the bundler config
 * (proxy, build output, etc.) stays focused on dev/prod builds while
 * the test runner picks up jsdom + the global `expect` extensions.
 *
 * jsdom is required because every component under test renders DOM,
 * and `@testing-library/jest-dom`'s custom matchers (`toBeInTheDocument`,
 * `toBeDisabled`, ...) get registered in `src/setupTests.ts`.
 */
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/setupTests.ts"],
    css: false,
    // ReactFlow measures DOM internally; jsdom doesn't have a real
    // layout engine, but the tests we ship don't depend on actual
    // pixel positions — we only assert on edge styles + node labels.
    server: {
      deps: {
        inline: ["reactflow"],
      },
    },
  },
});
