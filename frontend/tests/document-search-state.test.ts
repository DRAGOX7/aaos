import { describe, expect, it } from "vitest";

import type { DocumentSummary } from "@/lib/api/academic";
import { documentSearchState } from "@/lib/documents/search-state";

function doc(overrides: Partial<DocumentSummary>): DocumentSummary {
  return {
    id: 1,
    course_id: 2,
    title: "Notes.pdf",
    ingestion_status: "READY",
    extraction_status: "SUCCESS",
    page_count: 10,
    index_readiness: "READY",
    index_coverage: "FULL",
    updated_at: null,
    ...overrides,
  } as DocumentSummary;
}

describe("documentSearchState", () => {
  it.each([
    [{}, "Searchable", "success"],
    [{ index_coverage: "PARTIAL" }, "Partly searchable", "warning"],
    [{ index_readiness: "PREPARING", ingestion_status: "NEVER_ATTEMPTED" }, "Being indexed", "info"],
    [{ extraction_status: "NO_TEXT", index_readiness: "NOT_SEARCHABLE" }, "No searchable text", "warning"],
    [{ ingestion_status: "FAILED", extraction_status: "NO_TEXT" }, "Can't be searched", "danger"],
    [{ index_readiness: "FAILED" }, "Can't be searched", "danger"],
    [{ index_readiness: null, index_coverage: null, extraction_status: null }, "Not indexed yet", "neutral"],
  ] as const)("reads %j as %s", (overrides, label, tone) => {
    expect(documentSearchState(doc(overrides as Partial<DocumentSummary>))).toEqual({ label, tone });
  });

  it("never reports a failed file as searchable", () => {
    expect(documentSearchState(doc({ ingestion_status: "FAILED" })).tone).toBe("danger");
  });
});
