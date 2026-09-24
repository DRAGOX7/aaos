import type { components } from "@/types/api.generated";

import { createApiError } from "@/lib/api/errors";

export type RagQueryRequest = components["schemas"]["RagQueryRequest"];
export type RagAnswerResponse = components["schemas"]["RagAnswerResponse"];
export type RagCitation = components["schemas"]["RagCitation"];
export type RagStructuralCitation = components["schemas"]["RagStructuralCitation"];
export type RagAnswerCitation = RagCitation | RagStructuralCitation;

export function isRagStructuralCitation(
  citation: RagAnswerCitation,
): citation is RagStructuralCitation {
  return "unit_start" in citation;
}

export function ragCitationKey(citation: RagAnswerCitation): string {
  return isRagStructuralCitation(citation)
    ? `${citation.document_id}-structure-${citation.unit_start}-${citation.unit_end}`
    : `${citation.document_id}-${citation.page_start}-${citation.page_end}`;
}

function requirePositiveInteger(value: number): void {
  if (!Number.isSafeInteger(value) || value <= 0) {
    throw new Error("AAOS RAG course scope requires a positive integer ID.");
  }
}

function requireQuestion(question: string): void {
  if (typeof question !== "string" || question.trim().length === 0 || question.length > 4096) {
    throw new Error("AAOS RAG question must be bounded and non-empty.");
  }
}

export async function fetchRagQuery(
  request: RagQueryRequest,
  signal?: AbortSignal,
): Promise<RagAnswerResponse> {
  requireQuestion(request.question);
  if (request.scope.type === "course") {
    requirePositiveInteger(request.scope.course_id);
  }
  const response = await fetch("/api/v1/rag/queries", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
    signal,
  });
  if (!response.ok) {
    throw await createApiError(response, "RAG");
  }
  return (await response.json()) as RagAnswerResponse;
}
