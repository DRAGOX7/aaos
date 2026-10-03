import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { TaskListView } from "@/components/aaos/task-list-view";

const { useQueryMock } = vi.hoisted(() => ({ useQueryMock: vi.fn() }));

vi.mock("@tanstack/react-query", async () => ({
  ...(await vi.importActual<typeof import("@tanstack/react-query")>("@tanstack/react-query")),
  useQuery: useQueryMock,
}));

describe("task list view", () => {
  beforeEach(() => useQueryMock.mockReset());

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
    expect(screen.getByText(/High priority/)).toBeInTheDocument();
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
});
