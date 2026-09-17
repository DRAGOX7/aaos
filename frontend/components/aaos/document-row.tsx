import Link from "next/link";

import { DocumentReadiness } from "@/components/aaos/document-readiness";
import type { DocumentSummary } from "@/lib/api/academic";
import { documentHref } from "@/lib/documents/source-links";

type DocumentRowProps = Readonly<{
  document: DocumentSummary;
}>;

export function DocumentRow({ document }: DocumentRowProps) {
  return (
    <li className="rounded-lg border border-border bg-background p-4">
      <article>
        <h2 className="text-sm font-semibold text-foreground">
          <Link
            href={documentHref(document.id)}
            className="underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {document.title}
          </Link>
        </h2>
        <DocumentReadiness document={document} />
      </article>
    </li>
  );
}
