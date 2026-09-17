import { describe, expect, it } from "vitest";

import { courseKeys } from "@/lib/query/courses";
import {
  academicKeys,
  assignmentsQueryOptions,
  courseQueryOptions,
  documentQueryOptions,
  documentsQueryOptions,
} from "@/lib/query/academic";

describe("AAOS academic query keys", () => {
  it("preserves the existing course-list key while adding stable detail keys", () => {
    expect(courseKeys.all).toEqual(["courses"]);
    expect(academicKeys.course(42)).toEqual(["courses", 42]);
    expect(academicKeys.course(42)).toEqual(academicKeys.course(42));
  });

  it("omits course_id from unfiltered assignment and document keys", () => {
    expect(academicKeys.assignments()).toEqual(["assignments"]);
    expect(academicKeys.documents()).toEqual(["documents"]);
    expect(academicKeys.assignments()).not.toContain(undefined);
    expect(academicKeys.documents()).not.toContain(undefined);
  });

  it("includes a validated course_id in filtered keys", () => {
    expect(academicKeys.assignments(42)).toEqual(["assignments", 42]);
    expect(academicKeys.documents(42)).toEqual(["documents", 42]);
    expect(academicKeys.document(7)).toEqual(["documents", 7]);
  });
});

describe("AAOS academic query options", () => {
  it("binds each factory to its corresponding stable key", () => {
    expect(courseQueryOptions(42).queryKey).toEqual(academicKeys.course(42));
    expect(assignmentsQueryOptions().queryKey).toEqual(
      academicKeys.assignments(),
    );
    expect(assignmentsQueryOptions(42).queryKey).toEqual(
      academicKeys.assignments(42),
    );
    expect(documentsQueryOptions().queryKey).toEqual(
      academicKeys.documents(),
    );
    expect(documentsQueryOptions(42).queryKey).toEqual(
      academicKeys.documents(42),
    );
    expect(documentQueryOptions(7).queryKey).toEqual(
      academicKeys.document(7),
    );
  });
});
