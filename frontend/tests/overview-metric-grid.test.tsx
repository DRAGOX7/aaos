import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { OverviewMetricGrid } from "@/components/aaos/overview-metric-grid";

const { useQueryMock } = vi.hoisted(() => ({
  useQueryMock: vi.fn(),
}));

vi.mock("@tanstack/react-query", async () => ({
  ...(await vi.importActual<typeof import("@tanstack/react-query")>(
    "@tanstack/react-query",
  )),
  useQuery: useQueryMock,
}));

describe("overview metric grid", () => {
  beforeEach(() => {
    useQueryMock.mockReset();
  });

  it("keeps unavailable metrics subdued and explicit", () => {
    useQueryMock.mockReturnValue({ isPending: false, isError: true });

    render(<OverviewMetricGrid />);

    expect(screen.getAllByText("—")).toHaveLength(4);
    expect(screen.getAllByText("Not connected")).toHaveLength(4);
    expect(screen.queryByText("Unavailable")).not.toBeInTheDocument();
  });

  it("uses compact supporting copy for connected zero-record states", () => {
    useQueryMock.mockReturnValue({
      isPending: false,
      isError: false,
      data: {
        courses: [],
        assignments: [],
        tasks: [],
        approvals: [],
      },
    });

    render(<OverviewMetricGrid />);

    expect(screen.getAllByText("0")).toHaveLength(4);
    expect(screen.getAllByText("Courses in your workspace")).toHaveLength(1);
    expect(screen.getAllByText("Human decisions waiting")).toHaveLength(1);
  });
});
