import { describe, expect, it } from "vitest";

import { courseDisplayName, distinctCourses } from "@/lib/courses/display";
import type { CourseSummary } from "@/lib/api/courses";

function course(id: number, title: string): CourseSummary {
  return { id, title, code: null, description: null, updated_at: null, status: "current" };
}

describe("course display names", () => {
  it.each([
    ["AI380-OPTIMIZATION ALGORITHMS (All Sections )", "AI380", "Optimization Algorithms"],
    ["AI380-OPTIMIZATION ALGORITHMS (1)", "AI380", "Optimization Algorithms"],
    ["AI491-GRADUATION PROJECT (1) (1)", "AI491", "Graduation Project (1)"],
    [
      "HI420-APPLICATIONS OF ARTIFICIAL INTELLIGENCE IN HEALTHCARE (All Sections )",
      "HI420",
      "Applications of Artificial Intelligence in Healthcare",
    ],
    ["AI495C-SPECIAL TOPICS IN ARTIFICIAL INTELLIGENCE (3) (1)", "AI495C",
      "Special Topics in Artificial Intelligence (3)"],
    [
      "HSS100O.T-DISABILITY AND THE SOCIETY(NOT ALLOWED FOR STUDENTS) (All Sections )",
      "HSS100O.T",
      "Disability and the Society (Not Allowed for Students)",
    ],
  ])("reads %s as a code and a readable name", (title, code, name) => {
    expect(courseDisplayName(course(1, title))).toEqual({ code, name });
  });

  it("leaves a title that is not a Moodle code title unchanged", () => {
    expect(courseDisplayName(course(1, "Operating Systems"))).toEqual({
      code: null,
      name: "Operating Systems",
    });
  });

  it("prefers the course's own code field when the title has none", () => {
    expect(courseDisplayName({ ...course(1, "Operating Systems"), code: "CS-42" })).toEqual({
      code: "CS-42",
      name: "Operating Systems",
    });
  });

  it("collapses Moodle section entries into one course, keeping All Sections", () => {
    const result = distinctCourses([
      course(6, "AI380-OPTIMIZATION ALGORITHMS (1)"),
      course(7, "AI380-OPTIMIZATION ALGORITHMS (All Sections )"),
      course(8, "AI445-NATURAL LANGUAGE PROCESSING (1)"),
      course(9, "AI445-NATURAL LANGUAGE PROCESSING (All Sections )"),
      course(42, "Operating Systems"),
    ]);

    expect(result.map((item) => item.id)).toEqual([7, 9, 42]);
  });

  it("keeps two different courses that share no code apart", () => {
    expect(distinctCourses([course(1, "Operating Systems"), course(2, "Databases")]))
      .toHaveLength(2);
  });
});
