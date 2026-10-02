import { describe, expect, it } from "vitest";

import { courseCardFacts } from "@/lib/courses/card-data";

const NOW = Date.parse("2026-10-03T09:00:00Z");

const section = { id: 6, title: "AI380-OPTIMIZATION ALGORITHMS (1)", code: null };
const allSections = { id: 7, title: "AI380-OPTIMIZATION ALGORITHMS (All Sections )", code: null };
const os = { id: 42, title: "Operating Systems", code: "CS-42" };
const all = [section, allSections, os];
const displayed = [allSections, os];

describe("courseCardFacts", () => {
  it("gathers deadlines and files from every Moodle entry of a course", () => {
    const facts = courseCardFacts(
      displayed,
      all,
      [{ course_id: 6, title: "Lab 4", due_at: "2026-10-05T09:00:00Z" }],
      [{ course_id: 6 }, { course_id: 7 }],
      NOW,
    );

    expect(facts.get(7)).toEqual({
      nextDeadline: { title: "Lab 4", dueAt: Date.parse("2026-10-05T09:00:00Z") },
      fileCount: 2,
    });
    expect(facts.get(42)).toEqual({ nextDeadline: undefined, fileCount: 0 });
  });

  it("picks the earliest upcoming deadline and ignores past or undated ones", () => {
    const facts = courseCardFacts(
      displayed,
      all,
      [
        { course_id: 42, title: "Past quiz", due_at: "2026-10-01T09:00:00Z" },
        { course_id: 42, title: "Undated", due_at: null },
        { course_id: 42, title: "Later", due_at: "2026-10-20T09:00:00Z" },
        { course_id: 42, title: "Sooner", due_at: "2026-10-04T09:00:00Z" },
      ],
      [],
      NOW,
    );

    expect(facts.get(42)?.nextDeadline?.title).toBe("Sooner");
  });

  it("reports unknown, not 'none', when assignments, documents, or the clock are missing", () => {
    expect(courseCardFacts(displayed, all, null, null, NOW).get(7)).toEqual({
      nextDeadline: null,
      fileCount: null,
    });
    expect(courseCardFacts(displayed, all, [], [], null).get(7)?.nextDeadline).toBeNull();
  });
});
