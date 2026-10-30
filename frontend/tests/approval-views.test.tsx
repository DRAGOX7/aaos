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
  it.each([[false, false], [true, false], [false, true]])("honors capabilities %s/%s and previews the scheduled event", (can_approve, can_reject) => {
    useQueryMock.mockReturnValue({ data: { approvals: [{
      approval_id: "a", task_id: "t", status: "PENDING", operation_state: null,
      approval_expires_at: "2026-10-30T10:00:00Z", can_approve, can_reject,
      action: { summary: "Review", integration_label: "Calendar", action_type: "CREATE_EVENT",
        start: "2026-10-31T22:30:00Z", end: "2026-10-31T23:30:00Z", timezone: "Asia/Amman",
        location: "Study room", description: "Review the exercises" },
    }] }, isPending: false, isError: false });
    render(<ApprovalListView />);
    expect(!!screen.queryByRole("button", { name: "Approve" })).toBe(can_approve);
    expect(!!screen.queryByRole("button", { name: "Reject" })).toBe(can_reject);
    expect(screen.getByText("Asia/Amman")).toBeInTheDocument();
    expect(screen.getByText("Study room")).toBeInTheDocument();
    expect(screen.getByText("Review the exercises")).toBeInTheDocument();
    expect(document.querySelector('time[datetime="2026-10-31T22:30:00Z"]')).toHaveTextContent(/Nov|November/);
  });
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

  it("shows the approval deadline only for pending approvals", () => {
    const approval = (id: string, status: string, summary: string) => ({
      approval_id: id,
      task_id: `task-${id}`,
      status,
      issued_at: "2026-10-24T09:00:00Z",
      approval_expires_at: "2026-10-25T09:00:00Z",
      operation_state: null,
      can_approve: status === "PENDING",
      can_reject: status === "PENDING",
      action: {
        action_type: "CREATE_EVENT",
        integration_label: "Google Calendar",
        summary,
        start: "2026-10-25T15:00:00Z",
        end: "2026-10-25T16:00:00Z",
        timezone: "Asia/Amman",
      },
    });
    useQueryMock.mockReturnValue({
      data: { approvals: [approval("a", "PENDING", "Pending one"), approval("b", "REJECTED", "Done one")] },
      isPending: false,
      isError: false,
    });
    render(<ApprovalListView />);
    expect(screen.getAllByText(/^Approve by /)).toHaveLength(1);
    expect(screen.getByText(/Nothing is written to\s+Calendar unless you approve/)).toBeInTheDocument();
  });
});
