import { queryOptions } from "@tanstack/react-query";

import { fetchSystemHealth, type SystemHealthResponse } from "@/lib/api/health";
import { systemHealthKeys } from "@/lib/query/keys";

export { systemHealthKeys } from "@/lib/query/keys";

export function systemHealthQueryOptions() {
  return queryOptions<SystemHealthResponse>({
    queryKey: systemHealthKeys.all,
    queryFn: ({ signal }) => fetchSystemHealth(signal),
  });
}
