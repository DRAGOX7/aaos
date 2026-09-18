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
