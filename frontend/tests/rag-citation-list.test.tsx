import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { RagCitationList } from "@/components/aaos/ask-result-cards";
import type { components } from "@/types/api.generated";

type RagAnswer = components["schemas"]["AcademicAskRagAnswerProvenanceView"];

const provenance: RagAnswer = {
  answer_text: "Grounded answer",
  citations: [
    {
      claim_id: "claim-1",
      document_version_id: 202,
      document_title: "SE440 Syllabus.pdf",
      page_start: 3,
      page_end: 3,
    },
  ],
  content_revision: 1,
  generation_id: 2,
  language: "en",
  rendered_text: "Grounded answer",
  status: "ANSWER",
  from_past_course: false,
};

describe("RAG citation list", () => {
  it("labels sources from a past course", () => {
    render(<RagCitationList provenance={{ ...provenance, from_past_course: true }} />);

    expect(screen.getByText(/Page 3 · Version 202 · past course/)).toBeInTheDocument();
  });

  it("does not label sources from current courses", () => {
    render(<RagCitationList provenance={provenance} />);

    expect(screen.queryByText(/past course/)).not.toBeInTheDocument();
  });
});
