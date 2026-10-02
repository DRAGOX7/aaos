import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import { OverviewAcademicSummary } from "@/components/aaos/overview-academic-summary";

const { pushMock, useQueryMock } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  useQueryMock: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

vi.mock("@tanstack/react-query", async () => {
  const actual =
    await vi.importActual<typeof import("@tanstack/react-query")>("@tanstack/react-query");
  return { ...actual, useQuery: useQueryMock };
});

type QueryState = Readonly<{ data?: unknown; isError?: boolean; isPending?: boolean }>;

const courses = {
  courses: [
    { id: 6, title: "AI380-OPTIMIZATION ALGORITHMS (1)", code: null, description: null,
      updated_at: null, status: "current" },
    { id: 7, title: "AI380-OPTIMIZATION ALGORITHMS (All Sections )", code: null,
      description: null, updated_at: null, status: "current" },
    { id: 42, title: "Operating Systems", code: "CS-42", description: null, updated_at: null,
      status: "current" },
    { id: 5, title: "SE440-PROJECT MANAGEMENT (All Sections )", code: null, description: null,
      updated_at: null, status: "past", archived_at: "2026-10-02T08:16:00Z" },
  ],
};
const documents = {
  documents: [
    { id: 1, course_id: 7, title: "Syllabus.pdf", ingestion_status: "READY",
      extraction_status: "SUCCESS", page_count: 4, index_readiness: "READY",
      index_coverage: "FULL", updated_at: "2026-09-17T12:00:00Z" },
  ],
};
const noAssignments = { assignments: [] };
const tasks = { tasks: [{ id: "t1", status: "PENDING_APPROVAL" }, { id: "t2", status: "REJECTED" }] };
const approvals = { approvals: [{ id: "a1", status: "PENDING" }, { id: "a2", status: "APPROVED" }] };
const health = {
  checked_at: "2026-09-17T12:00:00Z",
  components: [],
  overall: "HEALTHY",
  schema_version: "phase7-health-v1",
};

/** Answer each query by its key, so render order does not matter. */
function setQueries(states: Partial<Record<string, QueryState>>) {
  useQueryMock.mockImplementation((options: { queryKey: readonly unknown[] }) => {
    const key = options.queryKey.join("/");
    const state = states[key] ?? { isPending: true };
    return {
      data: state.data,
      error: state.isError ? new Error("private backend failure") : null,
      isError: state.isError ?? false,
      isPending: state.isPending ?? false,
    };
  });
}

const allSuccess = {
  courses: { data: courses },
  "documents/list": { data: documents },
  assignments: { data: noAssignments },
  "tasks/list": { data: tasks },
  "approvals/list": { data: approvals },
  "system-health": { data: health },
};

describe("AAOS overview (refresh direction A)", () => {
  beforeEach(() => {
    useQueryMock.mockReset();
    pushMock.mockReset();
    window.sessionStorage.clear();
  });

  it("puts a working Ask box first", () => {
    setQueries(allSuccess);
    render(<OverviewAcademicSummary />);

    expect(
      screen.getByRole("heading", { level: 1, name: "What are we studying today?" }),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Ask AAOS"), {
      target: { value: "What does the AI380 syllabus say about exams?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));

    expect(window.sessionStorage.getItem("aaos.ask.handoff")).toBe(
      "What does the AI380 syllabus say about exams?",
    );
    expect(pushMock).toHaveBeenCalledWith("/ask");
  });

  it("sends a suggested question the same way", () => {
    setQueries(allSuccess);
    render(<OverviewAcademicSummary />);

    fireEvent.click(screen.getByRole("button", { name: "What's due this week?" }));

    expect(window.sessionStorage.getItem("aaos.ask.handoff")).toBe("What's due this week?");
    expect(pushMock).toHaveBeenCalledWith("/ask");
  });

  it("does not navigate for an empty question", () => {
    setQueries(allSuccess);
    render(<OverviewAcademicSummary />);

    fireEvent.click(screen.getByRole("button", { name: "Ask" }));

    expect(pushMock).not.toHaveBeenCalled();
  });

  it("lists each current course once, readably, with its real file count", () => {
    setQueries(allSuccess);
    render(<OverviewAcademicSummary />);

    const list = screen.getByRole("list", { name: "Current courses" });
    const links = within(list).getAllByRole("link");
    expect(links.map((link) => link.getAttribute("href"))).toEqual(["/courses/7", "/courses/42"]);
    expect(within(list).getByText("Optimization Algorithms")).toBeInTheDocument();
    expect(within(list).getByText("AI380")).toBeInTheDocument();
    expect(within(list).getByText("1 file")).toBeInTheDocument();
    expect(within(list).getByText("No files posted yet")).toBeInTheDocument();
    expect(within(list).queryByText(/Project Management/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "All courses · 1 past" })).toHaveAttribute(
      "href",
      "/courses",
    );
  });

  it("shows a calm empty state when nothing is due", () => {
    setQueries(allSuccess);
    render(<OverviewAcademicSummary />);

    expect(screen.getByText("Nothing due right now")).toBeInTheDocument();
    expect(
      screen.getByText("Assignments appear here after your next Moodle sync."),
    ).toBeInTheDocument();
  });

  it("counts only pending approvals and non-terminal tasks", () => {
    setQueries(allSuccess);
    render(<OverviewAcademicSummary />);

    const approvalsRow = screen.getByRole("link", { name: /Pending approvals/ });
    const tasksRow = screen.getByRole("link", { name: /Active tasks/ });
    expect(within(approvalsRow).getByText("1")).toBeInTheDocument();
    expect(within(tasksRow).getByText("1")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /System health/ })).toHaveTextContent("Healthy");
  });

  it("keeps other panels when courses fail, without leaking the error", () => {
    setQueries({ ...allSuccess, courses: { isError: true } });
    render(<OverviewAcademicSummary />);

    expect(screen.getByText("Academic data is temporarily unavailable.")).toBeInTheDocument();
    expect(screen.getByText("Nothing due right now")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /System health/ })).toHaveTextContent("Healthy");
    expect(screen.queryByText("private backend failure")).not.toBeInTheDocument();
  });

  it("keeps courses when assignments fail", () => {
    setQueries({ ...allSuccess, assignments: { isError: true } });
    render(<OverviewAcademicSummary />);

    expect(screen.getByText("Optimization Algorithms")).toBeInTheDocument();
    expect(screen.getByText("Academic data is temporarily unavailable.")).toBeInTheDocument();
  });

  it("shows each panel's own loading state", () => {
    setQueries({ "system-health": { data: health } });
    render(<OverviewAcademicSummary />);

    expect(screen.getByLabelText("Loading courses")).toBeInTheDocument();
    expect(screen.getByLabelText("Loading assignments")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /System health/ })).toHaveTextContent("Healthy");
  });

  it("invents no analytics, schedule, or profile data", () => {
    setQueries(allSuccess);
    render(<OverviewAcademicSummary />);

    expect(
      screen.queryByText(/GPA|progress|streak|study hours|analytics|schedule|Abdullah/i),
    ).not.toBeInTheDocument();
  });
});
