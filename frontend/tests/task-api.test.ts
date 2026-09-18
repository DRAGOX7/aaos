import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  approveApproval,
  fetchApproval,
  fetchApprovals,
  fetchTask,
  fetchTasks,
  rejectApproval,
} from "@/lib/api/tasks";

function response(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("task and approval browser API", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("uses safe same-origin collection and detail paths", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(() => Promise.resolve(response({ tasks: [] })));

    await fetchTasks();
    await fetchTask("task-1");
    await fetchApprovals();
    await fetchApproval("approval-1");

    expect(fetchMock.mock.calls.map(([input]) => input)).toEqual([
      "/api/v1/tasks",
      "/api/v1/tasks/task-1",
      "/api/v1/approvals",
      "/api/v1/approvals/approval-1",
    ]);
  });

  it("sends only the closed empty command body and no authority fields", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(() => Promise.resolve(response({})));

    await approveApproval("approval-1");
    await rejectApproval("approval-1");

    expect(fetchMock.mock.calls.map(([input, init]) => [input, init?.method, init?.body])).toEqual([
      ["/api/v1/approvals/approval-1/approve", "POST", "{}"],
      ["/api/v1/approvals/approval-1/reject", "POST", "{}"],
    ]);
  });

  it("rejects empty or unsafe identifiers before making a request", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");

    expect(() => fetchTask("")).toThrow();
    expect(() => fetchApproval("../secret")).toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
