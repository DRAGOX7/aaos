import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { ApprovalListView } from "@/components/aaos/approval-list-view";
import { TaskListView } from "@/components/aaos/task-list-view";

const { useMutationMock, useQueryClientMock, useQueryMock } = vi.hoisted(() => ({
  useMutationMock: vi.fn(),
  useQueryClientMock: vi.fn(() => ({ invalidateQueries: vi.fn() })),
  useQueryMock: vi.fn(),
}));

vi.mock("@tanstack/react-query", async () => ({
  ...(await vi.importActual<typeof import("@tanstack/react-query")>(
    "@tanstack/react-query",
  )),
  useMutation: useMutationMock,
  useQuery: useQueryMock,
  useQueryClient: useQueryClientMock,
}));

describe("Phase 7G state and authority boundaries", () => {
  beforeEach(() => {
    useQueryMock.mockReturnValue({
      data: { tasks: [] },
      isPending: false,
      isError: false,
    });
    useMutationMock.mockReturnValue({ mutate: vi.fn(), isPending: false });
  });

  it("renders a truthful empty task state without execution controls", () => {
    render(<TaskListView />);

    expect(screen.getByText("No tasks found.")).toBeVisible();
    expect(screen.queryByRole("button", { name: /retry execution|repair|restart/i })).not.toBeInTheDocument();
  });

  it("renders a truthful empty approval state without mutation controls", () => {
    useQueryMock.mockReturnValue({
      data: { approvals: [] },
      isPending: false,
      isError: false,
    });
    render(<ApprovalListView />);

    expect(screen.getByText("No approvals found.")).toBeVisible();
    expect(screen.queryByRole("button", { name: /approve|reject|retry|repair|restart/i })).not.toBeInTheDocument();
  });
});
