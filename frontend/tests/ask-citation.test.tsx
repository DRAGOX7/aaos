import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { AskCitation } from "@/components/aaos/ask-citation";
import type { components } from "@/types/api.generated";

const structuralCitation: components["schemas"]["RagStructuralCitation"] = {
  document_id: 12,
  document_title: "Database Systems",
  unit_start: 3,
  unit_end: 5,
  heading: "Relational model",
  table_index: 0,
  row_index: 1,
  column_index: 2,
};

describe("AskCitation", () => {
  it("links only to the safe document route and displays bounded page data", () => {
    render(
      <AskCitation
        citation={{
          document_id: 7,
          document_title: "Operating Systems",
          page_start: 2,
          page_end: 4,
        }}
      />,
    );

    const link = screen.getByRole("link", { name: /Operating Systems/ });
    expect(link).toHaveAttribute("href", "/documents/7");
    expect(screen.getByText(/pages 2–4/)).toBeInTheDocument();
    expect(screen.queryByText(/version|source|path|sql/i)).not.toBeInTheDocument();
  });

  it("renders structural coordinates as human-facing units without page navigation", () => {
    render(<AskCitation citation={structuralCitation} />);

    const link = screen.getByRole("link", { name: "Database Systems" });
    expect(link).toHaveAttribute("href", "/documents/12");
    expect(link).not.toHaveAttribute("href", expect.stringContaining("page"));
    expect(screen.getByText(/units 3–5/)).toBeInTheDocument();
    expect(screen.getByText(/heading Relational model/)).toBeInTheDocument();
    expect(screen.getByText(/table 1, row 2, column 3/)).toBeInTheDocument();
    expect(screen.queryByText(/page/i)).not.toBeInTheDocument();
  });

  it("uses singular unit wording when the structural span has one unit", () => {
    render(
      <AskCitation
        citation={{
          document_id: 13,
          document_title: "Algorithms",
          unit_start: 4,
          unit_end: 4,
        }}
      />,
    );

    expect(screen.getByText(/unit 4/)).toBeInTheDocument();
    expect(screen.queryByText(/page/i)).not.toBeInTheDocument();
  });
});

