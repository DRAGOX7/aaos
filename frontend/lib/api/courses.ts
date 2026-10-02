import type { components } from "@/types/api.generated";

import { createApiError } from "@/lib/api/errors";

export type CourseSummary =
  components["schemas"]["CourseSummary"];

export type CourseListResponse =
  components["schemas"]["CourseListResponse"];

export async function fetchCourses(
  signal?: AbortSignal,
): Promise<CourseListResponse> {
  const response = await fetch("/api/v1/courses", {
    method: "GET",
    headers: {
      Accept: "application/json",
    },
    signal,
  });

  if (!response.ok) {
    throw await createApiError(response, "courses");
  }

  return (await response.json()) as CourseListResponse;
}

const archivedDateFormat = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

/** A course Moodle no longer lists (archived by a complete sync). */
export function isPastCourse(course: CourseSummary): boolean {
  return course.status === "past";
}

/** "2 Oct 2026", or null when the archive date is unknown or invalid. */
export function formatArchivedDate(value: string | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : archivedDateFormat.format(date);
}
