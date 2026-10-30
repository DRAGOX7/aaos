"use client";

import {
  createContext, useContext, useEffect, useId, useRef, useState, useSyncExternalStore,
  type ReactNode,
} from "react";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import type { components } from "@/types/api.generated";
import { useI18n } from "@/lib/i18n";
import { isCitationInspection } from "@/lib/api/ask";
import { alignEvidence, type Match } from "@/lib/ask/evidence-alignment";

type Citation = components["schemas"]["AcademicAskRagPageCitationView"] | components["schemas"]["AcademicAskRagStructuralCitationView"];
type Evidence = components["schemas"]["CitationEvidenceView"];
type Source = { location: number; data: Evidence };
type Selection = { citation: Citation; trigger: HTMLButtonElement; sequence: number };
type Inspector = {
  selection: Selection | null;
  panelId: string;
  select: (citation: Citation, trigger: HTMLButtonElement) => void;
};
const InspectorContext = createContext<Inspector | null>(null);
const WIDE_QUERY = "(min-width: 1200px)";
function subscribeViewport(notify: () => void) {
  const media = window.matchMedia?.(WIDE_QUERY);
  media?.addEventListener("change", notify);
  return () => media?.removeEventListener("change", notify);
}
function wideViewport() { return window.matchMedia?.(WIDE_QUERY).matches ?? false; }
function serverViewport() { return false; }

/** One shared selection keeps the existing answer interactive beside its source. */
export function EvidenceWorkspace({ children, header }: { children: ReactNode; header?: ReactNode }) {
  const { t, dir } = useI18n();
  const [selection, setSelection] = useState<Selection | null>(null);
  const sequence = useRef(0);
  const wide = useSyncExternalStore(subscribeViewport, wideViewport, serverViewport);
  const panelId = useId();
  function close() {
    const trigger = selection?.trigger;
    setSelection(null);
    requestAnimationFrame(() => { if (trigger?.isConnected) trigger.focus(); });
  }
  const content = selection ? <EvidenceContent key={selection.sequence} citation={selection.citation} modal={!wide} close={close} /> : null;
  return <InspectorContext.Provider value={{ selection, panelId, select: (citation, trigger) => {
    setSelection({ citation, trigger, sequence: ++sequence.current });
  } }}>
    <div onKeyDown={(event) => { if (selection && wide && event.key === "Escape") { event.stopPropagation(); close(); } }}>
    {header}
    <div className={selection && wide ? "grid min-w-0 items-start gap-6 grid-cols-[minmax(0,1.38fr)_minmax(22rem,1fr)]" : "min-w-0"}>
      <div className="min-w-0">{children}</div>
      {selection && wide ? <aside id={panelId} aria-label={t("Evidence inspector")} className="sticky top-20 max-h-[calc(100dvh-6rem)] min-w-0 overflow-y-auto rounded-[20px] border border-border bg-card">{content}</aside> : null}
    </div>
    </div>
    {!wide ? <Sheet open={selection !== null} onOpenChange={(value) => { if (!value) close(); }}>
      <SheetContent id={panelId} side={dir === "rtl" ? "left" : "right"} showCloseButton={false}
        className="!w-full !max-w-none overflow-y-auto motion-reduce:animate-none motion-reduce:transition-none"
        onCloseAutoFocus={(event) => event.preventDefault()}>
        {content}
      </SheetContent>
    </Sheet> : null}
  </InspectorContext.Provider>;
}

export function CitationEvidence({ citation, children }: { citation: Citation; children: ReactNode }) {
  const inspector = useContext(InspectorContext);
  if (!inspector) return <EvidenceWorkspace><CitationEvidence citation={citation}>{children}</CitationEvidence></EvidenceWorkspace>;
  const selected = inspector.selection?.citation === citation;
  return <button type="button" aria-expanded={selected} aria-controls={selected ? inspector.panelId : undefined}
    data-selected={selected || undefined}
    onClick={(event) => inspector.select(citation, event.currentTarget)}
    className="min-w-0 min-h-11 flex-1 cursor-pointer rounded-xl border border-border p-3 text-start underline-offset-4 hover:border-border-hover hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring data-selected:bg-primary-soft data-selected:ring-2 data-selected:ring-ring">
    {children}
  </button>;
}

async function fetchEvidence(version: number, start: number, end: number, kind: string, signal: AbortSignal): Promise<Evidence> {
  const response = await fetch(`/api/v1/document-versions/${version}/evidence?start=${start}&end=${end}`, { signal, cache: "no-store" });
  if (!response.ok) throw new Error("Evidence unavailable");
  const data: unknown = await response.json();
  if (!data || typeof data !== "object") throw new Error("Invalid evidence");
  const value = data as Evidence;
  if (value.document_version_id !== version || value.start !== start || value.end !== end
    || value.location_kind !== kind || typeof value.document_title !== "string"
    || typeof value.course_title !== "string" || typeof value.truncated !== "boolean"
    || !Array.isArray(value.excerpts) || value.excerpts.length > 20
    || !value.excerpts.every((text) => typeof text === "string")
    || value.excerpts.reduce((length, text) => length + Array.from(text).length, 0) > 16000) throw new Error("Invalid evidence");
  return value;
}

function EvidenceContent({ citation, modal, close }: { citation: Citation; modal: boolean; close: () => void }) {
  const { t } = useI18n();
  const [sources, setSources] = useState<Source[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const page = "page_start" in citation;
  const start = page ? citation.page_start : citation.unit_start;
  const end = page ? citation.page_end : citation.unit_end;
  const kind = page ? "PAGE" : "STRUCTURE";
  const version = citation.document_version_id;
  const inspection = isCitationInspection(citation.inspection, citation) ? citation.inspection : null;
  useEffect(() => { heading.current?.focus(); }, []);
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    const locations = inspection ? [...new Set(inspection.passages.map((span) => span.location))] : null;
    const load = locations ? Promise.all(locations.map(async (location) => ({
      location, data: await fetchEvidence(version, location, location, kind, controller.signal),
    }))) : fetchEvidence(version, start, end, kind, controller.signal).then((data) => [{ location: start, data }]);
    load.then((data) => { if (active) setSources(data); }).catch(() => { if (active) setFailed(true); });
    return () => { active = false; controller.abort(); };
  }, [citation, inspection, version, start, end, kind, attempt]);
  const matches = inspection?.passages.flatMap((passage) => {
    const source = sources?.find((item) => item.location === passage.location)?.data;
    const original = source?.excerpts[0];
    if (!original || !source) return [];
    const match = alignEvidence(original, passage.text, passage, !source.truncated);
    return match ? [{ original, match, location: passage.location }] : [];
  }) ?? [];
  const locationLabel = page
    ? start === end ? t("Page {page}", { page: start }) : t("Pages {start}–{end}", { start, end })
    : start === end ? t("Unit {unit}", { unit: start }) : t("Units {start}–{end}", { start, end });
  const title = <bdi dir="auto">{citation.document_title}</bdi>;
  const titleClass = "min-w-0 break-words text-base font-semibold text-foreground outline-none";
  return <>
    <div className="flex items-start justify-between gap-3 border-b border-border p-5 sm:p-6">
      <div className="min-w-0 space-y-2">
        {modal ? <SheetTitle ref={heading} tabIndex={-1} className={titleClass}>{title}</SheetTitle>
          : <h2 ref={heading} tabIndex={-1} className={titleClass}>{title}</h2>}
        {modal ? <SheetDescription>{t("Evidence inspector")}</SheetDescription> : <p className="text-sm text-muted-foreground">{t("Evidence inspector")}</p>}
        <p className="text-sm text-muted-foreground"><bdi>{locationLabel}</bdi>{" · "}<bdi>{t("Version {version}", { version })}</bdi></p>
        {sources?.[0]?.data.course_title ? <p dir="auto" className="text-start text-sm font-medium [unicode-bidi:plaintext]">{sources[0].data.course_title}</p> : null}
      </div>
      <Button variant="ghost" className="min-h-11 shrink-0" onClick={close}>{t("Close")}</Button>
    </div>
    <div className="space-y-6 p-5 sm:p-6">
      {inspection ? <section aria-label={t("Selected claim")}>
        <h3 className="mb-2 text-sm font-semibold">{t("Selected claim")}</h3>
        <p dir="auto" className="whitespace-pre-wrap break-words border-s-2 border-primary-strong ps-4 text-start text-base leading-7 [unicode-bidi:plaintext]">{inspection.claim_text}</p>
      </section> : null}
      {!sources && !failed ? <p role="status">{t("Loading evidence…")}</p> : null}
      {failed ? <div role="alert"><p>{t("Evidence is unavailable. The source may no longer be accessible.")}</p>
        <Button variant="outline" className="mt-3" onClick={() => { setSources(null); setFailed(false); setAttempt((value) => value + 1); }}>{t("Retry")}</Button></div> : null}
      {sources ? <>
        <section aria-label={matches.length ? t("Retrieved passage") : t("Source context")}>
          <h3 className="mb-2 text-sm font-semibold">{matches.length ? t("Retrieved passage") : t("Source context")}</h3>
          {matches.length ? <>
            <p className="mb-4 rounded-lg bg-muted px-3 py-2.5 text-[13px] leading-5 text-muted-foreground">{t("Highlight locates a cited retrieved passage, not a minimal verified claim span.")}</p>
            {matches.map(({ original, match, location }, index) => <div key={index} className="mb-4">
              <p className="mb-1 text-xs text-muted-foreground">{page ? t("Page {page}", { page: location }) : t("Unit {unit}", { unit: location })}</p>
              <p dir="auto" className="whitespace-pre-wrap break-words text-start text-base leading-7 [unicode-bidi:plaintext]">
                <mark className="rounded-sm bg-warning-soft px-0.5 text-foreground decoration-clone">{original.slice(match.start, match.end)}</mark>
              </p>
            </div>)}
          </> : sources.flatMap((source) => source.data.excerpts).map((text, index) => <p key={index} dir="auto" className="whitespace-pre-wrap break-words text-start text-sm leading-7 [unicode-bidi:plaintext]">{text}</p>)}
          {!matches.length || matches.length !== inspection?.passages.length ? <p className="mt-3 text-xs leading-5 text-muted-foreground">{t("Exact highlighting is unavailable for some source locations. The answer's verification status is unchanged.")}</p> : null}
          {sources.every((source) => source.data.excerpts.length === 0) ? <p>{t("No extracted text is available at this location.")}</p> : null}
          {sources.some((source) => source.data.truncated) ? <p className="mt-3 text-xs text-muted-foreground">{t("Excerpt shortened. Open the original for the full context.")}</p> : null}
        </section>
        {matches.length ? <details className="border-t border-border pt-3" onToggle={(event) => setExpanded(event.currentTarget.open)}>
          <summary className="min-h-11 cursor-pointer text-sm font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{t("Surrounding source context")}</summary>
          {expanded ? <SurroundingContext sources={sources} matches={matches} version={version} start={start} end={end} kind={kind} /> : null}
        </details> : null}
      </> : null}
      <a className="inline-flex min-h-11 items-center font-semibold text-primary-strong underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        href={`/api/v1/document-versions/${version}/file${page ? `#page=${start}` : ""}`} target="_blank" rel="noopener noreferrer">{t("Open original document")}</a>
    </div>
  </>;
}

type LocatedMatch = { original: string; match: Match; location: number };
function SurroundingContext({ sources, matches, version, start, end, kind }: {
  sources: Source[]; matches: LocatedMatch[]; version: number; start: number; end: number; kind: string;
}) {
  const { t } = useI18n();
  const [extra, setExtra] = useState<Evidence | null>(null);
  const [failed, setFailed] = useState(false);
  const needsRange = start !== end;
  useEffect(() => {
    if (!needsRange) return;
    const controller = new AbortController();
    let active = true;
    fetchEvidence(version, start, end, kind, controller.signal).then((data) => { if (active) setExtra(data); })
      .catch(() => { if (active) setFailed(true); });
    return () => { active = false; controller.abort(); };
  }, [needsRange, version, start, end, kind]);
  const originals = [...new Set(sources.flatMap((source) => source.data.excerpts))];
  const contexts = originals.flatMap((original) => {
    const ranges = matches.filter((item) => item.original === original).map((item) => item.match).sort((a, b) => a.start - b.start);
    const pieces: string[] = [];
    let cursor = 0;
    for (const range of ranges) {
      if (range.start > cursor) pieces.push(original.slice(cursor, range.start));
      cursor = Math.max(cursor, range.end);
    }
    if (cursor < original.length) pieces.push(original.slice(cursor));
    return pieces;
  });
  contexts.push(...(extra?.excerpts.filter((text) => !originals.includes(text)) ?? []));
  return <div className="space-y-3 pt-2">
    {contexts.map((text, index) => <p key={index} dir="auto" className="whitespace-pre-wrap break-words text-start text-sm leading-7 [unicode-bidi:plaintext]">{text}</p>)}
    {needsRange && !extra && !failed ? <p role="status">{t("Loading evidence…")}</p> : null}
    {failed ? <p role="alert">{t("Evidence is unavailable. The source may no longer be accessible.")}</p> : null}
    {extra?.truncated ? <p>{t("Excerpt shortened. Open the original for the full context.")}</p> : null}
  </div>;
}
