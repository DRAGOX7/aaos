import { describe, expect, it } from "vitest";

import {
  bucketAssignment,
  type AssignmentDueBucket,
} from "@/lib/assignments/due-buckets";

const now = new Date("2026-09-17T12:00:00.000Z");

describe("assignment due buckets", () => {
  it.each<[string | null, AssignmentDueBucket]>([
    [null, "NO_DUE_DATE"],
    ["2026-09-17T11:59:59.999Z", "OVERDUE"],
    ["2026-09-17T12:00:00.000Z", "DUE_SOON"],
    ["2026-09-19T11:59:59.999Z", "DUE_SOON"],
    ["2026-09-19T12:00:00.000Z", "THIS_WEEK"],
    ["2026-09-20T23:59:59.999Z", "THIS_WEEK"],
    ["2026-09-21T00:00:00.000Z", "LATER"],
  ])("classifies %s as %s", (dueAt, expected) => {
    expect(bucketAssignment(dueAt, now)).toBe(expected);
  });

  it("handles a Sunday-to-Monday week boundary without reading the clock", () => {
    const sundayNight = new Date("2026-09-20T23:00:00.000Z");

    expect(
      bucketAssignment("2026-09-20T23:59:59.999Z", sundayNight),
    ).toBe("DUE_SOON");
    expect(
      bucketAssignment("2026-09-21T00:00:00.000Z", sundayNight),
    ).toBe("LATER");
  });

  it("rejects invalid timestamps before presentation classification", () => {
    expect(() => bucketAssignment("not-an-iso-date", now)).toThrow(
      "AAOS assignment due date is invalid.",
    );
  });
});
