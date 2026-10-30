import { fireEvent, render, screen, within } from "@testing-library/react";
import { MobileNav } from "@/components/aaos/mobile-nav";
import { describe, expect, it, vi } from "vitest";

import { AppSidebar, resolveActiveNavigationHref } from "@/components/aaos/app-sidebar";

const { pathnameMock } = vi.hoisted(() => ({
  pathnameMock: vi.fn(() => "/"),
}));

vi.mock("next/navigation", () => ({
  usePathname: pathnameMock,
}));

const expectedLabels = [
  "Overview",
  "Courses",
  "Assignments",
  "Documents",
  "Ask AAOS",
  "Tasks",
  "Approvals",
  "Activity",
  "System Health",
];

const expectedHrefs = [
  "/",
  "/courses",
  "/assignments",
  "/documents",
  "/ask",
  "/tasks",
  "/approvals",
  "/activity",
  "/system-health",
];

describe("AAOS navigation", () => {
  it("marks the actual nested mobile route, not Overview", () => {
    pathnameMock.mockReturnValue("/courses/42");
    render(<MobileNav />);
    fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));
    const nav = screen.getByRole("navigation", { name: "AAOS mobile" });
    expect(within(nav).getByRole("link", { name: "Courses" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
  });
  it("renders exactly the frozen Phase 7 navigation labels", () => {
    render(<AppSidebar />);

    const navigation = screen.getByRole("navigation", {
      name: "AAOS",
    });

    const links = within(navigation).getAllByRole("link");

    expect(
      links.map((link) => link.textContent?.trim()),
    ).toEqual(expectedLabels);

    expect(links.map((link) => link.getAttribute("href"))).toEqual(
      expectedHrefs,
    );

    expect(links).toHaveLength(9);
  });

  it("highlights the page reached by client-side navigation", () => {
    // Regression: the sidebar listened only to popstate, so after router.push
    // to /ask it still marked Overview as the current page.
    pathnameMock.mockReturnValue("/ask");
    render(<AppSidebar />);

    const navigation = screen.getByRole("navigation", { name: "AAOS" });
    expect(within(navigation).getByRole("link", { name: "Ask AAOS" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(navigation).getByRole("link", { name: "Overview" })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it.each([
    ["/", "/"],
    ["/courses", "/courses"],
    ["/courses/42", "/courses"],
    ["/system-health", "/system-health"],
  ])("selects the correct navigation item for %s", (pathname, expected) => {
    expect(resolveActiveNavigationHref(pathname)).toBe(expected);
  });
});
