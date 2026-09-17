import type { DocumentSummary } from "@/lib/api/academic";

type DocumentReadinessProps = Readonly<{
  document: DocumentSummary;
}>;

function formatStatus(value: string): string {
  return value
    .replaceAll("_", " ")
    .toLowerCase()
    .replace(/(^| )\w/g, (character) => character.toUpperCase());
}

function optionalStatusLabel(
  label: string,
  value: string | null,
): string {
  return `${label}: ${value ? formatStatus(value) : "Not available yet"}`;
}

export function DocumentReadiness({
  document,
}: DocumentReadinessProps) {
  return (
    <dl className="mt-4 grid gap-2 text-sm text-muted-foreground sm:grid-cols-2">
      <div>
        <dt className="sr-only">Ingestion status</dt>
        <dd>Ingestion: {formatStatus(document.ingestion_status)}</dd>
      </div>
      <div>
        <dt className="sr-only">Extraction status</dt>
        <dd>{optionalStatusLabel("Extraction", document.extraction_status)}</dd>
      </div>
      <div>
        <dt className="sr-only">Index readiness</dt>
        <dd>{optionalStatusLabel("Index readiness", document.index_readiness)}</dd>
      </div>
      <div>
        <dt className="sr-only">Index coverage</dt>
        <dd>{optionalStatusLabel("Index coverage", document.index_coverage)}</dd>
      </div>
      {document.page_count !== null ? (
        <div>
          <dt className="sr-only">Page count</dt>
          <dd>Pages: {document.page_count}</dd>
        </div>
      ) : null}
    </dl>
  );
}
