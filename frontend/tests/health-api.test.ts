import { afterEach, expect, it, vi } from "vitest";

import {
  fetchSystemHealth,
  type SystemHealthResponse,
} from "@/lib/api/health";
import { systemHealthQueryOptions } from "@/lib/query/health";

afterEach(() => {
  vi.unstubAllGlobals();
});

const response: SystemHealthResponse = {
  schema_version: "phase7-health-v1",
  checked_at: "2026-09-18T00:00:00Z",
  overall: "HEALTHY",
  components: [],
};

it("fetches system health through a same-origin GET with abort propagation", async () => {
  const signal = new AbortController().signal;
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(response), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);

  await expect(fetchSystemHealth(signal)).resolves.toEqual(response);
  expect(fetchMock).toHaveBeenCalledWith("/api/v1/system-health", {
    method: "GET",
    headers: { Accept: "application/json" },
    signal,
  });
});

it("binds system health to a stable query key and does not expose write options", () => {
  expect(systemHealthQueryOptions().queryKey).toEqual(["system-health"]);
  expect(systemHealthQueryOptions().queryKey).not.toContain("POST");
});
