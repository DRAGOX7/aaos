import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { OverviewAskPanel } from "@/components/aaos/overview-ask-panel";

describe("overview Ask AAOS launcher", () => {
  it("routes users to the single canonical Ask experience", () => {
    render(<OverviewAskPanel />);

    expect(screen.getByRole("heading", { name: "Ask AAOS" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open full Ask AAOS" })).toHaveAttribute(
      "href",
      "/ask",
    );
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Ask AAOS" })).not.toBeInTheDocument();
  });
});
