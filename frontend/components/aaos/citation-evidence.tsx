"use client";

import { useEffect, useState } from "react";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import type { components } from "@/types/api.generated";
import { useI18n } from "@/lib/i18n";

type Citation = components["schemas"]["AcademicAskRagPageCitationView"] | components["schemas"]["AcademicAskRagStructuralCitationView"];
type Evidence = components["schemas"]["CitationEvidenceView"];

export function CitationEvidence({ citation, children }: { citation: Citation; children: React.ReactNode }) {
  const { t, dir } = useI18n();
  const [open, setOpen] = useState(false);
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const start = "page_start" in citation ? citation.page_start : citation.unit_start;
  const end = "page_end" in citation ? citation.page_end : citation.unit_end;
  const version = citation.document_version_id;

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    let active = true;
    fetch(`/api/v1/document-versions/${version}/evidence?start=${start}&end=${end}`, {
      signal: controller.signal, cache: "no-store",
    }).then(async (response) => {
      if (!response.ok) throw new Error("Evidence unavailable");
      const data: Evidence = await response.json();
      if (data.document_version_id !== version || data.start !== start || data.end !== end
        || !Array.isArray(data.excerpts) || !data.excerpts.every((text) => typeof text === "string")
        || typeof data.document_title !== "string" || typeof data.course_title !== "string"
        || data.location_kind !== ("page_start" in citation ? "PAGE" : "STRUCTURE")) {
        throw new Error("Invalid evidence response");
      }
      if (active) setEvidence(data);
    }).catch(() => { if (active) setFailed(true); });
    return () => { active = false; controller.abort(); };
  }, [open, version, start, end, attempt, citation]);

  return <Sheet open={open} onOpenChange={(value) => { setOpen(value); setEvidence(null); setFailed(false); }}>
    <SheetTrigger asChild>
      <button type="button" className="min-w-0 flex-1 cursor-pointer rounded-md text-start focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring hover:underline underline-offset-4">{children}</button>
    </SheetTrigger>
    <SheetContent side={dir === "rtl" ? "left" : "right"} className="!w-full sm:!max-w-lg overflow-y-auto motion-reduce:animate-none motion-reduce:transition-none">
      <SheetHeader className="pe-14 border-b border-border">
        <SheetTitle>{citation.document_title}</SheetTitle>
        <SheetDescription>{t("Citation evidence")}</SheetDescription>
      </SheetHeader>
      <div className="space-y-5 px-4 pb-6">
        <p className="text-sm text-muted-foreground">{t("Source context for claim {claim}", { claim: citation.claim_id })}</p>
        <p>{"page_start" in citation ? t("Pages {start}–{end}", { start, end }) : t("Units {start}–{end}", { start, end })}</p>
        <p className="text-sm text-muted-foreground">{t("Original text from the cited location. This context is not a separate claim verification result.")}</p>
        {!evidence && !failed ? <p role="status">{t("Loading evidence…")}</p> : null}
        {failed ? <div role="alert"><p>{t("Evidence is unavailable. The source may no longer be accessible.")}</p><Button variant="outline" className="mt-3" onClick={() => { setFailed(false); setAttempt((value) => value + 1); }}>{t("Retry")}</Button></div> : null}
        {evidence ? <>
          <p className="text-sm font-medium">{evidence.course_title}</p>
          {evidence.excerpts.length ? evidence.excerpts.map((text, index) => <blockquote key={index} dir="auto" className="whitespace-pre-wrap break-words border-s-2 border-primary/40 ps-4 text-sm leading-7">{text}</blockquote>) : <p>{t("No extracted text is available at this location.")}</p>}
          {evidence.truncated ? <p className="text-sm text-muted-foreground">{t("Excerpt shortened. Open the original for the full context.")}</p> : null}
          <a className="inline-flex min-h-11 items-center font-semibold text-primary-strong underline underline-offset-4" href={`/api/v1/document-versions/${version}/file${evidence.location_kind === "PAGE" ? `#page=${start}` : ""}`} target="_blank" rel="noopener noreferrer">{t("Open original document")}</a>
        </> : null}
      </div>
    </SheetContent>
  </Sheet>;
}
