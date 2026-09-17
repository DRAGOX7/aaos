"use client";

import { useQuery } from "@tanstack/react-query";

import { DocumentReadiness } from "@/components/aaos/document-readiness";
import { SectionCard } from "@/components/aaos/section-card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api/errors";
import { documentQueryOptions } from "@/lib/query/academic";

type DocumentDetailViewProps = Readonly<{
  documentId: number | null;
}>;

export function parseDocumentRouteId(value: string): number | null {
  if (!/^[0-9]+$/.test(value)) {
    return null;
  }

  const documentId = Number(value);
  return Number.isSafeInteger(documentId) && documentId > 0
    ? documentId
    : null;
}

function DocumentDetailQuery({ documentId }: { documentId: number }) {
  const { data, error, isError, isPending } = useQuery(
    documentQueryOptions(documentId),
  );

  if (isPending) {
    return (
      <SectionCard title="Document">
        <div aria-label="Loading document">
          <Skeleton className="h-8 w-2/3" />
          <Skeleton className="mt-6 h-24 w-full" />
        </div>
      </SectionCard>
    );
  }

  if (isError || !data) {
    const notFound =
      error instanceof ApiError && error.code === "DOCUMENT_NOT_FOUND";

    return (
      <SectionCard title="Document">
        <p className="text-sm text-muted-foreground">
          {notFound
            ? "Document not found."
            : "Academic data is temporarily unavailable."}
        </p>
      </SectionCard>
    );
  }

  return (
    <SectionCard title="Document">
      <article aria-labelledby="document-detail-title">
        <h1
          id="document-detail-title"
          className="text-2xl font-semibold tracking-tight text-foreground"
        >
          {data.title}
        </h1>
        <DocumentReadiness document={data} />
      </article>
    </SectionCard>
  );
}

export function DocumentDetailView({ documentId }: DocumentDetailViewProps) {
  if (documentId === null) {
    return (
      <SectionCard title="Document">
        <p className="text-sm text-muted-foreground">
          The document link is invalid.
        </p>
      </SectionCard>
    );
  }

  return <DocumentDetailQuery documentId={documentId} />;
}
