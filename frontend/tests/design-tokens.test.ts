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

  it("keeps light as the default theme", () => {
    const root = css.slice(css.indexOf(":root {"), css.indexOf("}", css.indexOf(":root {")));
    expect(root).toContain("color-scheme: light;");
  });

  it("redefines every colour token for the dark theme", () => {
    const start = css.indexOf(".dark {");
    expect(start).toBeGreaterThan(-1);
    const dark = css.slice(start, css.indexOf("}", start));
    expect(dark).toContain("color-scheme: dark;");
    const root = css.slice(css.indexOf(":root {"), css.indexOf("}", css.indexOf(":root {")));
    const colourTokens = [...root.matchAll(/(--aaos-[a-z-]+):\s*(#|rgb)/g)].map((m) => m[1]);
    expect(colourTokens.length).toBeGreaterThan(20);
    for (const token of colourTokens) {
      expect(dark, token).toContain(`${token}:`);
    }
  });

  it("uses the locally managed Plus Jakarta Sans font variable", () => {
    expect(css).toContain(
      "--font-sans: var(--font-jakarta);",
    );
  });

  it("defines the layered dashboard surface tokens", () => {
    expect(css).toContain("--aaos-surface-glass:");
    expect(css).toContain("--aaos-primary-deep:");
    expect(css).toContain("--aaos-radius-panel:");
    expect(css).toContain("--aaos-shadow-float:");
  });
});
