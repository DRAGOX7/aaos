import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { ApprovalListView } from "@/components/aaos/approval-list-view";

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

describe("approval list view", () => {
  beforeEach(() => {
    useQueryMock.mockReset();
    useMutationMock.mockReturnValue({ mutate: vi.fn(), isPending: false });
  });

  it("renders a bounded loading state", () => {
    useQueryMock.mockReturnValue({ isPending: true, isError: false });
    render(<ApprovalListView />);
    expect(screen.getByLabelText("Loading approvals")).toBeInTheDocument();
  });

  it("renders an empty state without treating unknown outcome as success", () => {
    useQueryMock.mockReturnValue({ data: { approvals: [] }, isPending: false, isError: false });
    render(<ApprovalListView />);
    expect(screen.getByText("No approvals found.")).toBeInTheDocument();
    expect(screen.queryByText(/success|provider|permission|credential/i)).not.toBeInTheDocument();
  });

  it("shows decisions only for pending approvals and does not optimistically change status", () => {
    const mutate = vi.fn();
    useMutationMock.mockReturnValue({ mutate, isPending: false });
    useQueryMock.mockReturnValue({
      data: {
        approvals: [{
          approval_id: "approval-1",
          task_id: "task-1",
          status: "PENDING",
          issued_at: "2026-09-18T00:00:00Z",
          approval_expires_at: "2026-09-19T00:00:00Z",
          operation_state: null,
          can_approve: true,
          can_reject: true,
          action: {
            action_type: "CREATE_EVENT",
            integration_label: "Calendar",
            summary: "Study session",
            start: "2026-09-19T10:00:00Z",
            end: "2026-09-19T11:00:00Z",
            timezone: "UTC",
          },
        }, {
          approval_id: "approval-2",
          task_id: "task-2",
          status: "CONSUMED",
          issued_at: "2026-09-18T00:00:00Z",
          approval_expires_at: "2026-09-19T00:00:00Z",
          operation_state: "UNKNOWN_OUTCOME",
          can_approve: false,
          can_reject: false,
          action: {
            action_type: "CREATE_EVENT",
            integration_label: "Calendar",
            summary: "Uncertain session",
            start: "2026-09-19T12:00:00Z",
            end: "2026-09-19T13:00:00Z",
            timezone: "UTC",
          },
        }],
      },
      isPending: false,
      isError: false,
    });
    render(<ApprovalListView />);
    expect(screen.getAllByRole("button", { name: "Approve" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Reject" })).toHaveLength(1);
    expect(screen.getByText(/UNKNOWN OUTCOME/)).toBeInTheDocument();
    screen.getByRole("button", { name: "Approve" }).click();
    expect(mutate).toHaveBeenCalledWith({ approvalId: "approval-1", decision: "approve" });
    expect(screen.getByText("PENDING")).toBeInTheDocument();
  });
});
