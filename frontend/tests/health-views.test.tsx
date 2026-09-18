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
    | "DATABASE_UNAVAILABLE"
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
          component("DATABASE", "HEALTHY", true, "RAG_INDEX_READY"),
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
    expect(screen.getByText("DEGRADED")).toBeInTheDocument();
    expect(screen.getByText("DATABASE")).toBeInTheDocument();
    expect(screen.getByText("ACADEMIC DATA")).toBeInTheDocument();
    expect(screen.getByText("RAG INDEX")).toBeInTheDocument();
    expect(screen.getAllByText("HEALTHY")).toHaveLength(1);
    expect(screen.getByText("UNAVAILABLE")).toBeInTheDocument();
    expect(screen.getByText("NOT CONFIGURED")).toBeInTheDocument();
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
    expect(screen.getAllByText("UNKNOWN")).toHaveLength(2);
    expect(screen.getByText("NOT CONFIGURED")).toBeInTheDocument();
    expect(screen.queryByText(/uptime|latency|requests per second|restart/i)).not.toBeInTheDocument();
  });
});
