export const courseKeys = {
  all: ["courses"] as const,
};

export const academicKeys = {
  course: (courseId: number) => [
    ...courseKeys.all,
    courseId,
  ] as const,
  assignments: (courseId?: number) =>
    courseId === undefined
      ? (["assignments"] as const)
      : (["assignments", courseId] as const),
  documents: (courseId?: number) =>
    courseId === undefined
      ? (["documents"] as const)
      : (["documents", courseId] as const),
  document: (documentId: number) => [
    "documents",
    documentId,
  ] as const,
};
