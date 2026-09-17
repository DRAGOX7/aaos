import { describe, expect, it } from "vitest";

import { documentHref } from "@/lib/documents/source-links";

describe("document navigation links", () => {
  it("creates a document route from a positive integer", () => {
    expect(documentHref(7)).toBe("/documents/7");
  });

  it.each([0, -1, 1.5, Number.NaN, Number.POSITIVE_INFINITY])(
    "rejects an unsafe document ID: %s",
    (documentId) => {
      expect(() => documentHref(documentId)).toThrow(
        "AAOS document link requires a positive integer ID.",
      );
    },
  );
});
