import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchAssignments,
  fetchCourse,
  fetchDocument,
  fetchDocuments,
  type AssignmentListResponse,
  type CourseSummary,
  type DocumentListResponse,
  type DocumentSummary,
} from "@/lib/api/academic";

afterEach(() => {
  vi.unstubAllGlobals();
});

const course: CourseSummary = {
  id: 42,
  title: "Operating Systems",
  code: "CS-42",
  description: null,
  updated_at: null,
};

const assignments: AssignmentListResponse = {
  assignments: [],
};

const documents: DocumentListResponse = {
  documents: [],
};

const document: DocumentSummary = {
  id: 7,
  course_id: 42,
  title: "Lecture notes",
  ingestion_status: "READY",
  extraction_status: "SUCCESS",
  page_count: 2,
  index_readiness: "READY",
  index_coverage: "FULL",
  updated_at: "2026-09-17T12:00:00Z",
};

function stubJsonResponse(payload: unknown): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockImplementation(() =>
    Promise.resolve(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("AAOS academic API clients", () => {
  it("fetches a course through the exact same-origin API path", async () => {
    const signal = new AbortController().signal;
    const fetchMock = stubJsonResponse(course);

    await expect(fetchCourse(42, signal)).resolves.toEqual(course);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/courses/42",
      {
        method: "GET",
        headers: { Accept: "application/json" },
        signal,
      },
    );
  });

  it("fetches filtered and unfiltered assignments without navigation URLs", async () => {
    const signal = new AbortController().signal;
    const fetchMock = stubJsonResponse(assignments);

    await fetchAssignments(undefined, signal);
    await fetchAssignments(42, signal);

    expect(fetchMock.mock.calls).toEqual([
      ["/api/v1/assignments", expect.objectContaining({ signal })],
      [
        "/api/v1/assignments?course_id=42",
        expect.objectContaining({ signal }),
      ],
    ]);
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      method: "GET",
      headers: { Accept: "application/json" },
    });
  });

  it("fetches filtered and unfiltered documents without navigation URLs", async () => {
    const signal = new AbortController().signal;
    const fetchMock = stubJsonResponse(documents);

    await fetchDocuments(undefined, signal);
    await fetchDocuments(42, signal);

    expect(fetchMock.mock.calls).toEqual([
      ["/api/v1/documents", expect.objectContaining({ signal })],
      [
        "/api/v1/documents?course_id=42",
        expect.objectContaining({ signal }),
      ],
    ]);
  });

  it("fetches one document through the exact same-origin API path", async () => {
    const signal = new AbortController().signal;
    const fetchMock = stubJsonResponse(document);

    await expect(fetchDocument(7, signal)).resolves.toEqual(document);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/documents/7",
      {
        method: "GET",
        headers: { Accept: "application/json" },
        signal,
      },
    );
  });

  it.each([
    ["course", async () => fetchCourse(0)],
    ["assignment filter", async () => fetchAssignments(-1)],
    ["document filter", async () => fetchDocuments(Number.NaN)],
    ["document", async () => fetchDocument(Number.POSITIVE_INFINITY)],
  ])("rejects an invalid %s without making a request", async (_label, request) => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(request()).rejects.toThrow(
      "AAOS academic request requires a positive integer ID.",
    );

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("normalizes non-2xx responses without reading or exposing the response body", async () => {
    const text = vi.fn().mockResolvedValue("private traceback");
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      text,
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchDocuments()).rejects.toThrow(
      "AAOS academic request failed (503)",
    );

    expect(text).not.toHaveBeenCalled();
  });

  it.each([
    [
      "course not found",
      404,
      "COURSE_NOT_FOUND",
      "The requested course is not available.",
      () => fetchCourse(42),
    ],
    [
      "document not found",
      404,
      "DOCUMENT_NOT_FOUND",
      "The requested document is not available.",
      () => fetchDocument(7),
    ],
    [
      "invalid request",
      422,
      "INVALID_REQUEST",
      "The request is invalid.",
      () => fetchDocuments(),
    ],
    [
      "internal failure",
      500,
      "INTERNAL_ERROR",
      "AAOS could not complete the request.",
      () => fetchAssignments(),
    ],
  ])(
    "retains only safe status/code information for %s",
    async (_label, status, code, message, request) => {
      const fetchMock = vi.fn().mockResolvedValue({
        ok: false,
        status,
        json: vi.fn().mockResolvedValue({
          error: {
            code,
            message: "private traceback, SQL, and local path",
          },
        }),
      });
      vi.stubGlobal("fetch", fetchMock);

      const error = await request().catch((value: unknown) => value);

      expect(error).toMatchObject({ status, code, message });
      expect(String(error)).not.toContain("private traceback");
      expect(String(error)).not.toContain("SQL");
      expect(String(error)).not.toContain("local path");
    },
  );

  it("does not add credentials or provider authority to requests", async () => {
    const fetchMock = stubJsonResponse(documents);

    await fetchDocuments();

    expect(fetchMock.mock.calls[0]?.[1]).toEqual({
      method: "GET",
      headers: { Accept: "application/json" },
      signal: undefined,
    });
  });
});
