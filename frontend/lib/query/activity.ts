import { queryOptions } from "@tanstack/react-query";

import { fetchActivity } from "@/lib/api/activity";
import { activityKeys } from "@/lib/query/keys";

export { activityKeys } from "@/lib/query/keys";

export function activityHistoryQueryOptions(before?: string) {
  return queryOptions({
    queryKey: activityKeys.history(before),
    queryFn: ({ signal }) => fetchActivity({ before, signal }),
  });
}
