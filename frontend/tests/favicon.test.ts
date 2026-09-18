import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

describe("AAOS application icon", () => {
  const icon = readFileSync(
    resolve(process.cwd(), "app/icon.svg"),
    "utf8",
  );
  const layout = readFileSync(
    resolve(process.cwd(), "app/layout.tsx"),
    "utf8",
  );

  it("is a local, deterministic safe SVG with explicit metadata", () => {
    expect(icon).toMatch(/^<svg[\s>]/);
    expect(icon).toContain('viewBox="0 0 64 64"');
    expect(icon).not.toMatch(
      /(?:href|xlink:href)=["'](?:https?:|data:)/i,
    );
    expect(icon).not.toMatch(/DATABASE_URL|token|secret|path/i);
    expect(layout).toContain("icons:");
    expect(layout).toContain('"/icon.svg"');
  });
});
