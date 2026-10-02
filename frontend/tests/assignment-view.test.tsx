import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";

import AssignmentsPage from "@/app/assignments/page";
import { AssignmentList } from "@/components/aaos/assignment-list";
import type { CourseListResponse, CourseSummary } from "@/lib/api/courses";
import type { AssignmentListResponse } from "@/lib/api/academic";
import { ApiError } from "@/lib/api/errors";

const { useQueryMock } = vi.hoisted(() => ({
  useQueryMock: vi.fn(),
}));

vi.mock("@tanstack/react-query", async () => {
  const actual =
    await vi.importActual<typeof import("@tanstack/react-query")>(
      "@tanstack/react-query",
    );

  return {
    ...actual,
    useQuery: useQueryMock,
  };
});

const course42: CourseSummary = {
  id: 42,
  title: "Operating Systems",
  code: "CS-42",
  description: null,
  updated_at: null,
  status: "current",
};

const course7: CourseSummary = {
  id: 7,
  title: "Distributed Systems",
  code: null,
  description: null,
  updated_at: null,
  status: "current",
};

const courses: CourseListResponse = {
  courses: [course7, course42],
};

const now = new Date("2026-09-17T12:00:00.000Z");

const assignments: AssignmentListResponse = {
  assignments: [
    {
      id: 2,
      course_id: 42,
      title: "Operating systems lab",
      description: "Submit the scheduler lab.",
      due_at: "2026-09-17T12:00:00.000Z",
      updated_at: null,
    },
    {
      id: 1,
      course_id: 7,
      title: "Distributed systems reading",
      description: null,
      due_at: null,
      updated_at: null,
    },
    {
      id: 3,
      course_id: 999,
      title: "Unmapped assignment",
      description: null,
      due_at: "2026-09-21T00:00:00.000Z",
      updated_at: null,
    },
  ],
};

function setQueryStates({
  assignmentData,
  assignmentError,
  assignmentPending = false,
  courseData = courses,
  courseError,
  coursePending = false,
}: {
  assignmentData?: AssignmentListResponse;
  assignmentError?: unknown;
  assignmentPending?: boolean;
  courseData?: CourseListResponse;
  courseError?: unknown;
  coursePending?: boolean;
}) {
  useQueryMock
    .mockReturnValueOnce({
      data: assignmentData,
      error: assignmentError,
      isError: assignmentError !== undefined,
      isPending: assignmentPending,
    })
    .mockReturnValueOnce({
      data: courseData,
      error: courseError,
      isError: courseError !== undefined,
      isPending: coursePending,
    });
}

describe("AAOS assignment read view", () => {
  beforeEach(() => {
    useQueryMock.mockReset();
  });

  it("renders a loading state while assignment or course reads are pending", () => {
    setQueryStates({ assignmentPending: true, coursePending: true });

    render(<AssignmentList now={now} />);

    expect(
      screen.getByRole("heading", { level: 1, name: "Assignments" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Loading assignments")).toBeInTheDocument();
  });

  it("renders a truthful empty state", () => {
    setQueryStates({ assignmentData: { assignments: [] } });

    render(<AssignmentList now={now} />);

    expect(screen.getByText("No assignments found.")).toBeInTheDocument();
  });

  it("keeps API order and resolves course context by numeric ID", () => {
    setQueryStates({ assignmentData: assignments });

    render(<AssignmentList now={now} />);

    const rows = screen.getAllByRole("listitem");
    expect(rows.map((row) => within(row).getByRole("heading").textContent)).toEqual([
      "Operating systems lab",
      "Distributed systems reading",
      "Unmapped assignment",
    ]);
    expect(within(rows[0]!).getByText(/Operating Systems/)).toBeInTheDocument();
    expect(within(rows[0]!).getByText(/CS-42/)).toBeInTheDocument();
    expect(within(rows[1]!).getByText(/Distributed Systems/)).toBeInTheDocument();
    expect(within(rows[2]!).getByText("Course unavailable")).toBeInTheDocument();
    expect(within(rows[2]!).queryByText("Operating Systems")).not.toBeInTheDocument();
    expect(screen.getByText("DUE_SOON")).toBeInTheDocument();
    expect(screen.getByText("NO_DUE_DATE")).toBeInTheDocument();
    expect(screen.getByText("LATER")).toBeInTheDocument();
  });

  it("renders a stable not-found state for a missing or inactive course", () => {
    setQueryStates({
      assignmentError: new ApiError(
        404,
        "COURSE_NOT_FOUND",
        "The requested course is not available.",
      ),
    });

    render(<AssignmentList courseId={42} now={now} />);

    expect(screen.getByText("Course not found.")).toBeInTheDocument();
  });

  it("renders a bounded generic error without arbitrary backend content", () => {
    setQueryStates({
      assignmentError: new Error("private traceback, SQL, and local path"),
    });

    render(<AssignmentList now={now} />);

    expect(
      screen.getByText("Academic data is temporarily unavailable."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/private traceback|SQL|local path/i)).not.toBeInTheDocument();
  });

  it("uses no assignment query for an invalid URL filter", async () => {
    const view = await AssignmentsPage({
      searchParams: Promise.resolve({ course_id: "0" }),
    });

    render(view);

    expect(screen.getByText("The course filter is invalid.")).toBeInTheDocument();
    expect(useQueryMock).not.toHaveBeenCalled();
  });

  it("passes a valid URL filter to the assignment query and omits it when absent", async () => {
    setQueryStates({ assignmentData: { assignments: [] } });
    const filteredView = await AssignmentsPage({
      searchParams: Promise.resolve({ course_id: "42" }),
    });

    render(filteredView);

    expect(useQueryMock.mock.calls[0]?.[0].queryKey).toEqual([
      "assignments",
      42,
    ]);

    useQueryMock.mockReset();
    setQueryStates({ assignmentData: { assignments: [] } });
    const unfilteredView = await AssignmentsPage({
      searchParams: Promise.resolve({}),
    });

    render(unfilteredView);

    expect(useQueryMock.mock.calls[0]?.[0].queryKey).toEqual([
      "assignments",
    ]);
  });

  it("does not render completion, task, reminder, approval, or write controls", () => {
    setQueryStates({ assignmentData: assignments });

    render(<AssignmentList now={now} />);

    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
