import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";

import { ActivityListView } from "@/components/aaos/activity-list-view";

const { useQueryMock, streamBoundaryMock } = vi.hoisted(() => ({
  useQueryMock: vi.fn(),
  streamBoundaryMock: vi.fn(() => null),
}));

vi.mock("@tanstack/react-query", async () => ({
  ...(await vi.importActual<typeof import("@tanstack/react-query")>("@tanstack/react-query")),
  useQuery: useQueryMock,
}));

vi.mock("@/components/aaos/activity-stream", () => ({
  ActivityStreamBoundary: streamBoundaryMock,
}));

describe("activity list view", () => {
  beforeEach(() => {
    useQueryMock.mockReset();
    streamBoundaryMock.mockClear();
  });

  it("renders a bounded loading state", () => {
    useQueryMock.mockReturnValue({ isPending: true, isError: false });
    render(<ActivityListView />);
    expect(screen.getByLabelText("Loading activity")).toBeInTheDocument();
  });

  it("renders a real empty state", () => {
    useQueryMock.mockReturnValue({
      data: { events: [], next_cursor: null },
      isPending: false,
      isError: false,
    });
    render(<ActivityListView />);
    expect(screen.getByText("No activity recorded yet.")).toBeInTheDocument();
  });

  it("renders safe activity and keeps unknown outcome distinct", () => {
    useQueryMock.mockReturnValue({
      data: {
        events: [{
          schema_version: "phase7-activity-v1",
          activity_id: "event-1",
          occurred_at: "2026-09-18T00:00:00Z",
          category: "INTEGRATION",
          event_code: "UNKNOWN_OUTCOME",
          status: "UNKNOWN_OUTCOME",
          resource_type: "TASK",
          resource_id: "task-1",
          title: "Action outcome unknown",
          detail: "Private path and SQL must not render",
          context: null,
        }],
        next_cursor: "cursor-1",
      },
      isPending: false,
      isError: false,
    });
    render(<ActivityListView />);
    expect(screen.getByText("Action outcome unknown")).toBeInTheDocument();
    expect(screen.getByText("UNKNOWN OUTCOME")).toBeInTheDocument();
    expect(screen.queryByText("CONFIRMED SUCCESS")).not.toBeInTheDocument();
    expect(screen.queryByText(/Private path|SQL/)).not.toBeInTheDocument();
  });

  it("renders a safe history error", () => {
    useQueryMock.mockReturnValue({ isPending: false, isError: true });
    render(<ActivityListView />);
    expect(screen.getByText("Activity history is temporarily unavailable.")).toBeInTheDocument();
  });

  it("keeps durable history visible when the live stream is unavailable", () => {
    useQueryMock.mockReturnValue({
      data: {
        events: [{
          schema_version: "phase7-activity-v1",
          activity_id: "event-1",
          occurred_at: "2026-09-18T00:00:00Z",
          category: "SYSTEM",
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

    const props = (streamBoundaryMock.mock.calls[0] as unknown as [{
      onConnectionState: (state: "unavailable") => void;
    }])[0];
    act(() => props.onConnectionState("unavailable"));

    expect(screen.getByText("Action outcome unknown")).toBeInTheDocument();
    expect(
      screen.getByText("Live activity is unavailable; showing durable history."),
    ).toBeInTheDocument();
  });
});
