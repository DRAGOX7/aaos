import { describe, expect, it, vi } from "vitest";

import { fetchActivity } from "@/lib/api/activity";
import { fetchSystemHealth } from "@/lib/api/health";

describe("activity and health browser transport", () => {
  it("uses same-origin GET requests only", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        schema_version: "phase7-activity-v1",
        events: [],
        next_cursor: null,
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        schema_version: "phase7-health-v1",
        checked_at: "2026-09-18T00:00:00Z",
        overall: "HEALTHY",
        components: [],
      }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await fetchActivity({ limit: 50 });
    await fetchSystemHealth();

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/v1/activity?limit=50",
      expect.objectContaining({ method: "GET" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/v1/system-health",
      expect.objectContaining({ method: "GET" }),
    );
    expect(JSON.stringify(fetchMock.mock.calls)).not.toMatch(/password|token|TrustedApproverContext|sqlite|provider/i);
    vi.unstubAllGlobals();
  });
});
