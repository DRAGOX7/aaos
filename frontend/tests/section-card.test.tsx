import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SectionCard } from "@/components/aaos/section-card";

describe("AAOS SectionCard", () => {
  it("uses the approved AAOS card radius and shadow tokens", () => {
    render(
      <SectionCard title="Courses">
        <p>Course content</p>
      </SectionCard>,
    );

    const card = screen
      .getByRole("heading", { name: "Courses" })
      .closest("section");

    if (!card) {
      throw new Error("SectionCard did not render a section element");
    }

    expect(card).toHaveClass("rounded-[var(--aaos-radius-card)]");
    expect(card).toHaveClass("shadow-[var(--aaos-shadow-card)]");
    expect(card).not.toHaveClass("rounded-[var(--aaos-card-radius)]");
    expect(card).not.toHaveClass("shadow-[var(--aaos-card-shadow)]");
  });
});
