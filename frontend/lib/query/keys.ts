export const courseKeys = {
  all: ["courses"] as const,
};

export const activityKeys = {
  history: (before?: string) => ["activity", "history", before] as const,
};

export const systemHealthKeys = {
  all: ["system-health"] as const,
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
      ? (["documents", "list"] as const)
      : (["documents", "list", courseId] as const),
  document: (documentId: number) => [
    "documents",
    "detail",
    documentId,
  ] as const,
};

export const taskKeys = {
  list: ["tasks", "list"] as const,
  detail: (taskId: string) => ["tasks", "detail", taskId] as const,
};

export const studyTaskKeys = {
  list: ["study-tasks", "list"] as const,
};

export const approvalKeys = {
  list: ["approvals", "list"] as const,
  detail: (approvalId: string) => ["approvals", "detail", approvalId] as const,
};
