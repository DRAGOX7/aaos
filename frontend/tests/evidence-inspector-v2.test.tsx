import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { EvidenceWorkspace } from "@/components/aaos/citation-evidence";
import { RagCitationList } from "@/components/aaos/ask-result-cards";
import { isAcademicAskResponse } from "@/lib/api/ask";
import { setLocale } from "@/lib/i18n/locale";
import type { components } from "@/types/api.generated";

type Provenance = components["schemas"]["AcademicAskRagAnswerProvenanceView"];
const passage = "KNIME is an open-source platform with a graphical interface.";
const source = "Lecture header\n" + passage + "\nAdditional context.";
const provenance: Provenance = {
  answer_text: "KNIME provides graphical workflows.", rendered_text: "KNIME provides graphical workflows.",
  generation_id: 1, content_revision: 2, language: "en", status: "ANSWER", from_past_course: false,
  citations: [{ claim_id: "claim-1", document_version_id: 31, document_title: "Lecture_2.pdf", page_start: 2, page_end: 2,
    inspection: { document_version_id: 31, kind: "RETRIEVED_PASSAGE", claim_text: "KNIME provides graphical workflows.",
      passages: [{ location: 2, start: 15, end: 15 + passage.length, text: passage }] } },
    { claim_id: "claim-2", document_version_id: 42, document_title: "Arabic notes", unit_start: 4, unit_end: 4,
      inspection: { document_version_id: 42, kind: "RETRIEVED_PASSAGE", claim_text: "تعالج الخوارزمية البيانات باستخدام KNIME.",
        passages: [{ location: 4, start: 0, end: 48, text: "تستخدم الخوارزمية KNIME مع 3 مجموعات و x = 2." }] } }],
};
function evidence(version = 31, text = source) {
  return { document_version_id: version, document_title: version === 31 ? "Lecture_2.pdf" : "Arabic notes",
    course_title: "AI380", start: version === 31 ? 2 : 4, end: version === 31 ? 2 : 4,
    location_kind: version === 31 ? "PAGE" : "STRUCTURE", excerpts: [text], truncated: false };
}
function viewport(wide: boolean) {
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: wide, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
}
function workspace(value = provenance) {
  return <EvidenceWorkspace><p>Original answer stays here.</p><button>Answer action</button><RagCitationList provenance={value} /></EvidenceWorkspace>;
}
afterEach(() => { act(() => setLocale("en")); localStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("Evidence Inspector V2", () => {
  it("keeps one interactive answer beside a non-modal desktop pane and switches citations", async () => {
    viewport(true);
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(Response.json(evidence()))
      .mockResolvedValueOnce(Response.json(evidence(42, "تستخدم الخوارزمية KNIME مع 3 مجموعات و x = 2.")));
    render(workspace());
    const first = screen.getByRole("button", { name: /Lecture_2/ });
    fireEvent.click(first);
    const pane = await screen.findByRole("complementary", { name: "Evidence inspector" });
    expect(await within(pane).findByText(passage)).toBeVisible();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getAllByText("Original answer stays here.")).toHaveLength(1);
    screen.getByRole("button", { name: "Answer action" }).focus();
    expect(screen.getByRole("button", { name: "Answer action" })).toHaveFocus();
    expect(first).toHaveAttribute("aria-expanded", "true");
    const second = screen.getByRole("button", { name: /Arabic notes/ });
    fireEvent.click(second);
    expect(await within(pane).findByText("تستخدم الخوارزمية KNIME مع 3 مجموعات و x = 2.")).toBeVisible();
    expect(first).toHaveAttribute("aria-expanded", "false");
    expect(second).toHaveAttribute("data-selected", "true");
    expect(within(pane).queryByText(passage)).not.toBeInTheDocument();
    fireEvent.keyDown(pane, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("complementary")).not.toBeInTheDocument());
    await waitFor(() => expect(second).toHaveFocus());
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("shows the mapped claim and original highlighted passage before collapsed headers", async () => {
    viewport(true);
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json(evidence()));
    render(workspace());
    fireEvent.click(screen.getByRole("button", { name: /Lecture_2/ }));
    const mark = await screen.findByText(passage, { selector: "mark" });
    expect(mark).toHaveTextContent(passage);
    expect(screen.getByRole("region", { name: "Selected claim" })).toHaveTextContent(provenance.answer_text);
    expect(screen.queryByText(/Lecture header/)).not.toBeInTheDocument();
    const details = screen.getByText("Surrounding source context").closest("details")!;
    details.open = true;
    fireEvent(details, new Event("toggle"));
    expect(await screen.findByText(/Lecture header/)).toBeVisible();
    expect(screen.getByText(/Additional context/)).toBeVisible();
    expect(screen.getAllByText(passage, { selector: "mark" })).toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledTimes(1); // single-page context is already loaded
  });

  it("uses original auto direction independently of UI language and supports live toggling", async () => {
    viewport(true);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json(evidence()));
    act(() => setLocale("ar"));
    render(workspace());
    fireEvent.click(screen.getByRole("button", { name: /Lecture_2/ }));
    const mark = await screen.findByText(passage, { selector: "mark" });
    expect(mark.closest("p")).toHaveAttribute("dir", "auto");
    expect(mark.closest("p")).toHaveClass("text-start");
    expect(screen.getByRole("complementary", { name: "فاحص الأدلة" })).toBeVisible();
    act(() => setLocale("en"));
    expect(screen.getByRole("complementary", { name: "Evidence inspector" })).toBeVisible();
    expect(mark).toHaveTextContent(passage);
  });

  it("uses a full-width modal on mobile with a clear close path", async () => {
    viewport(false);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json(evidence()));
    render(workspace());
    const trigger = screen.getByRole("button", { name: /Lecture_2/ });
    fireEvent.click(trigger);
    const dialog = await screen.findByRole("dialog", { name: "Lecture_2.pdf" });
    expect(dialog).toHaveClass("!w-full");
    expect(await within(dialog).findByText(passage)).toBeVisible();
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it.each(["duplicate", "no-match", "wrong-version", "missing-metadata"])("keeps original context without misleading highlights: %s", async (mode) => {
    viewport(true);
    const value = structuredClone(provenance);
    if (mode === "wrong-version") value.citations[0].inspection!.document_version_id = 999;
    if (mode === "missing-metadata") delete value.citations[0].inspection;
    if (mode === "duplicate") { value.citations[0].inspection!.passages[0].start = 999; value.citations[0].inspection!.passages[0].end = 1000; }
    const text = mode === "duplicate" ? passage + "\n" + passage : "Different original source text.";
    vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json(evidence(31, text)));
    render(workspace(value));
    fireEvent.click(screen.getByRole("button", { name: /Lecture_2/ }));
    expect(await screen.findByText(/Exact highlighting is unavailable/)).toBeVisible();
    expect(document.querySelector("mark")).toBeNull();
    expect(screen.getByRole("link", { name: "Open original document" })).toHaveAttribute("href", "/api/v1/document-versions/31/file#page=2");
  });

  it("ignores malformed optional metadata without discarding a valid verified answer", () => {
    const payload = { status: "ANSWERED", answer_text: "Verified", sources: [], context_sources: [], step_count: 1,
      rag_provenance: { ...provenance, citations: [{ ...provenance.citations[0], inspection: { passages: "bad" } }] } };
    expect(isAcademicAskResponse(payload)).toBe(true);
  });
});
