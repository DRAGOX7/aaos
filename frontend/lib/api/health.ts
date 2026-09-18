import type { components } from "@/types/api.generated";

import { createApiError } from "@/lib/api/errors";

export type SystemHealthResponse =
  components["schemas"]["SystemHealthResponse"];

export async function fetchSystemHealth(
  signal?: AbortSignal,
): Promise<SystemHealthResponse> {
  const response = await fetch("/api/v1/system-health", {
    method: "GET",
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) throw await createApiError(response, "system health");
  return (await response.json()) as SystemHealthResponse;
}
