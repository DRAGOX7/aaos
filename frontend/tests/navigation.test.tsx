import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AppSidebar } from "@/components/aaos/app-sidebar";

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
  "Settings",
];

describe("AAOS navigation", () => {
  it("renders exactly the frozen Phase 7 navigation labels", () => {
    render(<AppSidebar />);

    const navigation = screen.getByRole("navigation", {
      name: "AAOS",
    });

    const links = within(navigation).getAllByRole("link");

    expect(
      links.map((link) => link.textContent?.trim()),
    ).toEqual(expectedLabels);

    expect(links).toHaveLength(10);
  });
});
