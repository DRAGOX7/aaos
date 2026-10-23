import Link from "next/link";
import { FileText } from "lucide-react";

import type { DocumentSummary } from "@/lib/api/academic";
import { documentSearchState, type SearchTone } from "@/lib/documents/search-state";
import { documentHref } from "@/lib/documents/source-links";
import { cn } from "@/lib/utils";

const TONES: Record<SearchTone, { pill: string; dot: string }> = {
  success: { pill: "bg-success-soft text-success-strong", dot: "bg-success" },
  warning: { pill: "bg-warning-soft text-warning-strong", dot: "bg-warning" },
  info: { pill: "bg-primary-soft text-primary-strong", dot: "bg-primary" },
  danger: { pill: "bg-danger-soft text-danger-strong", dot: "bg-destructive" },
  neutral: { pill: "bg-muted text-muted-foreground", dot: "bg-text-subtle" },
};

type DocumentRowProps = Readonly<{
  document: DocumentSummary;
}>;

export function DocumentRow({ document }: DocumentRowProps) {
  const state = documentSearchState(document);
  const tone = TONES[state.tone];
  return (
    <li className="py-1">
      <article className="flex items-center gap-3.5 rounded-xl px-2 py-2.5 transition-colors hover:bg-muted">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-primary-soft text-primary">
          <FileText aria-hidden="true" className="size-5" strokeWidth={2} />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-[15px] font-semibold text-foreground">
            <Link
              href={documentHref(document.id)}
              className="underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {document.title}
            </Link>
          </h2>
          {document.page_count !== null ? (
            <p className="mt-0.5 text-[13px] text-muted-foreground">
              {document.page_count} page{document.page_count === 1 ? "" : "s"}
            </p>
          ) : null}
        </div>
        <span
          className={cn(
            "inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold",
            tone.pill,
          )}
        >
          <span aria-hidden="true" className={cn("size-1.5 rounded-full", tone.dot)} />
          {state.label}
        </span>
      </article>
    </li>
  );
}
