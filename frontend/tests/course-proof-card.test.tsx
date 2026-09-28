import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import HomePage from "@/app/page";
import { AppShell } from "@/components/aaos/app-shell";
import { CourseProofCard } from "@/components/aaos/course-proof-card";
import type { CourseListResponse } from "@/lib/api/courses";

const { useMutationMock, useQueryMock, pathnameMock } = vi.hoisted(() => ({
  useMutationMock: vi.fn(),
  useQueryMock: vi.fn(),
  pathnameMock: vi.fn(() => "/"),
}));

vi.mock("@tanstack/react-query", async () => {
  const actual =
    await vi.importActual<typeof import("@tanstack/react-query")>(
      "@tanstack/react-query",
    );

  return {
    ...actual,
    useMutation: useMutationMock,
    useQuery: useQueryMock,
  };
});

vi.mock("next/navigation", () => ({
  usePathname: pathnameMock,
}));

function setQueryState({
  data,
  isError = false,
  isPending = false,
}: {
  data?: CourseListResponse;
  isError?: boolean;
  isPending?: boolean;
}) {
  useQueryMock.mockReturnValue({
    data,
    isError,
    isPending,
  });
}

describe("AAOS course proof card", () => {
  beforeEach(() => {
    useMutationMock.mockReset();
    useMutationMock.mockReturnValue({ mutateAsync: vi.fn(), isPending: false });
    useQueryMock.mockReset();
  });

  it("shows an accessible loading state with skeleton rows", () => {
    setQueryState({
      isPending: true,
    });

    render(<CourseProofCard />);

    const loading = screen.getByLabelText("Loading courses");

    expect(loading).toBeInTheDocument();

    expect(
      loading.querySelectorAll('[data-slot="skeleton"]'),
    ).toHaveLength(3);
  });

  it("shows the frozen empty-state copy", () => {
    setQueryState({
      data: {
        courses: [],
      },
    });

    render(<CourseProofCard />);

    expect(
      screen.getByText("No active courses found."),
    ).toBeInTheDocument();

    expect(
      screen.getByRole("link", {
        name: "View courses",
      }),
    ).toHaveAttribute("href", "/courses");
  });

  it("shows at most the first three real course titles and codes", () => {
    setQueryState({
      data: {
        courses: [
          {
            id: 1,
            title: "Operating Systems",
            code: "AI375",
            description: null,
            updated_at: null,
          },
          {
            id: 2,
            title: "Big Data",
            code: "AI328",
            description: null,
            updated_at: null,
          },
          {
            id: 3,
            title: "Deep Learning",
            code: "AI342",
            description: null,
            updated_at: null,
          },
          {
            id: 4,
            title: "Hidden Fourth Course",
            code: "CS999",
            description: null,
            updated_at: null,
          },
        ],
      },
    });

    render(<CourseProofCard />);

    expect(
      screen.getByText("Operating Systems"),
    ).toBeInTheDocument();
    expect(screen.getByText("AI375")).toBeInTheDocument();

    expect(screen.getByText("Big Data")).toBeInTheDocument();
    expect(screen.getByText("AI328")).toBeInTheDocument();

    expect(
      screen.getByText("Deep Learning"),
    ).toBeInTheDocument();
    expect(screen.getByText("AI342")).toBeInTheDocument();

    expect(
      screen.queryByText("Hidden Fourth Course"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("CS999")).not.toBeInTheDocument();
  });

  it("keeps a course request error local to the course surface", () => {
    setQueryState({
      isError: true,
    });

    render(
      <AppShell>
        <CourseProofCard />
      </AppShell>,
    );

    expect(
      screen.getByText(
        "Academic data is temporarily unavailable.",
      ),
    ).toBeInTheDocument();

    expect(
      screen.getByRole("heading", {
        level: 1,
        name: "Overview",
      }),
    ).toBeInTheDocument();
  });

  it("composes the real course surface on Overview", () => {
    useQueryMock.mockImplementation((options: { queryKey?: readonly unknown[] }) => {
      switch (options.queryKey?.[0]) {
        case "courses":
          return { data: { courses: [] }, isError: false, isPending: false };
        case "assignments":
          return { data: { assignments: [] }, isError: false, isPending: false };
        case "tasks":
          return { data: { tasks: [] }, isError: false, isPending: false };
        case "approvals":
          return { data: { approvals: [] }, isError: false, isPending: false };
        case "system-health":
          return {
            data: {
              checked_at: "2026-09-17T12:00:00Z",
              components: [],
              overall: "HEALTHY",
              schema_version: "phase7-health-v1",
            },
            isError: false,
            isPending: false,
          };
        default:
          return { data: undefined, isError: true, isPending: false };
      }
    });

    render(<HomePage />);

    expect(
      screen.getByText("No active courses found."),
    ).toBeInTheDocument();
  });
});
