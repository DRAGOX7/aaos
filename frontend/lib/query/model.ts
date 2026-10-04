import { queryOptions } from "@tanstack/react-query";

import { fetchModelStatus, type ModelStatus } from "@/lib/api/model";
import { modelKeys } from "@/lib/query/keys";

export { modelKeys } from "@/lib/query/keys";

export function modelQueryOptions() {
  return queryOptions<ModelStatus>({
    queryKey: modelKeys.all,
    queryFn: ({ signal }) => fetchModelStatus(signal),
  });
}
