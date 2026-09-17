import { describe, expect, it } from "vitest";

import { parseCourseIdParam } from "@/lib/query/course-filter";

describe("assignment course filter", () => {
  it("distinguishes an absent filter", () => {
    expect(parseCourseIdParam(null)).toEqual({ kind: "ABSENT" });
  });

  it.each(["1", "42"])("accepts a positive ASCII integer: %s", (value) => {
    expect(parseCourseIdParam(value)).toEqual({
      kind: "VALID",
      courseId: Number(value),
    });
  });

  it.each(["0", "-1", "1.5", "abc", "", " 42 ", "+1"])(
    "rejects an invalid course filter: %s",
    (value) => {
      expect(parseCourseIdParam(value)).toEqual({ kind: "INVALID" });
    },
  );
});
