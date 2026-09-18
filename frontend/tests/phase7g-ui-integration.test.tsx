import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, within } from "@testing-library/react";

import { AppSidebar } from "@/components/aaos/app-sidebar";
import { ActivityListView } from "@/components/aaos/activity-list-view";
import { AskView } from "@/components/aaos/ask-view";
import { SystemHealthView } from "@/components/aaos/system-health-view";

const { useMutationMock, useQueryMock, streamBoundaryMock } = vi.hoisted(() => ({
  useMutationMock: vi.fn(),
  useQueryMock: vi.fn(),
  streamBoundaryMock: vi.fn(() => null),
}));

vi.mock("@tanstack/react-query", async () => ({
  ...(await vi.importActual<typeof import("@tanstack/react-query")>(
    "@tanstack/react-query",
  )),
  useMutation: useMutationMock,
  useQuery: useQueryMock,
}));

vi.mock("@/components/aaos/activity-stream", () => ({
  ActivityStreamBoundary: streamBoundaryMock,
}));

describe("Phase 7G UI integration boundaries", () => {
  beforeEach(() => {
    useMutationMock.mockReset();
    useQueryMock.mockReset();
    streamBoundaryMock.mockClear();
    useMutationMock.mockReturnValue({ mutateAsync: vi.fn(), isPending: false });
  });

  it("keeps primary navigation free of unsupported Settings", () => {
    render(<AppSidebar />);
    const navigation = screen.getByRole("navigation", { name: "AAOS" });

    expect(within(navigation).getAllByRole("link")).toHaveLength(9);
    expect(screen.queryByText("Settings")).not.toBeInTheDocument();
  });

  it("renders Activity heading, honest unknown status, and live-unavailable status", () => {
    useQueryMock.mockReturnValue({
      data: {
        events: [{
          schema_version: "phase7-activity-v1",
          activity_id: "event-1",
          occurred_at: "2026-09-18T00:00:00Z",
          category: "INTEGRATION",
          event_code: "UNKNOWN_OUTCOME",
          status: "UNKNOWN_OUTCOME",
          resource_type: null,
          resource_id: null,
          title: "Action outcome unknown",
          detail: null,
          context: null,
        }],
        next_cursor: "cursor-1",
      },
      isPending: false,
      isError: false,
    });
    render(<ActivityListView />);

    expect(screen.getByRole("heading", { name: "Activity", level: 1 })).toBeVisible();
    expect(screen.getByText("UNKNOWN OUTCOME")).toBeVisible();
    const props = (streamBoundaryMock.mock.calls[0] as unknown as [{
      onConnectionState: (state: "unavailable") => void;
    }])[0];
    act(() => props.onConnectionState("unavailable"));
    expect(screen.getByRole("status")).toHaveAccessibleName();
    expect(screen.getByText("Live activity is unavailable; showing durable history.")).toBeVisible();
  });

  it("keeps System Health unavailable state bounded", () => {
    useQueryMock.mockReturnValue({ isPending: false, isError: true });
    render(<SystemHealthView />);

    expect(screen.getByRole("heading", { name: "System health", level: 1 })).toBeVisible();
    expect(screen.getByText("System health is temporarily unavailable.")).toBeVisible();
  });

  it("keeps insufficient evidence distinct from a grounded answer", () => {
    useQueryMock.mockReturnValue({ data: { courses: [] }, isPending: false });
    useMutationMock.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
      data: {
        status: "INSUFFICIENT_EVIDENCE",
        answer_text: "There is not enough evidence.",
        language: "en",
        citations: [],
      },
    });
    render(<AskView />);

    expect(screen.getByText("Not enough evidence")).toBeVisible();
    expect(screen.getByText("There is not enough evidence.")).toBeVisible();
    expect(screen.queryByText("Grounded answer")).not.toBeInTheDocument();
  });
});
