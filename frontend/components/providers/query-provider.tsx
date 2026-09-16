"use client";

import {
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import {
  type ReactNode,
  useState,
} from "react";

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: 1,
        refetchOnWindowFocus: false,
      },
      mutations: {
        retry: false,
      },
    },
  });
}

type QueryProviderProps = Readonly<{
  children: ReactNode;
}>;

export function QueryProvider({
  children,
}: QueryProviderProps) {
  const [queryClient] = useState(
    () => createQueryClient(),
  );

  return (
    <QueryClientProvider client={queryClient}>
      {children}
    </QueryClientProvider>
  );
}
