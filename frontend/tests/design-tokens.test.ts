import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

describe("AAOS design tokens", () => {
  const css = readFileSync(
    resolve(process.cwd(), "app/globals.css"),
    "utf8",
  );

  it("maps shadcn semantics onto AAOS-owned tokens", () => {
    expect(css).toContain(
      "--background: var(--aaos-background);",
    );
    expect(css).toContain(
      "--foreground: var(--aaos-text);",
    );
    expect(css).toContain(
      "--card: var(--aaos-surface);",
    );
    expect(css).toContain(
      "--primary: var(--aaos-primary);",
    );
    expect(css).toContain(
      "--border: var(--aaos-border);",
    );
    expect(css).toContain(
      "--sidebar-primary: var(--aaos-primary);",
    );
  });

  it("keeps Phase 7 light-only", () => {
    expect(css).not.toContain(".dark {");
  });

  it("uses the locally managed Inter font variable", () => {
    expect(css).toContain(
      "--font-sans: var(--font-inter);",
    );
  });
});
