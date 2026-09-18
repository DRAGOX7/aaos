import { describe, expect, it } from "vitest";

describe("Phase 7G browser security boundary", () => {
  it("keeps forbidden authority and secret terms out of browser bundles", () => {
    const browserSource = Object.values(import.meta.glob(
      ["../app/**/*.{ts,tsx,svg}", "../components/**/*.{ts,tsx,svg}", "../lib/**/*.{ts,tsx,svg}", "../types/**/*.{ts,tsx,svg}"],
      { eager: true, query: "?raw", import: "default" },
    )).join("\n");
    const forbidden = [
      "DATABASE_URL",
      "Authorization",
      "Bearer",
      "data/auth",
      "data/credentials",
      "TrustedApproverContext",
      "provider payload",
      "Google tokens",
      "OAuth",
      "cookies",
      "traceback",
      "SQL",
      "API keys",
      "PreparedAction",
      "ActionEvent",
      "request fingerprints",
      "idempotency keys",
    ];

    for (const token of forbidden) {
      expect(browserSource.toLowerCase()).not.toContain(token.toLowerCase());
    }
  });
});
