import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  fetchAcademicAsk,
  MalformedAskResponseError,
  type AcademicAskRequest,
} from "@/lib/api/ask";

function validResponse() {
  return {
    status: "ANSWERED",
    answer_text: "Grounded answer.",
    context_sources: [],
    conversation_id: "d5e2f53d-e8bf-4f0d-976d-fca7fd7911aa",
    sources: [],
    step_count: 1,
    request_id: "50317f6c-a8c6-4553-b2c9-1bb662e5fdfa",
    rag_provenance: {
      answer_text: "Grounded answer.",
      citations: [{
        claim_id: "claim-1",
        document_version_id: 12,
        document_title: "Algorithms",
        page_start: 2,
        page_end: 2,
      }],
      content_revision: 3,
      generation_id: 7,
      language: "en",
      rendered_text: "Grounded answer.",
      status: "ANSWER",
    },
  };
}

describe("Academic Ask browser API boundary", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("posts the typed question and backend conversation ID to the same-origin Ask route", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(validResponse()), { status: 200 }),
    );
    const request: AcademicAskRequest = {
      question: "Explain graph search",
      conversation_id: "d5e2f53d-e8bf-4f0d-976d-fca7fd7911aa",
      request_id: "50317f6c-a8c6-4553-b2c9-1bb662e5fdfa",
    };
    const signal = new AbortController().signal;

    await expect(fetchAcademicAsk(request, signal)).resolves.toMatchObject({
      status: "ANSWERED",
      answer_text: "Grounded answer.",
    });
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/ask", {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
      signal,
    });
  });

  it("rejects malformed nested provenance instead of rendering it as trusted", async () => {
    const malformed = validResponse();
    malformed.rag_provenance.citations[0] = {
      claim_id: "claim-1",
      document_version_id: 12,
      document_title: "Algorithms",
      page_start: 5,
      page_end: 2,
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(malformed), { status: 200 }),
    );

    await expect(fetchAcademicAsk({ question: "Question" })).rejects.toBeInstanceOf(
      MalformedAskResponseError,
    );
  });

  it("normalizes unavailable responses without exposing backend detail", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: "ASK_UNAVAILABLE",
            message: "provider payload and traceback",
            request_id: "safe-id",
          },
        }),
        { status: 503 },
      ),
    );

    await expect(fetchAcademicAsk({ question: "Question" })).rejects.toThrow(
      "AAOS ask request failed (503)",
    );
  });
});
