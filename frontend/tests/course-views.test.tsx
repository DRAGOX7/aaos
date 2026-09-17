import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";

import { CourseDetailView } from "@/components/aaos/course-detail-view";
import { CourseListView } from "@/components/aaos/course-list-view";
import { ApiError } from "@/lib/api/errors";
import type { CourseListResponse, CourseSummary } from "@/lib/api/courses";
import { parseCourseRouteId } from "@/lib/routing/route-ids";

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

const courses: CourseSummary[] = [
  {
    id: 42,
    title: "Operating Systems",
    code: "CS-42",
    description: "Kernel and systems programming.",
    updated_at: null,
  },
  {
    id: 7,
    title: "Distributed Systems",
    code: null,
    description: null,
    updated_at: null,
  },
];

function setQueryState({
  data,
  error,
  isError = false,
  isPending = false,
}: {
  data?: CourseListResponse | CourseSummary;
  error?: unknown;
  isError?: boolean;
  isPending?: boolean;
}) {
  useQueryMock.mockReturnValue({
    data,
    error,
    isError,
    isPending,
  });
}

describe("AAOS course read views", () => {
  beforeEach(() => {
    useQueryMock.mockReset();
  });

  describe("course list", () => {
    it("renders an accessible loading state", () => {
      setQueryState({ isPending: true });

      render(<CourseListView />);

      expect(
        screen.getByRole("heading", { level: 1, name: "Courses" }),
      ).toBeInTheDocument();
      const pageHeader = screen.getByRole("banner");
      expect(
        within(pageHeader).getByRole("heading", {
          level: 1,
          name: "Courses",
        }),
      ).toBeInTheDocument();
      expect(
        within(pageHeader).getByText(
          "Active courses in your academic workspace.",
        ),
      ).toBeInTheDocument();
      expect(screen.getByLabelText("Loading courses")).toBeInTheDocument();
    });

    it("renders the real empty state without fabricated academic data", () => {
      setQueryState({ data: { courses: [] } });

      render(<CourseListView />);

      expect(screen.getByText("No active courses found.")).toBeInTheDocument();
      expect(screen.queryByText(/grade|progress|deadline|schedule|metric/i)).not.toBeInTheDocument();
    });

    it("renders only returned courses in API order with accessible links", () => {
      setQueryState({ data: { courses } });

      render(<CourseListView />);

      const links = screen.getAllByRole("link");
      expect(links.map((link) => link.getAttribute("href"))).toEqual([
        "/courses/42",
        "/courses/7",
      ]);
      expect(screen.getByText("Operating Systems")).toBeInTheDocument();
      expect(screen.getByText("Distributed Systems")).toBeInTheDocument();
      expect(screen.getByText("CS-42")).toBeInTheDocument();
      expect(screen.queryByText("grade")).not.toBeInTheDocument();
      expect(screen.queryByText("progress")).not.toBeInTheDocument();
    });

    it("renders a bounded generic error state", () => {
      setQueryState({
        error: new Error("private traceback, SQL, and local path"),
        isError: true,
      });

      render(<CourseListView />);

      expect(
        screen.getByText("Academic data is temporarily unavailable."),
      ).toBeInTheDocument();
      expect(screen.queryByText(/private traceback|SQL|local path/i)).not.toBeInTheDocument();
    });
  });

  describe("course detail", () => {
    it("renders an accessible loading state", () => {
      setQueryState({ isPending: true });

      render(<CourseDetailView courseId={42} />);

      expect(screen.getByLabelText("Loading course")).toBeInTheDocument();
    });

    it("renders only the returned CourseSummary fields and read links", () => {
      setQueryState({ data: courses[0] });

      render(<CourseDetailView courseId={42} />);

      expect(
        screen.getByRole("heading", { level: 1, name: "Operating Systems" }),
      ).toBeInTheDocument();
      expect(screen.getByText("CS-42")).toBeInTheDocument();
      expect(screen.getByText("Kernel and systems programming.")).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "View assignments" })).toHaveAttribute(
        "href",
        "/assignments?course_id=42",
      );
      expect(screen.getByRole("link", { name: "View documents" })).toHaveAttribute(
        "href",
        "/documents?course_id=42",
      );
      expect(screen.queryByText(/grade|progress|deadline|schedule|metric|enrollment/i)).not.toBeInTheDocument();
    });

    it("renders a bounded not-found state for COURSE_NOT_FOUND", () => {
      setQueryState({
        error: new ApiError(
          404,
          "COURSE_NOT_FOUND",
          "The requested course is not available.",
        ),
        isError: true,
      });

      render(<CourseDetailView courseId={42} />);

      expect(screen.getByText("Course not found.")).toBeInTheDocument();
    });

    it("renders a safe generic error without arbitrary backend content", () => {
      setQueryState({
        error: new ApiError(
          500,
          "INTERNAL_ERROR",
          "AAOS could not complete the request.",
        ),
        isError: true,
      });

      render(<CourseDetailView courseId={42} />);

      expect(
        screen.getByText("Academic data is temporarily unavailable."),
      ).toBeInTheDocument();
      expect(screen.queryByText(/private traceback|SQL|local path/i)).not.toBeInTheDocument();
    });

    it.each(["", "0", "-1", "not-an-integer"])(
      "rejects an unsafe route ID without a request: %s",
      (routeId) => {
        expect(parseCourseRouteId(routeId)).toBeNull();

        render(
          <CourseDetailView courseId={null} />,
        );

        expect(screen.getByText("The course link is invalid.")).toBeInTheDocument();
        expect(useQueryMock).not.toHaveBeenCalled();
      },
    );
  });
});
