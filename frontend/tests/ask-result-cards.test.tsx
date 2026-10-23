import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { CalendarProposalCard, MoodleChangeCard } from "@/components/aaos/ask-result-cards";
import type { components } from "@/types/api.generated";

const { approveMock, rejectMock, fetchTaskMock, fetchApprovalMock } = vi.hoisted(() => ({
  approveMock: vi.fn(),
  rejectMock: vi.fn(),
  fetchTaskMock: vi.fn(),
  fetchApprovalMock: vi.fn(),
}));

vi.mock("@/lib/api/tasks", () => ({
  approveApproval: approveMock,
  rejectApproval: rejectMock,
  fetchTask: fetchTaskMock,
  fetchApproval: fetchApprovalMock,
}));

const proposal: components["schemas"]["CalendarProposalView"] = {
  proposal_id: "proposal-123",
  approval_id: "approval-456",
  operation: "CREATE_EVENT",
  summary: "Review Algorithms assignment",
  start: "2026-10-02T14:00:00+03:00",
  end: "2026-10-02T15:00:00+03:00",
  timezone: "Asia/Amman",
  source_entity_kind: "assignment",
  source_entity_id: 42,
  approval_status: "PENDING",
  task_status: "AWAITING_APPROVAL",
  operation_state: null,
};

describe("CalendarProposalCard", () => {
  beforeEach(() => {
    approveMock.mockReset();
    rejectMock.mockReset();
    fetchTaskMock.mockReset();
    fetchApprovalMock.mockReset();
  });

  it("renders Moodle supplied HTML as text without creating active elements", () => {
    const hostileTitle =
      '<img src=x onerror="window.__xss=1"><script>window.__xss=2</script><iframe src="javascript:window.__xss=3"></iframe>';
    const source: components["schemas"]["AcademicAskSource"] = {
      tool_name: "read_moodle_changes",
      entity_kind: "moodle_change",
      entity_id: 17,
      changed_fields: [],
      title: hostileTitle,
    };
    const { container } = render(<MoodleChangeCard source={source} />);

    expect(screen.getByText(hostileTitle)).toBeVisible();
    expect(container.querySelector("script, img, iframe")).toBeNull();
  });

  it("keeps a pending proposal distinct from a created Calendar event", () => {
    render(<CalendarProposalCard proposal={proposal} />);

    expect(screen.getByText("Awaiting approval")).toBeVisible();
    expect(screen.getByText(/This event has not been added to Calendar\. It is added only if you approve it/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Reject" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: /update|delete/i })).not.toBeInTheDocument();
  });

  it("binds approval to the exact returned ID and removes decision controls afterward", async () => {
    approveMock.mockResolvedValue({
      approval_id: "approval-456",
      task_id: "task-789",
      status: "APPROVED",
      operation_state: "EXECUTING",
    });
    fetchTaskMock.mockResolvedValue({
      task_id: "task-789",
      status: "EXECUTING",
      operation_state: "EXECUTING",
      failure_code: null,
    });
    render(<CalendarProposalCard proposal={proposal} />);

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() => expect(approveMock).toHaveBeenCalledWith("approval-456"));
    expect(fetchTaskMock).toHaveBeenCalledWith("task-789");
    expect(await screen.findByText("Approved · executing")).toBeVisible();
    expect(screen.getByText("Approval is recorded; Calendar execution is still in progress.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
    expect(screen.queryByText("Executed")).not.toBeInTheDocument();
  });

  it("lets the user refresh unresolved execution status until it becomes terminal", async () => {
    approveMock.mockResolvedValue({
      approval_id: "approval-456",
      task_id: "task-789",
      status: "APPROVED",
      operation_state: "EXECUTING",
    });
    fetchTaskMock
      .mockResolvedValueOnce({
        task_id: "task-789",
        status: "EXECUTING",
        operation_state: "EXECUTING",
        failure_code: null,
      })
      .mockResolvedValueOnce({
        task_id: "task-789",
        status: "CONFIRMED_SUCCESS",
        operation_state: "CONFIRMED_SUCCESS",
        failure_code: null,
      });
    fetchApprovalMock.mockResolvedValueOnce({
      approval_id: "approval-456",
      task_id: "task-789",
      status: "CONSUMED",
      operation_state: "CONFIRMED_SUCCESS",
    });
    render(<CalendarProposalCard proposal={proposal} />);

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByText("Approved · executing")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Check proposal status" }));

    expect(await screen.findByText("Executed")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Check proposal status" })).not.toBeInTheDocument();
  });

  it("offers status refresh when the post-approval task fetch fails", async () => {
    approveMock.mockResolvedValue({
      approval_id: "approval-456",
      task_id: "task-789",
      status: "APPROVED",
      operation_state: "EXECUTING",
    });
    fetchTaskMock
      .mockRejectedValueOnce(new TypeError("temporary task endpoint failure"))
      .mockResolvedValueOnce({
        task_id: "task-789",
        status: "CONFIRMED_FAILURE",
        operation_state: "CONFIRMED_FAILURE",
        failure_code: "PROVIDER_REJECTED",
      });
    fetchApprovalMock.mockResolvedValueOnce({
      approval_id: "approval-456",
      task_id: "task-789",
      status: "APPROVED",
      operation_state: "CONFIRMED_FAILURE",
    });
    render(<CalendarProposalCard proposal={proposal} />);

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The decision was recorded, but execution status could not be refreshed.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Check proposal status" }));

    expect(await screen.findByText("Execution failed")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Check proposal status" })).not.toBeInTheDocument();
  });

  it("shows confirmed provider failure without claiming execution success", async () => {
    approveMock.mockResolvedValue({
      approval_id: "approval-456",
      task_id: "task-789",
      status: "APPROVED",
      operation_state: "CONFIRMED_FAILURE",
    });
    fetchTaskMock.mockResolvedValue({
      task_id: "task-789",
      status: "CONFIRMED_FAILURE",
      operation_state: "CONFIRMED_FAILURE",
      failure_code: "PROVIDER_REJECTED",
    });
    render(<CalendarProposalCard proposal={proposal} />);

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    expect(await screen.findByText("Execution failed")).toBeVisible();
    expect(screen.getByText("PROVIDER_REJECTED")).toBeVisible();
    expect(screen.queryByText("Executed")).not.toBeInTheDocument();
  });

  it("shows invalidated and not-dispatched as a terminal outcome, not approved success", async () => {
    approveMock.mockResolvedValue({
      approval_id: "approval-456",
      task_id: "task-789",
      status: "APPROVED",
      operation_state: "NOT_DISPATCHED",
    });
    fetchTaskMock.mockResolvedValue({
      task_id: "task-789",
      status: "INVALIDATED",
      operation_state: "NOT_DISPATCHED",
      failure_code: null,
    });
    render(<CalendarProposalCard proposal={proposal} />);

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    expect(await screen.findByText("Invalidated · not dispatched")).toBeVisible();
    expect(screen.getByText("The proposal was invalidated before execution; no Calendar event was created.")).toBeVisible();
    expect(screen.queryByText("Approval is recorded. Calendar execution is not yet confirmed.")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Check proposal status" })).not.toBeInTheDocument();
  });

  it("shows unknown execution outcome until the backend confirms success", async () => {
    approveMock.mockResolvedValue({
      approval_id: "approval-456",
      task_id: "task-789",
      status: "APPROVED",
      operation_state: "UNKNOWN_OUTCOME",
    });
    fetchTaskMock.mockResolvedValue({
      task_id: "task-789",
      status: "UNKNOWN_OUTCOME",
      operation_state: "UNKNOWN_OUTCOME",
      failure_code: null,
    });
    render(<CalendarProposalCard proposal={proposal} />);

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    expect(await screen.findByText("Outcome unknown")).toBeVisible();
    expect(screen.getByText("AAOS could not confirm whether the Calendar event was created.")).toBeVisible();
    expect(screen.queryByText("Executed")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check proposal status" })).toBeEnabled();
  });

  it("shows execution success only when both trusted states confirm it", async () => {
    approveMock.mockResolvedValue({
      approval_id: "approval-456",
      task_id: "task-789",
      status: "CONSUMED",
      operation_state: "CONFIRMED_SUCCESS",
    });
    fetchTaskMock.mockResolvedValue({
      task_id: "task-789",
      status: "CONFIRMED_SUCCESS",
      operation_state: "CONFIRMED_SUCCESS",
      failure_code: null,
    });
    render(<CalendarProposalCard proposal={proposal} />);

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    expect(await screen.findByText("Executed")).toBeVisible();
    expect(screen.getByText("AAOS confirmed that the event was created.")).toBeVisible();
  });

  it("refreshes the exact approval after an uncertain decision response", async () => {
    approveMock.mockRejectedValueOnce(new TypeError("connection lost"));
    fetchApprovalMock.mockResolvedValueOnce({
      approval_id: "approval-456",
      task_id: "task-789",
      status: "APPROVED",
      operation_state: "UNKNOWN_OUTCOME",
    });
    fetchTaskMock.mockResolvedValueOnce({
      task_id: "task-789",
      status: "UNKNOWN_OUTCOME",
      operation_state: "UNKNOWN_OUTCOME",
      failure_code: null,
    });
    render(<CalendarProposalCard proposal={proposal} />);

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    expect(await screen.findByText("Outcome unknown")).toBeVisible();
    expect(fetchApprovalMock).toHaveBeenCalledWith("approval-456");
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });

  it("blocks another decision until an uncertain approval state is refreshed", async () => {
    approveMock.mockRejectedValueOnce(new TypeError("connection lost"));
    fetchApprovalMock
      .mockRejectedValueOnce(new TypeError("refresh failed"))
      .mockResolvedValueOnce({
        approval_id: "approval-456",
        task_id: "task-789",
        status: "PENDING",
        operation_state: null,
      });
    fetchTaskMock.mockResolvedValueOnce({
      task_id: "task-789",
      status: "AWAITING_APPROVAL",
      operation_state: null,
      failure_code: null,
    });
    render(<CalendarProposalCard proposal={proposal} />);

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));

    expect(await screen.findByText("Status not confirmed")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check proposal status" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Check proposal status" }));

    expect(await screen.findByText("Awaiting approval")).toBeVisible();
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Reject" })).toBeEnabled();
  });

  it("renders rejection as final and removes approval controls", async () => {
    rejectMock.mockResolvedValue({
      approval_id: "approval-456",
      task_id: "task-789",
      status: "REJECTED",
      operation_state: null,
    });
    fetchTaskMock.mockResolvedValue({
      task_id: "task-789",
      status: "REJECTED",
      operation_state: null,
      failure_code: null,
    });
    render(<CalendarProposalCard proposal={proposal} />);

    fireEvent.click(screen.getByRole("button", { name: "Reject" }));

    expect(await screen.findByText("Rejected")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
  });
});
