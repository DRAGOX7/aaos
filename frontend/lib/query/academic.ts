import { queryOptions } from "@tanstack/react-query";

import {
  fetchAssignments,
  fetchCourse,
  fetchDocument,
  fetchDocuments,
} from "@/lib/api/academic";
import { academicKeys } from "@/lib/query/keys";

export { academicKeys } from "@/lib/query/keys";

export function courseQueryOptions(courseId: number) {
  return queryOptions({
    queryKey: academicKeys.course(courseId),
    queryFn: ({ signal }) => fetchCourse(courseId, signal),
  });
}

export function assignmentsQueryOptions(courseId?: number) {
  return queryOptions({
    queryKey: academicKeys.assignments(courseId),
    queryFn: ({ signal }) => fetchAssignments(courseId, signal),
  });
}

export function documentsQueryOptions(courseId?: number) {
  return queryOptions({
    queryKey: academicKeys.documents(courseId),
    queryFn: ({ signal }) => fetchDocuments(courseId, signal),
  });
}

export function documentQueryOptions(documentId: number) {
  return queryOptions({
    queryKey: academicKeys.document(documentId),
    queryFn: ({ signal }) => fetchDocument(documentId, signal),
  });
}
