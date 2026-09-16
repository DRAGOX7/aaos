import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchCourses,
  type CourseListResponse,
} from "@/lib/api/courses";
import {
  courseKeys,
  courseListQueryOptions,
} from "@/lib/query/courses";
import { createQueryClient } from "@/components/providers/query-provider";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AAOS courses API client", () => {
  it("requests courses through the same-origin API boundary", async () => {
    const payload: CourseListResponse = {
      courses: [
        {
          id: 42,
          title: "Operating Systems",
          code: "AI375",
          description: null,
          updated_at: null,
        },
      ],
    };

    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: {
          "Content-Type": "application/json",
        },
      }),
    );

    vi.stubGlobal("fetch", fetchMock);

    const controller = new AbortController();

    await expect(
      fetchCourses(controller.signal),
    ).resolves.toEqual(payload);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/courses",
      {
        method: "GET",
        headers: {
          Accept: "application/json",
        },
        signal: controller.signal,
      },
    );
  });

  it("uses a closed generic error without exposing response content", async () => {
    const text = vi.fn().mockResolvedValue(
      "sensitive backend diagnostic",
    );

    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      text,
    });

    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchCourses(),
    ).rejects.toThrow(
      "AAOS courses request failed (503)",
    );

    expect(text).not.toHaveBeenCalled();
  });
});

describe("AAOS course query foundation", () => {
  it("uses the frozen courses query key", () => {
    expect(courseKeys.all).toEqual(["courses"]);

    expect(
      courseListQueryOptions().queryKey,
    ).toEqual(courseKeys.all);
  });

  it("uses the approved TanStack Query defaults", () => {
    const queryClient = createQueryClient();
    const defaults = queryClient.getDefaultOptions();

    expect(defaults.queries?.retry).toBe(1);
    expect(defaults.queries?.refetchOnWindowFocus).toBe(false);
    expect(defaults.mutations?.retry).toBe(false);

    queryClient.clear();
  });
});
