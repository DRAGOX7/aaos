import { describe, expect, it } from "vitest";

import {
  approvalQueryOptions,
  approvalListQueryOptions,
  taskListQueryOptions,
  taskQueryOptions,
} from "@/lib/query/tasks";

describe("task and approval query keys", () => {
  it("keeps task and approval list/detail keys distinct", () => {
    expect(taskListQueryOptions().queryKey).toEqual(["tasks", "list"]);
    expect(taskQueryOptions("task-1").queryKey).toEqual(["tasks", "detail", "task-1"]);
    expect(approvalListQueryOptions().queryKey).toEqual(["approvals", "list"]);
    expect(approvalQueryOptions("approval-1").queryKey).toEqual([
      "approvals",
      "detail",
      "approval-1",
    ]);
  });
});
