import { describe, expect, it } from "vitest";

import { AAOS_NAVIGATION } from "@/components/aaos/app-sidebar";

const ROUTE_ALLOWLIST = new Set([
  "/",
  "/courses",
  "/assignments",
  "/documents",
  "/ask",
  "/tasks",
  "/approvals",
  "/activity",
  "/system-health",
]);

describe("Phase 7G navigation truth", () => {
  it("exposes only supported primary routes", () => {
    const hrefs = AAOS_NAVIGATION.map((item) => item.href);

    expect(hrefs).toHaveLength(9);
    expect(new Set(hrefs)).toEqual(ROUTE_ALLOWLIST);
    expect(hrefs).not.toContain("/settings");
  });
});
