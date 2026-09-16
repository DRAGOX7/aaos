import { describe, expect, it } from "vitest";

import nextConfig from "../next.config";

describe("Next.js AAOS transport boundary", () => {
  it("proxies browser /api traffic to a server-only backend origin", async () => {
    process.env.AAOS_BACKEND_ORIGIN = "http://127.0.0.1:8765";

    const rules =
      typeof nextConfig.rewrites === "function"
        ? await nextConfig.rewrites()
        : [];

    expect(rules).toContainEqual({
      source: "/api/:path*",
      destination: "http://127.0.0.1:8765/api/:path*",
    });
  });

  it("never requires a NEXT_PUBLIC backend origin", async () => {
    delete process.env.NEXT_PUBLIC_AAOS_BACKEND_ORIGIN;
    process.env.AAOS_BACKEND_ORIGIN = "http://127.0.0.1:8765";

    const rules =
      typeof nextConfig.rewrites === "function"
        ? await nextConfig.rewrites()
        : [];

    expect(JSON.stringify(rules)).not.toContain(
      "NEXT_PUBLIC_AAOS_BACKEND_ORIGIN",
    );
  });
});
