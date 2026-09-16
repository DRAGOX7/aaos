import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

describe("AAOS root providers", () => {
  const layout = readFileSync(
    resolve(process.cwd(), "app/layout.tsx"),
    "utf8",
  );

  it("provides tooltip context at the application boundary", () => {
    expect(layout).toContain(
      'import { TooltipProvider } from "@/components/ui/tooltip";',
    );
    expect(layout).toContain("<TooltipProvider>");
  });

  it("composes the AAOS shell at the root boundary", () => {
    expect(layout).toContain(
      'import { AppShell } from "@/components/aaos/app-shell";',
    );
    expect(layout).toContain(
      "<AppShell>{children}</AppShell>",
    );
  });
});
