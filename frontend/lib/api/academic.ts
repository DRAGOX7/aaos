import type { components } from "@/types/api.generated";

import { createApiError } from "@/lib/api/errors";

export type CourseSummary = components["schemas"]["CourseSummary"];
export type AssignmentListResponse =
  components["schemas"]["AssignmentListResponse"];
export type DocumentListResponse =
  components["schemas"]["DocumentListResponse"];
export type DocumentSummary = components["schemas"]["DocumentSummary"];

function requirePositiveInteger(value: number): void {
  if (!Number.isSafeInteger(value) || value <= 0) {
    throw new Error(
      "AAOS academic request requires a positive integer ID.",
    );
  }
}

async function getJson<T>(
  path: string,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(path, {
    method: "GET",
    headers: {
      Accept: "application/json",
    },
    signal,
  });

  if (!response.ok) {
    throw await createApiError(response, "academic");
  }

  return (await response.json()) as T;
}

export function fetchCourse(
  courseId: number,
  signal?: AbortSignal,
): Promise<CourseSummary> {
  requirePositiveInteger(courseId);
  return getJson<CourseSummary>(
    `/api/v1/courses/${courseId}`,
    signal,
  );
}

export function fetchAssignments(
  courseId?: number,
  signal?: AbortSignal,
): Promise<AssignmentListResponse> {
  const path = courseId === undefined
    ? "/api/v1/assignments"
    : (requirePositiveInteger(courseId),
      `/api/v1/assignments?course_id=${courseId}`);
  return getJson<AssignmentListResponse>(path, signal);
}

export function fetchDocuments(
  courseId?: number,
  signal?: AbortSignal,
): Promise<DocumentListResponse> {
  const path = courseId === undefined
    ? "/api/v1/documents"
    : (requirePositiveInteger(courseId),
      `/api/v1/documents?course_id=${courseId}`);
  return getJson<DocumentListResponse>(path, signal);
}

export function fetchDocument(
  documentId: number,
  signal?: AbortSignal,
): Promise<DocumentSummary> {
  requirePositiveInteger(documentId);
  return getJson<DocumentSummary>(
    `/api/v1/documents/${documentId}`,
    signal,
  );
}
