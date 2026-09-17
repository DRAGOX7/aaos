export type CourseIdParam =
  | Readonly<{ kind: "ABSENT" }>
  | Readonly<{ kind: "VALID"; courseId: number }>
  | Readonly<{ kind: "INVALID" }>;

export function parseCourseIdParam(value: string | null): CourseIdParam {
  if (value === null) {
    return { kind: "ABSENT" };
  }

  if (!/^[0-9]+$/.test(value)) {
    return { kind: "INVALID" };
  }

  const courseId = Number(value);
  return Number.isSafeInteger(courseId) && courseId > 0
    ? { kind: "VALID", courseId }
    : { kind: "INVALID" };
}
