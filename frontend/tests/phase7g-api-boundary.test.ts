import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const apiRoot = resolve(process.cwd(), "lib/api");

describe("Phase 7G browser API boundary", () => {
  it("keeps academic fetch helpers on same-origin API paths", () => {
    const academic = readFileSync(resolve(apiRoot, "academic.ts"), "utf8");
    const courses = readFileSync(resolve(apiRoot, "courses.ts"), "utf8");
    const health = readFileSync(resolve(apiRoot, "health.ts"), "utf8");

    expect(academic).toContain("/api/v1/courses/");
    expect(academic).toContain("/api/v1/assignments");
    expect(academic).toContain("/api/v1/assignments?course_id=");
    expect(academic).toContain("/api/v1/documents");
    expect(academic).toContain("/api/v1/documents?course_id=");
    expect(academic).toContain("/api/v1/documents/${documentId}");
    expect(courses).toContain("/api/v1/courses");
    expect(health).toContain("/api/v1/system-health");

    expect(academic).not.toMatch(/127\.0\.0\.1:8000|AAOS_BACKEND_ORIGIN/);
  });

  it("keeps the backend origin server-only in Next rewrites", () => {
    const config = readFileSync(
      resolve(process.cwd(), "next.config.ts"),
      "utf8",
    );

    expect(config).toContain("AAOS_BACKEND_ORIGIN");
    expect(config).not.toContain("NEXT_PUBLIC_AAOS_BACKEND_ORIGIN");
  });
});
