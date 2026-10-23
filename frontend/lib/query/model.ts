import { queryOptions } from "@tanstack/react-query";

import {
  fetchChatGPTPlanModels,
  fetchGeminiModels,
  fetchModelStatus,
  type ModelStatus,
} from "@/lib/api/model";
import { modelKeys } from "@/lib/query/keys";

export { modelKeys } from "@/lib/query/keys";

export function modelQueryOptions() {
  return queryOptions<ModelStatus>({
    queryKey: modelKeys.all,
    queryFn: ({ signal }) => fetchModelStatus(signal),
  });
}

export function geminiModelsQueryOptions() {
  return queryOptions<string[]>({
    queryKey: modelKeys.geminiModels,
    queryFn: ({ signal }) => fetchGeminiModels(signal),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
}

export function chatgptPlanModelsQueryOptions() {
  return queryOptions<string[]>({
    queryKey: modelKeys.chatgptPlanModels,
    queryFn: ({ signal }) => fetchChatGPTPlanModels(signal),
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
}
