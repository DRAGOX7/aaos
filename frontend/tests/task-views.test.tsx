import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { TaskListView } from "@/components/aaos/task-list-view";

const { useQueryMock, useMutationMock, useQueryClientMock } = vi.hoisted(() => ({
  useQueryMock: vi.fn(),
  useMutationMock: vi.fn(),
  useQueryClientMock: vi.fn(() => ({ invalidateQueries: vi.fn() })),
}));

vi.mock("@tanstack/react-query", async () => ({
  ...(await vi.importActual<typeof import("@tanstack/react-query")>("@tanstack/react-query")),
  useQuery: useQueryMock,
  useMutation: useMutationMock,
  useQueryClient: useQueryClientMock,
}));

const calendarAction = (summary: string) => ({
  action_type: "CREATE_EVENT",
  integration_label: "Calendar",
  summary,
  start: "2026-09-19T10:00:00Z",
  end: "2026-09-19T11:00:00Z",
  timezone: "UTC",
});

describe("task list view", () => {
  beforeEach(() => {
    useQueryMock.mockReset();
    useMutationMock.mockReturnValue({ mutate: vi.fn(), isPending: false, isError: false });
  });

  it("renders a bounded loading state", () => {
    useQueryMock.mockReturnValue({ isPending: true, isError: false });
    render(<TaskListView />);
    expect(screen.getByLabelText("Loading tasks")).toBeInTheDocument();
  });

  it("renders a real empty state without provider or authority fields", () => {
    useQueryMock.mockReturnValue({ data: { tasks: [] }, isPending: false, isError: false });
    render(<TaskListView />);
    expect(screen.getByText("No tasks found.")).toBeInTheDocument();
    expect(screen.queryByText(/provider|scope|permission|credential|sql/i)).not.toBeInTheDocument();
  });

  it("lists study tasks with plain status, priority and course link", () => {
    useQueryMock.mockImplementation((options?: { queryKey?: readonly unknown[] }) =>
      options?.queryKey?.[0] === "study-tasks"
        ? {
            data: {
              study_tasks: [
                {
                  id: 3, title: "review chapter 2", status: "OPEN", priority: "NORMAL",
                  course_id: 9, course_title: "AI445 · Natural Language Processing",
                  assignment_id: null, assignment_title: null, due_at: null,
                  created_at: "2026-10-03T09:00:00Z", updated_at: "2026-10-03T09:00:00Z",
                },
                {
                  id: 1, title: "practice SQL", status: "COMPLETED", priority: "HIGH",
                  course_id: null, course_title: null,
                  assignment_id: null, assignment_title: null, due_at: null,
                  created_at: "2026-10-02T09:00:00Z", updated_at: "2026-10-02T09:00:00Z",
                },
              ],
            },
            isPending: false,
            isError: false,
          }
        : { data: { tasks: [] }, isPending: false, isError: false },
    );
    render(<TaskListView />);

    const list = screen.getByRole("list", { name: "Study tasks" });
    expect(list.querySelector("#study-task-3")).not.toBeNull();
    expect(screen.getByText("To do")).toBeInTheDocument();
    expect(screen.getByText("Done")).toBeInTheDocument();
    expect(screen.getByText("High priority")).toHaveClass("bg-danger-soft", "text-danger-strong");
    expect(screen.getByText("Normal priority")).toHaveClass("bg-warning-soft", "text-warning-strong");
    expect(
      screen.getByRole("link", { name: "AI445 · Natural Language Processing" }),
    ).toHaveAttribute("href", "/courses/9");
    expect(screen.queryByText("No tasks found.")).not.toBeInTheDocument();
  });

  it("keeps unknown outcome visibly distinct from confirmed success", () => {
    useQueryMock.mockReturnValue({
      data: {
        tasks: [{
          task_id: "task-1",
          task_kind: "CALENDAR_CREATE_EVENT",
          status: "UNKNOWN_OUTCOME",
          created_at: "2026-09-18T00:00:00Z",
          updated_at: "2026-09-18T00:00:00Z",
          action: {
            action_type: "CREATE_EVENT",
            integration_label: "Calendar",
            summary: "Study session",
            start: "2026-09-19T10:00:00Z",
            end: "2026-09-19T11:00:00Z",
            timezone: "UTC",
          },
        }],
      },
      isPending: false,
      isError: false,
    });
    render(<TaskListView />);
    expect(screen.getByText("UNKNOWN OUTCOME")).toBeInTheDocument();
    expect(screen.queryByText("CONFIRMED SUCCESS")).not.toBeInTheDocument();
  });

  it("offers decisions only on Calendar actions with a pending approval", () => {
    const mutate = vi.fn();
    useMutationMock.mockReturnValue({ mutate, isPending: false, isError: false });
    const task = (taskId: string, status: string, summary: string) => ({
      task_id: taskId,
      task_kind: "CALENDAR_CREATE_EVENT",
      status,
      created_at: "2026-09-18T00:00:00Z",
      updated_at: "2026-09-18T00:00:00Z",
      action: calendarAction(summary),
    });
    const approval = (approvalId: string, taskId: string, status: string) => ({
      approval_id: approvalId,
      task_id: taskId,
      status,
      issued_at: "2026-09-18T00:00:00Z",
      approval_expires_at: "2026-09-19T00:00:00Z",
      operation_state: null,
      can_approve: status === "PENDING",
      can_reject: status === "PENDING",
      action: calendarAction("unused"),
    });
    useQueryMock.mockImplementation((options?: { queryKey?: readonly unknown[] }) => {
      if (options?.queryKey?.[0] === "approvals") {
        return {
          data: { approvals: [approval("approval-1", "task-1", "PENDING"), approval("approval-2", "task-2", "REJECTED")] },
          isPending: false,
          isError: false,
        };
      }
      if (options?.queryKey?.[0] === "tasks") {
        return {
          data: {
            tasks: [
              task("task-1", "AWAITING_APPROVAL", "Waiting session"),
              task("task-2", "REJECTED", "Rejected session"),
              task("task-3", "AWAITING_APPROVAL", "Unmatched session"),
            ],
          },
          isPending: false,
          isError: false,
        };
      }
      return { data: { study_tasks: [] }, isPending: false, isError: false };
    });
    render(<TaskListView />);

    expect(screen.getAllByRole("button", { name: "Approve" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Reject" })).toHaveLength(1);
    screen.getByRole("button", { name: "Reject" }).click();
    expect(mutate).toHaveBeenCalledWith({ approvalId: "approval-1", decision: "reject" });
    expect(screen.getAllByText("AWAITING APPROVAL")).toHaveLength(2);
  });
});
