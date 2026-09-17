import { describe, expect, it } from "vitest";

import AskPage from "@/app/ask/page";

describe("Ask AAOS page", () => {
  it("renders the Ask view entry point", () => {
    expect(AskPage).toBeTypeOf("function");
  });
});

