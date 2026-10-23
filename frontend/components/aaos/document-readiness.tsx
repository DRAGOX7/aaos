"use client";

import type { DocumentSummary } from "@/lib/api/academic";
import { useI18n, type MessageKey, type Translator } from "@/lib/i18n";

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
  label: MessageKey,
  value: string | null,
  { t, status }: Translator,
): string {
  return `${t(label)}: ${value ? status(value, formatStatus) : t("Not available yet")}`;
}

export function DocumentReadiness({
  document,
}: DocumentReadinessProps) {
  const i18n = useI18n();
  const { t, status } = i18n;
  return (
    <dl className="mt-4 grid gap-2 text-sm text-muted-foreground sm:grid-cols-2">
      <div>
        <dt className="sr-only">{t("Ingestion status")}</dt>
        <dd>{`${t("Ingestion")}: ${status(document.ingestion_status, formatStatus)}`}</dd>
      </div>
      <div>
        <dt className="sr-only">{t("Extraction status")}</dt>
        <dd>{optionalStatusLabel("Extraction", document.extraction_status, i18n)}</dd>
      </div>
      <div>
        <dt className="sr-only">{t("Index readiness")}</dt>
        <dd>{optionalStatusLabel("Index readiness", document.index_readiness, i18n)}</dd>
      </div>
      <div>
        <dt className="sr-only">{t("Index coverage")}</dt>
        <dd>{optionalStatusLabel("Index coverage", document.index_coverage, i18n)}</dd>
      </div>
      {document.page_count !== null ? (
        <div>
          <dt className="sr-only">{t("Page count")}</dt>
          <dd>{`${t("Pages")}: ${document.page_count}`}</dd>
        </div>
      ) : null}
    </dl>
  );
}
