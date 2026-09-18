import type { components } from "@/types/api.generated";

import { createApiError } from "@/lib/api/errors";

export type TaskListResponse = components["schemas"]["TaskListResponse"];
export type TaskDetail = components["schemas"]["TaskDetail"];
export type ApprovalListResponse = components["schemas"]["ApprovalListResponse"];
export type ApprovalDetail = components["schemas"]["ApprovalDetail"];

function requireIdentifier(value: string): void {
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(value)) {
    throw new Error("AAOS task identifiers must be bounded safe values.");
  }
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    method: "GET",
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) {
    throw await createApiError(response, "task");
  }
  return (await response.json()) as T;
}

async function postJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: "{}",
    signal,
  });
  if (!response.ok) {
    throw await createApiError(response, "approval");
  }
  return (await response.json()) as T;
}

export function fetchTasks(signal?: AbortSignal): Promise<TaskListResponse> {
  return getJson<TaskListResponse>("/api/v1/tasks", signal);
}

export function fetchTask(taskId: string, signal?: AbortSignal): Promise<TaskDetail> {
  requireIdentifier(taskId);
  return getJson<TaskDetail>(`/api/v1/tasks/${encodeURIComponent(taskId)}`, signal);
}

export function fetchApprovals(signal?: AbortSignal): Promise<ApprovalListResponse> {
  return getJson<ApprovalListResponse>("/api/v1/approvals", signal);
}

export function fetchApproval(
  approvalId: string,
  signal?: AbortSignal,
): Promise<ApprovalDetail> {
  requireIdentifier(approvalId);
  return getJson<ApprovalDetail>(
    `/api/v1/approvals/${encodeURIComponent(approvalId)}`,
    signal,
  );
}

export function approveApproval(
  approvalId: string,
  signal?: AbortSignal,
): Promise<ApprovalDetail> {
  requireIdentifier(approvalId);
  return postJson<ApprovalDetail>(
    `/api/v1/approvals/${encodeURIComponent(approvalId)}/approve`,
    signal,
  );
}

export function rejectApproval(
  approvalId: string,
  signal?: AbortSignal,
): Promise<ApprovalDetail> {
  requireIdentifier(approvalId);
  return postJson<ApprovalDetail>(
    `/api/v1/approvals/${encodeURIComponent(approvalId)}/reject`,
    signal,
  );
}
