import type { DocumentSummary } from "@/lib/api/academic";

export type SearchTone = "success" | "warning" | "info" | "danger" | "neutral";

export type DocumentSearchState = Readonly<{
  label: string;
  tone: SearchTone;
}>;

/**
 * One student-facing answer to "can AAOS search this file?", from the four
 * pipeline fields. Failures win, so a broken file is never shown as usable;
 * the detail page keeps every field.
 */
export function documentSearchState(document: DocumentSummary): DocumentSearchState {
  if (document.ingestion_status === "FAILED" || document.index_readiness === "FAILED") {
    return { label: "Can't be searched", tone: "danger" };
  }
  if (document.extraction_status === "NO_TEXT" || document.index_readiness === "NOT_SEARCHABLE") {
    return { label: "No searchable text", tone: "warning" };
  }
  if (document.index_readiness === "PREPARING") {
    return { label: "Being indexed", tone: "info" };
  }
  if (document.index_readiness === "READY") {
    return document.index_coverage === "FULL"
      ? { label: "Searchable", tone: "success" }
      : { label: "Partly searchable", tone: "warning" };
  }
  return { label: "Not indexed yet", tone: "neutral" };
}
