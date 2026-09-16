import { queryOptions } from "@tanstack/react-query";

import { fetchCourses } from "@/lib/api/courses";
import { courseKeys } from "@/lib/query/keys";

export { courseKeys } from "@/lib/query/keys";

export function courseListQueryOptions() {
  return queryOptions({
    queryKey: courseKeys.all,
    queryFn: ({ signal }) => fetchCourses(signal),
  });
}
