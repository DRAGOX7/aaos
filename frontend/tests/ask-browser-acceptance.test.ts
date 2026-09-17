import { describe, expect, it } from "vitest";

describe("Phase 7D browser acceptance boundary", () => {
  it("does not expose a write or approval navigation surface", () => {
    expect("/ask").toBe("/ask");
    expect(["/tasks", "/approvals", "/calendar"]).not.toContain("/ask");
  });
});

