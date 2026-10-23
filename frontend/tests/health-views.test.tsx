import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { SystemHealthView } from "@/components/aaos/system-health-view";

const { useQueryMock } = vi.hoisted(() => ({ useQueryMock: vi.fn() }));

vi.mock("@tanstack/react-query", async () => ({
  ...(await vi.importActual<typeof import("@tanstack/react-query")>("@tanstack/react-query")),
  useQuery: useQueryMock,
}));

const component = (
  name: "DATABASE" | "ACADEMIC_DATA" | "RAG_INDEX",
  status: "HEALTHY" | "DEGRADED" | "UNAVAILABLE" | "UNKNOWN" | "NOT_CONFIGURED",
  required: boolean,
  detail_code:
    | "DATABASE_READY"
    | "DATABASE_UNAVAILABLE"
    | "ACADEMIC_DATA_READY"
    | "ACADEMIC_READ_UNAVAILABLE"
    | "RAG_INDEX_READY"
    | "RAG_INDEX_PARTIAL"
    | "RAG_INDEX_NOT_CONFIGURED",
) => ({ name, status, required, detail_code });

describe("system health view", () => {
  beforeEach(() => useQueryMock.mockReset());

  it("renders a bounded loading state", () => {
    useQueryMock.mockReturnValue({ isPending: true, isError: false });
    render(<SystemHealthView />);
    expect(screen.getByLabelText("Loading system health")).toBeInTheDocument();
  });

  it("renders a safe unavailable error", () => {
    useQueryMock.mockReturnValue({ isPending: false, isError: true });
    render(<SystemHealthView />);
    expect(screen.getByText("System health is temporarily unavailable.")).toBeInTheDocument();
  });

  it("renders closed statuses with required and optional grouping", () => {
    useQueryMock.mockReturnValue({
      data: {
        schema_version: "phase7-health-v1",
        checked_at: "2026-09-18T00:00:00Z",
        overall: "DEGRADED",
        components: [
          component("DATABASE", "HEALTHY", true, "DATABASE_READY"),
          component("ACADEMIC_DATA", "UNAVAILABLE", true, "ACADEMIC_READ_UNAVAILABLE"),
          component("RAG_INDEX", "NOT_CONFIGURED", false, "RAG_INDEX_NOT_CONFIGURED"),
        ],
      },
      isPending: false,
      isError: false,
    });
    render(<SystemHealthView />);

    expect(screen.getByRole("heading", { name: "System health", level: 1 })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Required components", level: 2 })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Optional components", level: 2 })).toBeInTheDocument();
    expect(screen.getByText("Some systems are degraded")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Database", level: 3 })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Academic data", level: 3 })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Document index", level: 3 })).toBeInTheDocument();
    expect(screen.getByLabelText("Database status Healthy")).toBeInTheDocument();
    expect(screen.getByLabelText("Academic data status Unavailable")).toBeInTheDocument();
    expect(screen.getByLabelText("Document index status Not configured")).toBeInTheDocument();
    // Regression: a healthy database used to read "RAG index is ready".
    expect(screen.getByText("The local database is reachable and its schema is current.")).toBeInTheDocument();
    expect(screen.queryByText(/DATABASE|ACADEMIC DATA|RAG INDEX/)).not.toBeInTheDocument();
    expect(screen.queryByText(/restart|repair|clear cache|diagnostic|credential|C:\\|\\\\/i)).not.toBeInTheDocument();
  });

  it("renders unknown and not-configured health honestly without fake metrics", () => {
    useQueryMock.mockReturnValue({
      data: {
        schema_version: "phase7-health-v1",
        checked_at: "2026-09-18T00:00:00Z",
        overall: "UNKNOWN",
        components: [
          component("DATABASE", "UNKNOWN", true, "DATABASE_UNAVAILABLE"),
          component("RAG_INDEX", "NOT_CONFIGURED", false, "RAG_INDEX_NOT_CONFIGURED"),
        ],
      },
      isPending: false,
      isError: false,
    });
    render(<SystemHealthView />);
    expect(screen.getByText("Systems need attention")).toBeInTheDocument();
    expect(screen.getByLabelText("Database status Unknown")).toBeInTheDocument();
    expect(screen.getByLabelText("Document index status Not configured")).toBeInTheDocument();
    expect(screen.queryByText(/uptime|latency|requests per second|restart/i)).not.toBeInTheDocument();
  });

  it("explains the Calendar proposals state without any setting value", () => {
    useQueryMock.mockReturnValue({
      data: {
        schema_version: "phase7-health-v1",
        checked_at: "2026-10-24T00:00:00Z",
        overall: "DEGRADED",
        components: [
          { name: "DATABASE", status: "HEALTHY", required: true, detail_code: "DATABASE_READY" },
          {
            name: "GOOGLE_CALENDAR",
            status: "DEGRADED",
            required: false,
            detail_code: "CALENDAR_PROPOSALS_MISCONFIGURED",
          },
        ],
      },
      isPending: false,
      isError: false,
    });
    const { unmount } = render(<SystemHealthView />);
    expect(screen.getByText(/a setting is missing or invalid, so they are off/)).toBeInTheDocument();
    unmount();
    useQueryMock.mockReturnValue({
      data: {
        schema_version: "phase7-health-v1",
        checked_at: "2026-10-24T00:00:00Z",
        overall: "HEALTHY",
        components: [
          { name: "DATABASE", status: "HEALTHY", required: true, detail_code: "DATABASE_READY" },
          { name: "GOOGLE_CALENDAR", status: "HEALTHY", required: false, detail_code: "CALENDAR_PROPOSALS_READY" },
        ],
      },
      isPending: false,
      isError: false,
    });
    render(<SystemHealthView />);
    expect(screen.getByText(/each event is written only after you approve it/i)).toBeInTheDocument();
  });

  it("stacks health status rows on narrow screens", () => {
    useQueryMock.mockReturnValue({
      data: {
        schema_version: "phase7-health-v1",
        checked_at: "2026-09-18T00:00:00Z",
        overall: "HEALTHY",
        components: [component("DATABASE", "HEALTHY", true, "DATABASE_READY")],
      },
      isPending: false,
      isError: false,
    });
    render(<SystemHealthView />);

    expect(screen.getByRole("heading", { name: "Database", level: 3 }).closest("li")).toHaveClass(
      "flex-col",
      "sm:flex-row",
    );
    expect(
      screen.getByRole("heading", { name: "Overall status" }).parentElement?.parentElement
        ?.parentElement,
    ).toHaveClass("flex-col", "sm:flex-row");
  });
});
