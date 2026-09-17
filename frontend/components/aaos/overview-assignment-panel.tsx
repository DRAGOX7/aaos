"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { SectionCard } from "@/components/aaos/section-card";
import { Skeleton } from "@/components/ui/skeleton";
import { assignmentsQueryOptions } from "@/lib/query/academic";

function assignmentSummary(count: number): string {
  return `${count} assignment${count === 1 ? "" : "s"} available.`;
}

export function OverviewAssignmentPanel() {
  const { data, isError, isPending } = useQuery(
    assignmentsQueryOptions(),
  );
  const assignments = data?.assignments;

  return (
    <SectionCard
      title="Assignments"
      action={
        <Link
          href="/assignments"
          className="text-sm font-medium text-primary underline-offset-4 transition-colors hover:underline"
        >
          View assignments
        </Link>
      }
    >
      {isPending ? (
        <div aria-label="Loading assignments" className="space-y-3">
          <Skeleton className="h-10 w-full rounded-lg" />
          <Skeleton className="h-10 w-3/4 rounded-lg" />
        </div>
      ) : isError ? (
        <p className="text-sm text-muted-foreground">
          Academic data is temporarily unavailable.
        </p>
      ) : !Array.isArray(assignments) ? (
        <p className="text-sm text-muted-foreground">
          Academic data is temporarily unavailable.
        </p>
      ) : assignments.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No assignments found.
        </p>
      ) : (
        <p className="text-sm text-muted-foreground">
          {assignmentSummary(assignments.length)}
        </p>
      )}
    </SectionCard>
  );
}
