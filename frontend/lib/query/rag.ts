import { fetchRagQuery, type RagQueryRequest } from "@/lib/api/rag";

export const ragKeys = {
  all: ["rag"] as const,
  query: (request: RagQueryRequest) =>
    request.scope.type === "global"
      ? (["rag", "query", "global", request.question] as const)
      : (["rag", "query", "course", request.scope.course_id, request.question] as const),
};

export function ragQueryOptions(request: RagQueryRequest) {
  return {
    queryKey: ragKeys.query(request),
    queryFn: ({ signal }: { signal: AbortSignal }) => fetchRagQuery(request, signal),
  };
}

