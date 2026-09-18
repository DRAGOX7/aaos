import type { components } from "@/types/api.generated";

import { createApiError } from "@/lib/api/errors";

export type ActivityHistoryResponse =
  components["schemas"]["ActivityHistoryResponse"];

const MAX_CURSOR_LENGTH = 512;

function validateOptions(options: { before?: string; limit?: number }): void {
  if (options.before !== undefined &&
      (options.before.length === 0 || options.before.length > MAX_CURSOR_LENGTH)) {
    throw new Error("AAOS activity cursor is invalid.");
  }
  if (options.limit !== undefined &&
      (!Number.isSafeInteger(options.limit) || options.limit < 1 || options.limit > 200)) {
    throw new Error("AAOS activity limit is invalid.");
  }
}

export async function fetchActivity(options: {
  before?: string;
  limit?: number;
  signal?: AbortSignal;
} = {}): Promise<ActivityHistoryResponse> {
  validateOptions(options);
  const params = new URLSearchParams();
  if (options.before !== undefined) params.set("before", options.before);
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  const query = params.toString();
  const path = query.length > 0 ? `/api/v1/activity?${query}` : "/api/v1/activity";
  const response = await fetch(path, {
    method: "GET",
    headers: { Accept: "application/json" },
    signal: options.signal,
  });
  if (!response.ok) throw await createApiError(response, "activity");
  return (await response.json()) as ActivityHistoryResponse;
}
