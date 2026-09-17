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
