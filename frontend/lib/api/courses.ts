import type { components } from "@/types/api.generated";

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
    throw new Error(
      `AAOS courses request failed (${response.status})`,
    );
  }

  return (await response.json()) as CourseListResponse;
}
