import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RagCitationList } from "@/components/aaos/ask-result-cards";
import { AskView } from "@/components/aaos/ask-view";
import type { components } from "@/types/api.generated";

type Provenance = components["schemas"]["AcademicAskRagAnswerProvenanceView"];
const provenance: Provenance = {
  answer_text: "Grounded answer", rendered_text: "Grounded answer", status: "ANSWER",
  language: "en", generation_id: 1, content_revision: 1, from_past_course: false,
  citations: [
    { claim_id: "claim-1", document_version_id: 31, document_title: "Algorithms", page_start: 2, page_end: 2 },
    { claim_id: "claim-2", document_version_id: 42, document_title: "Lecture notes", unit_start: 4, unit_end: 5 },
  ],
};
function evidence(version = 31, excerpts: string[] = ["Exact source text."]) {
  return { document_version_id: version, document_title: version === 31 ? "Algorithms" : "Lecture notes",
    course_title: "AI380", start: version === 31 ? 2 : 4, end: version === 31 ? 2 : 5,
    location_kind: version === 31 ? "PAGE" : "STRUCTURE", excerpts, truncated: false };
}
afterEach(() => { vi.restoreAllMocks(); });

describe("citation inspection", () => {
  it("opens each citation's source, closes with Escape, and restores focus", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(Response.json(evidence()))
      .mockResolvedValueOnce(Response.json(evidence(42, ["Structural original."])));
    render(<RagCitationList provenance={provenance} />);
    const trigger = screen.getByRole("button", { name: /Algorithms/ });
    trigger.focus();
    fireEvent.click(trigger);
    const dialog = await screen.findByRole("dialog", { name: "Algorithms" });
    expect(await screen.findByText("Exact source text.")).toBeVisible();
    expect(screen.getByText("AI380")).toBeVisible();
    expect(screen.getByRole("region", { name: "Source context" })).toBeVisible();
    expect(screen.getByText(/Exact highlighting is unavailable/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Open original document" })).toHaveAttribute("href", "/api/v1/document-versions/31/file#page=2");
    expect(fetchMock).toHaveBeenNthCalledWith(1, "/api/v1/document-versions/31/evidence?start=2&end=2", expect.any(Object));
    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(trigger).toHaveFocus());
    fireEvent.click(screen.getByRole("button", { name: /Lecture notes/ }));
    expect(await screen.findByText("Structural original.")).toBeVisible();
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/v1/document-versions/42/evidence?start=4&end=5", expect.any(Object));
    expect(screen.getByRole("link", { name: "Open original document" })).toHaveAttribute("href", "/api/v1/document-versions/42/file");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("handles missing excerpts and inaccessible evidence with a retry", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response("", { status: 404 }))
      .mockResolvedValueOnce(Response.json(evidence(31, [])));
    render(<RagCitationList provenance={provenance} />);
    fireEvent.click(screen.getByRole("button", { name: /Algorithms/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Evidence is unavailable");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("No extracted text is available at this location.")).toBeVisible();
    expect(screen.queryByText(/verified supporting claim/i)).not.toBeInTheDocument();
  });

  it("rejects mismatched evidence instead of displaying another document's text", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json(evidence(42)));
    render(<RagCitationList provenance={provenance} />);
    fireEvent.click(screen.getByRole("button", { name: /Algorithms/ }));
    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.queryByText("Exact source text.")).not.toBeInTheDocument();
  });

  it("clears loading after an interrupted stream and retries with the same request ID", async () => {
    const final = { status: "ANSWERED", answer_text: "Recovered", context_sources: [], sources: [], conversation_id: null, step_count: 1 };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(
      'event: stage\ndata: {"stage":"WRITING"}\n\n', { headers: { "content-type": "text/event-stream" } },
    )).mockResolvedValueOnce(new Response(`event: result\ndata: ${JSON.stringify(final)}\n\n`, { headers: { "content-type": "text/event-stream" } }));
    render(<AskView />);
    const send = () => {
      fireEvent.change(screen.getByRole("textbox", { name: "Your message" }), { target: { value: "Explain algorithms" } });
      fireEvent.click(screen.getByRole("button", { name: "Send" }));
    };
    send();
    expect(await screen.findByText("Unexpected response")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Stop waiting" })).not.toBeInTheDocument();
    send();
    expect(await screen.findByText("Recovered")).toBeVisible();
    const first = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    const second = JSON.parse(String(fetchMock.mock.calls[1][1]?.body));
    expect(second.request_id).toBe(first.request_id);
  });

  it("renders citations from a real parsed final stream and inspects their original evidence", async () => {
    const final = { status: "ANSWERED", answer_text: "Grounded answer", context_sources: [], sources: [],
      conversation_id: null, step_count: 1, rag_provenance: provenance };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(
      `event: stage\ndata: {"stage":"VERIFYING"}\n\nevent: result\ndata: ${JSON.stringify(final)}\n\n`,
      { headers: { "content-type": "text/event-stream" } },
    )).mockResolvedValueOnce(Response.json(evidence()));
    render(<AskView />);
    fireEvent.change(screen.getByRole("textbox", { name: "Your message" }), { target: { value: "Explain algorithms" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("Grounded answer")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /Algorithms/ }));
    expect(await screen.findByText("Exact source text.")).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
