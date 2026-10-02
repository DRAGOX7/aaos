"use client";

import { FileText } from "lucide-react";
import { useQuery } from "@tanstack/react-query";

import { DocumentRow } from "@/components/aaos/document-row";
import { EmptyState } from "@/components/aaos/empty-state";
import { PageHeader } from "@/components/aaos/page-header";
import { SectionCard } from "@/components/aaos/section-card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api/errors";
import { documentsQueryOptions } from "@/lib/query/academic";

type DocumentListProps = Readonly<{
  courseId?: number;
}>;

export function DocumentList({ courseId }: DocumentListProps) {
  const query = useQuery(documentsQueryOptions(courseId));
  const courseNotFound =
    query.error instanceof ApiError && query.error.code === "COURSE_NOT_FOUND";

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6">
      <PageHeader
        title="Documents"
        description="Read-only document readiness from your academic workspace."
      />

      <SectionCard title="Documents">
        {query.isPending ? (
          <div aria-label="Loading documents" className="space-y-3">
            {Array.from({ length: 3 }, (_, index) => (
              <Skeleton key={index} className="h-32 w-full rounded-lg" />
            ))}
          </div>
        ) : courseNotFound ? (
          <p className="text-sm text-muted-foreground">Course not found.</p>
        ) : query.isError ? (
          <p className="text-sm text-muted-foreground">
            Academic data is temporarily unavailable.
          </p>
        ) : query.data?.documents.length === 0 ? (
          <EmptyState
            icon={FileText}
            title="No documents found."
            description="Files from your current courses appear here once they are synced from Moodle and indexed. Past course files are on each past course's page."
            action={{ href: "/courses", label: "Browse courses" }}
          />
        ) : (
          <ul aria-label="Documents" className="divide-y divide-border">
            {query.data?.documents.map((document) => (
              <DocumentRow key={document.id} document={document} />
            ))}
          </ul>
        )}
      </SectionCard>
    </div>
  );
}
