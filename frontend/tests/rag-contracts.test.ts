import { describe, expect, it } from "vitest";

import { ApiError, createApiError } from "@/lib/api/errors";

describe("RAG browser error contract", () => {
  it("accepts only the sanitized RAG unavailable code", async () => {
    const response = new Response(
      JSON.stringify({
        error: {
          code: "RAG_UNAVAILABLE",
          message: "raw provider details must not escape",
          request_id: "safe-id",
        },
      }),
      { status: 503 },
    );

    const error = await createApiError(response, "RAG");

    expect(error).toBeInstanceOf(ApiError);
    expect(error.code).toBe("RAG_UNAVAILABLE");
    expect(error.message).toBe("AAOS could not answer from the local knowledge base.");
  });
});

