"use client";

import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";

import { DocumentReadiness } from "@/components/aaos/document-readiness";
import { SectionCard } from "@/components/aaos/section-card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api/errors";
import { useI18n } from "@/lib/i18n";
import { documentQueryOptions } from "@/lib/query/academic";
import { parseDocumentRouteId } from "@/lib/routing/route-ids";

type DocumentDetailViewProps = Readonly<{
  documentId: number | null;
}>;

function DocumentDetailQuery({ documentId }: { documentId: number }) {
  const { t } = useI18n();
  const { data, error, isError, isPending } = useQuery(
    documentQueryOptions(documentId),
  );

  if (isPending) {
    return (
      <SectionCard title={t("Document")}>
        <div aria-label={t("Loading document")}>
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
      <SectionCard title={t("Document")}>
        <p className="text-sm text-muted-foreground">
          {notFound
            ? t("Document not found.")
            : t("Academic data is temporarily unavailable.")}
        </p>
      </SectionCard>
    );
  }

  return (
    <SectionCard title={t("Document")}>
      <article aria-labelledby="document-detail-title">
        <h1
          id="document-detail-title"
          className="text-2xl font-semibold tracking-tight text-foreground"
        >
          {data.title}
        </h1>
        <DocumentReadiness document={data} />
        <DocumentFile documentId={documentId} title={data.title} />
      </article>
    </SectionCard>
  );
}

/**
 * The original file. AAOS stores PDF and Word files only: a PDF is shown
 * here, a Word file downloads (browsers cannot display it).
 */
function DocumentFile({ documentId, title }: { documentId: number; title: string }) {
  const { t } = useI18n();
  const fileUrl = `/api/v1/documents/${documentId}/file`;
  const word = /\.docx?$/i.test(title.trim());
  return (
    <div className="mt-6 flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <a
          href={fileUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
        >
          <ExternalLink aria-hidden="true" className="size-4" strokeWidth={2} />
          {word ? t("Download file") : t("Open file")}
        </a>
        {word ? (
          <p className="text-sm text-muted-foreground">{t("Word files open in Word after downloading.")}</p>
        ) : null}
      </div>
      {word ? null : (
        <iframe
          title={t("{title} preview", { title })}
          src={fileUrl}
          className="h-[75vh] min-h-[28rem] w-full rounded-xl border border-border bg-muted"
        />
      )}
    </div>
  );
}

export function DocumentDetailView({ documentId }: DocumentDetailViewProps) {
  const { t } = useI18n();
  if (documentId === null) {
    return (
      <SectionCard title={t("Document")}>
        <p className="text-sm text-muted-foreground">
          {t("The document link is invalid.")}
        </p>
      </SectionCard>
    );
  }

  return <DocumentDetailQuery documentId={documentId} />;
}
