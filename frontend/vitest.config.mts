import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

const frontendRoot = fileURLToPath(
  new URL(".", import.meta.url),
);

export default defineConfig({
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
  },
  resolve: {
    alias: {
      "@": frontendRoot,
    },
  },
});
