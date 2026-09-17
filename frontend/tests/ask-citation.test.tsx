import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { AskCitation } from "@/components/aaos/ask-citation";

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
});

