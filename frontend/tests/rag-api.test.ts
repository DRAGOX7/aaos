import { beforeEach, describe, expect, it, vi } from "vitest";

import { fetchRagQuery, type RagQueryRequest } from "@/lib/api/rag";

describe("RAG browser API client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("posts only the closed question and scope to the same-origin API", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          status: "INSUFFICIENT_EVIDENCE",
          answer_text: "Not enough evidence.",
          language: "en",
          citations: [],
        }),
        { status: 200 },
      ),
    );
    const request: RagQueryRequest = {
      question: "What is self-attention?",
      scope: { type: "course", course_id: 12 },
    };
    const signal = new AbortController().signal;

    await fetchRagQuery(request, signal);

    expect(fetchMock).toHaveBeenCalledWith("/api/v1/rag/queries", {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request),
      signal,
    });
  });

  it("normalizes the safe RAG operational error without exposing payload text", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: "RAG_UNAVAILABLE",
            message: "provider path and traceback",
            request_id: "safe-id",
          },
        }),
        { status: 503 },
      ),
    );

    await expect(
      fetchRagQuery({
        question: "Question",
        scope: { type: "global" },
      }),
    ).rejects.toMatchObject({
      code: "RAG_UNAVAILABLE",
      message: "AAOS could not answer from the local knowledge base.",
    });
  });

  it("rejects an invalid client-side course scope before network access", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");

    await expect(
      fetchRagQuery({
        question: "Question",
        scope: { type: "course", course_id: 0 },
      }),
    ).rejects.toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

