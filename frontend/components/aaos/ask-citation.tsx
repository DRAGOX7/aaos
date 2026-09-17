import Link from "next/link";

import type { RagCitation } from "@/lib/api/rag";
import { documentHref } from "@/lib/routing/document-links";

type AskCitationProps = Readonly<{
  citation: RagCitation;
}>;

export function AskCitation({ citation }: AskCitationProps) {
  return (
    <li className="text-sm text-muted-foreground">
      <Link
        href={documentHref(citation.document_id)}
        className="font-medium text-foreground underline-offset-4 hover:underline"
      >
        {citation.document_title}
      </Link>
      <span> · pages {citation.page_start}–{citation.page_end}</span>
    </li>
  );
}

