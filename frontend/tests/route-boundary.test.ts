import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const frontendRoot = resolve(process.cwd());

function readFrontendFile(path: string): string {
  return readFileSync(`${frontendRoot}/${path}`, "utf8");
}

describe("dynamic route server/client boundaries", () => {
  it("keeps course route parsing in a server-safe module", () => {
    const page = readFrontendFile("app/courses/[courseId]/page.tsx");

    expect(page).toContain('from "@/lib/routing/route-ids"');
    expect(page).not.toMatch(
      /parseCourseRouteId[\s\S]*from "@\/components\/aaos\/course-detail-view"/,
    );
  });

  it("keeps document route parsing in a server-safe module", () => {
    const page = readFrontendFile("app/documents/[documentId]/page.tsx");

    expect(page).toContain('from "@/lib/routing/route-ids"');
    expect(page).not.toMatch(
      /parseDocumentRouteId[\s\S]*from "@\/components\/aaos\/document-detail-view"/,
    );
  });
});
