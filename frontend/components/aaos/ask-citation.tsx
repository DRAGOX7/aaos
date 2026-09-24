import Link from "next/link";

import { isRagStructuralCitation, type RagAnswerCitation } from "@/lib/api/rag";
import { documentHref } from "@/lib/routing/document-links";

type AskCitationProps = Readonly<{
  citation: RagAnswerCitation;
}>;

export function AskCitation({ citation }: AskCitationProps) {
  const structural = isRagStructuralCitation(citation);
  let tableLocation: string | null = null;
  if (
    structural &&
    citation.table_index != null &&
    citation.row_index != null &&
    citation.column_index != null
  ) {
    tableLocation = ` · table ${citation.table_index + 1}, row ${citation.row_index + 1}, column ${citation.column_index + 1}`;
  }

  return (
    <li className="text-sm text-muted-foreground">
      <Link
        href={documentHref(citation.document_id)}
        className="font-medium text-foreground underline-offset-4 hover:underline"
      >
        {citation.document_title}
      </Link>
      {structural ? (
        <>
          <span>
            {citation.unit_start === citation.unit_end
              ? ` · unit ${citation.unit_start}`
              : ` · units ${citation.unit_start}–${citation.unit_end}`}
          </span>
          {citation.heading ? <span> · heading {citation.heading}</span> : null}
          {tableLocation ? <span>{tableLocation}</span> : null}
        </>
      ) : (
        <span> · pages {citation.page_start}–{citation.page_end}</span>
      )}
    </li>
  );
}

