"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { SectionCard } from "@/components/aaos/section-card";
import { Skeleton } from "@/components/ui/skeleton";
import { documentsQueryOptions } from "@/lib/query/academic";

function documentSummary(count: number): string {
  return `${count} document${count === 1 ? "" : "s"} available.`;
}

export function OverviewDocumentPanel() {
  const { data, isError, isPending } = useQuery(
    documentsQueryOptions(),
  );
  const documents = data?.documents;

  return (
    <SectionCard
      title="Documents"
      action={
        <Link
          href="/documents"
          className="text-sm font-medium text-primary underline-offset-4 transition-colors hover:underline"
        >
          View documents
        </Link>
      }
    >
      {isPending ? (
        <div aria-label="Loading documents" className="space-y-3">
          <Skeleton className="h-10 w-full rounded-lg" />
          <Skeleton className="h-10 w-3/4 rounded-lg" />
        </div>
      ) : isError ? (
        <p className="text-sm text-muted-foreground">
          Academic data is temporarily unavailable.
        </p>
      ) : !Array.isArray(documents) ? (
        <p className="text-sm text-muted-foreground">
          Academic data is temporarily unavailable.
        </p>
      ) : documents.length === 0 ? (
        <p className="text-sm text-muted-foreground">No documents found.</p>
      ) : (
        <p className="text-sm text-muted-foreground">
          {documentSummary(documents.length)}
        </p>
      )}
    </SectionCard>
  );
}
