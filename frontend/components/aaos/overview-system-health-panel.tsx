"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { SectionCard } from "@/components/aaos/section-card";
import { Skeleton } from "@/components/ui/skeleton";
import { systemHealthQueryOptions } from "@/lib/query/health";

function statusLabel(status: string): string {
  return status.replaceAll("_", " ");
}

export function OverviewSystemHealthPanel() {
  const query = useQuery(systemHealthQueryOptions());

  return (
    <SectionCard
      title="System health"
      action={
        <Link
          href="/system-health"
          className="text-sm font-medium text-primary underline-offset-4 transition-colors hover:underline"
        >
          View details
        </Link>
      }
    >
      {query.isPending ? (
        <div aria-label="Loading overview system health" className="space-y-3">
          <Skeleton className="h-5 w-32 rounded-md" />
          <Skeleton className="h-4 w-3/4 rounded-md" />
        </div>
      ) : query.isError || !query.data ? (
        <p className="text-sm text-muted-foreground">
          System health is temporarily unavailable.
        </p>
      ) : (
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-2xl font-semibold tracking-tight text-primary-deep">
              {statusLabel(query.data.overall)}
            </p>
            <p className="mt-1 text-sm text-muted-foreground">
              Passive status from trusted AAOS checks.
            </p>
          </div>
          <span className="size-3 rounded-full bg-success" aria-hidden="true" />
        </div>
      )}
    </SectionCard>
  );
}
