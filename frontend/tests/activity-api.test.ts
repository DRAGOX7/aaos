import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchActivity,
  type ActivityHistoryResponse,
} from "@/lib/api/activity";
import { activityHistoryQueryOptions, activityKeys } from "@/lib/query/activity";

afterEach(() => {
  vi.unstubAllGlobals();
});

const response: ActivityHistoryResponse = {
  schema_version: "phase7-activity-v1",
  events: [],
  next_cursor: null,
};

function stubJsonResponse(): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(response), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("AAOS Activity API client", () => {
  it("uses the same-origin GET path with bounded encoded query values", async () => {
    const signal = new AbortController().signal;
    const fetchMock = stubJsonResponse();

    await fetchActivity({ before: "cursor+/=", limit: 50, signal });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/activity?before=cursor%2B%2F%3D&limit=50",
      {
        method: "GET",
        headers: { Accept: "application/json" },
        signal,
      },
    );
  });

  it("omits absent filters and preserves query-key separation", async () => {
    const fetchMock = stubJsonResponse();

    await fetchActivity();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/activity",
      expect.objectContaining({ method: "GET" }),
    );
    expect(activityKeys.history()).toEqual(["activity", "history", undefined]);
    expect(activityKeys.history("cursor")).toEqual([
      "activity",
      "history",
      "cursor",
    ]);
    expect(activityKeys.history()).not.toEqual(activityKeys.history("cursor"));
  });

  it.each([
    { limit: 0 },
    { limit: 201 },
    { limit: Number.NaN },
    { before: "" },
    { before: "x".repeat(513) },
  ])("rejects unbounded activity options without a request", async (options) => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchActivity(options)).rejects.toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("normalizes non-2xx responses through the safe API error boundary", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      json: vi.fn().mockResolvedValue({
        error: { code: "INTERNAL_ERROR", message: "private SQL/path" },
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const error = await fetchActivity().catch((value: unknown) => value);

    expect(error).toMatchObject({ status: 503, code: "INTERNAL_ERROR" });
    expect(String(error)).not.toContain("private SQL/path");
  });

  it("binds the query factory to the opaque history key", () => {
    expect(activityHistoryQueryOptions("cursor").queryKey).toEqual(
      activityKeys.history("cursor"),
    );
  });
});
