import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/aaos/app-shell";
import { PageHeader } from "@/components/aaos/page-header";

const { pathnameMock } = vi.hoisted(() => ({
  pathnameMock: vi.fn(() => "/"),
}));

vi.mock("next/navigation", () => ({
  usePathname: pathnameMock,
}));

function renderShell(children: ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <AppShell>{children}</AppShell>
    </QueryClientProvider>,
  );
}

describe("AAOS application shell", () => {
  it("provides the required application landmarks", () => {
    renderShell(<p>Workspace content</p>);

    expect(screen.getByRole("navigation", { name: "AAOS" })).toBeInTheDocument();
    expect(screen.getByRole("banner")).toBeInTheDocument();
    expect(screen.getByRole("main")).toBeInTheDocument();
  });

  it("leaves the page title to the page, so no page is mislabelled", () => {
    // Regression: the shell header read "Overview" on every page but Ask.
    renderShell(<p>Workspace content</p>);

    expect(screen.queryByRole("heading", { level: 1 })).not.toBeInTheDocument();
    expect(within(screen.getByRole("banner")).queryByText("Overview")).not.toBeInTheDocument();
  });

  it("renders exactly one workspace search, even with a page header inside", () => {
    // Regression: page headers repeated the search box and workspace badge.
    renderShell(<PageHeader title="Courses" description="Your courses." />);

    expect(
      screen.getAllByRole("searchbox", { name: "Global workspace search" }),
    ).toHaveLength(1);
    expect(screen.getAllByText("AAOS workspace")).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1, name: "Courses" })).toBeInTheDocument();
  });

  it("preserves PageHeader defaults when rendered directly", () => {
    render(<PageHeader />);

    expect(screen.getByRole("heading", { level: 1, name: "Overview" })).toBeInTheDocument();
    expect(screen.getByText("Your academic workspace")).toBeInTheDocument();
  });

  it("keeps child content inside the main workspace", () => {
    renderShell(<p>Workspace content</p>);

    expect(within(screen.getByRole("main")).getByText("Workspace content")).toBeInTheDocument();
  });
});
