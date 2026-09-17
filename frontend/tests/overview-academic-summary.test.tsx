import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { OverviewAcademicSummary } from "@/components/aaos/overview-academic-summary";
import { OverviewAssignmentPanel } from "@/components/aaos/overview-assignment-panel";
import { OverviewDocumentPanel } from "@/components/aaos/overview-document-panel";
import type {
  AssignmentListResponse,
  DocumentListResponse,
} from "@/lib/api/academic";
import type { CourseListResponse } from "@/lib/api/courses";

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

const courses: CourseListResponse = {
  courses: [
    {
      id: 42,
      title: "Operating Systems",
      code: "CS-42",
      description: null,
      updated_at: null,
    },
  ],
};

const assignments: AssignmentListResponse = {
  assignments: [
    {
      id: 1,
      course_id: 42,
      title: "Scheduler lab",
      description: null,
      due_at: null,
      updated_at: null,
    },
  ],
};

const documents: DocumentListResponse = {
  documents: [
    {
      id: 7,
      course_id: 42,
      title: "Lecture notes",
      ingestion_status: "READY",
      extraction_status: "SUCCESS",
      page_count: 4,
      index_readiness: "READY",
      index_coverage: "FULL",
      updated_at: "2026-09-17T12:00:00Z",
    },
  ],
};

type QueryState = Readonly<{
  data?: unknown;
  error?: unknown;
  isError?: boolean;
  isPending?: boolean;
}>;

function setQueries(...states: QueryState[]) {
  for (const state of states) {
    useQueryMock.mockReturnValueOnce({
      data: state.data,
      error: state.error,
      isError: state.isError ?? false,
      isPending: state.isPending ?? false,
    });
  }
}

const success = (data: unknown): QueryState => ({ data });

describe("AAOS academic overview", () => {
  beforeEach(() => {
    useQueryMock.mockReset();
  });

  it("renders independent courses, assignments, and documents panels", () => {
    setQueries(success(courses), success(assignments), success(documents));

    render(<OverviewAcademicSummary />);

    expect(screen.getByText("Operating Systems")).toBeInTheDocument();
    expect(screen.getByText("1 assignment available.")).toBeInTheDocument();
    expect(screen.getByText("1 document available.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View courses" })).toHaveAttribute(
      "href",
      "/courses",
    );
    expect(screen.getByRole("link", { name: "View assignments" })).toHaveAttribute(
      "href",
      "/assignments",
    );
    expect(screen.getByRole("link", { name: "View documents" })).toHaveAttribute(
      "href",
      "/documents",
    );
    expect(screen.queryByText(/GPA|progress|streak|study hours|schedule|analytics/i)).not.toBeInTheDocument();
  });

  it("keeps successful panels visible when assignments fail", () => {
    setQueries(
      success(courses),
      { error: new Error("private assignment failure"), isError: true },
      success(documents),
    );

    render(<OverviewAcademicSummary />);

    expect(screen.getByText("Operating Systems")).toBeInTheDocument();
    expect(screen.getByText("Academic data is temporarily unavailable.")).toBeInTheDocument();
    expect(screen.getByText("1 document available.")).toBeInTheDocument();
    expect(screen.queryByText("private assignment failure")).not.toBeInTheDocument();
  });

  it("keeps successful panels visible when courses or documents fail", () => {
    setQueries(
      { error: new Error("private course failure"), isError: true },
      success(assignments),
      { error: new Error("private document failure"), isError: true },
    );

    render(<OverviewAcademicSummary />);

    expect(screen.getAllByText("Academic data is temporarily unavailable.")).toHaveLength(2);
    expect(screen.getByText("1 assignment available.")).toBeInTheDocument();
    expect(screen.queryByText("private course failure")).not.toBeInTheDocument();
    expect(screen.queryByText("private document failure")).not.toBeInTheDocument();
  });

  it("keeps each panel's loading and empty states independent", () => {
    setQueries(
      { isPending: true },
      { data: { assignments: [] } },
      { data: { documents: [] } },
    );

    render(<OverviewAcademicSummary />);

    expect(screen.getByLabelText("Loading courses")).toBeInTheDocument();
    expect(screen.getByText("No assignments found.")).toBeInTheDocument();
    expect(screen.getByText("No documents found.")).toBeInTheDocument();
  });

  it("renders assignment and document empty/error states when used independently", () => {
    setQueries({ data: { assignments: [] } });
    render(<OverviewAssignmentPanel />);
    expect(screen.getByText("No assignments found.")).toBeInTheDocument();

    useQueryMock.mockReset();
    setQueries({ isPending: true });
    render(<OverviewDocumentPanel />);
    expect(screen.getByLabelText("Loading documents")).toBeInTheDocument();
  });
});
