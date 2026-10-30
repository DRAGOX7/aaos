import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { AssignmentRow } from "@/components/aaos/assignment-row";
import { OverviewUpNext } from "@/components/aaos/overview-up-next";
import { formatDeadline } from "@/lib/time/deadline";

const due = "2026-10-31T22:30:00Z";
const assignment = { id: 1, course_id: 1, title: "Boundary deadline", due_at: due, description: null, updated_at: null };
vi.mock("@tanstack/react-query", async () => ({
  ...(await vi.importActual<typeof import("@tanstack/react-query")>("@tanstack/react-query")),
  useQuery: () => ({ data: { assignments: [assignment], courses: [] }, isPending: false, isError: false }),
}));
afterEach(() => { assignment.due_at = due; vi.useRealTimers(); vi.restoreAllMocks(); });

describe("academic deadline display", () => {
  it.each(["en-GB", "ar-JO-u-nu-latn"])("shows the actual next local day in %s", (locale) => {
    const text = formatDeadline(due, locale, "Asia/Amman");
    const parts = new Intl.DateTimeFormat(locale, { day: "numeric", timeZone: "Asia/Amman" }).format(new Date(due));
    expect(parts).toBe("1");
    expect(text).toContain(parts);
    expect(text).toMatch(/03|GMT|غرينتش/);
    expect(formatDeadline(due, "en-GB", "UTC")).toContain("31 Oct");
  });
  it("uses the same viewer-timezone deadline in Overview and Assignments", () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date("2026-10-30T12:00:00Z"));
    render(<><OverviewUpNext /><ul aria-label="Assignment results"><AssignmentRow assignment={assignment}
      courseContext={{ kind: "UNAVAILABLE" }} now={new Date()} /></ul></>);
    const displayed = document.querySelector("time")!.textContent!;
    expect(within(screen.getByRole("list", { name: "Assignment results" })).getByText(`Due: ${displayed}`)).toBeVisible();
    expect(screen.getAllByText("Due soon")).toHaveLength(2);
  });
  it("preserves the shared upcoming-week urgency boundary", () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date("2026-11-01T20:00:00Z"));
    assignment.due_at = "2026-11-02T06:00:00Z";
    // The existing bucket policy limits Due soon to the current UTC week.
    // This deadline is less than 48 hours away, across that boundary.
    render(<OverviewUpNext />);
    expect(screen.queryByText("Due soon")).not.toBeInTheDocument();
  });
});
