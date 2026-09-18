import { queryOptions } from "@tanstack/react-query";

import {
  fetchApproval,
  fetchApprovals,
  fetchTask,
  fetchTasks,
} from "@/lib/api/tasks";
import { approvalKeys, taskKeys } from "@/lib/query/keys";

export { approvalKeys, taskKeys } from "@/lib/query/keys";

export function taskListQueryOptions() {
  return queryOptions({
    queryKey: taskKeys.list,
    queryFn: ({ signal }) => fetchTasks(signal),
  });
}

export function taskQueryOptions(taskId: string) {
  return queryOptions({
    queryKey: taskKeys.detail(taskId),
    queryFn: ({ signal }) => fetchTask(taskId, signal),
  });
}

export function approvalListQueryOptions() {
  return queryOptions({
    queryKey: approvalKeys.list,
    queryFn: ({ signal }) => fetchApprovals(signal),
  });
}

export function approvalQueryOptions(approvalId: string) {
  return queryOptions({
    queryKey: approvalKeys.detail(approvalId),
    queryFn: ({ signal }) => fetchApproval(approvalId, signal),
  });
}
