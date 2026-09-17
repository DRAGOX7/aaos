import { describe, expect, it } from "vitest";

import { ragKeys } from "@/lib/query/rag";

describe("RAG query keys", () => {
  it("are stable and isolated from academic resources", () => {
    expect(ragKeys.query({ question: "Question", scope: { type: "global" } })).toEqual([
      "rag",
      "query",
      "global",
      "Question",
    ]);
    expect(ragKeys.query({ question: "Question", scope: { type: "course", course_id: 2 } })).toEqual([
      "rag",
      "query",
      "course",
      2,
      "Question",
    ]);
    expect(ragKeys.query({ question: "Question", scope: { type: "global" }})).not.toEqual([
      "courses",
    ]);
  });
});

