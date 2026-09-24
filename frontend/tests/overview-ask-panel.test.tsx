import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { OverviewAskPanel } from "@/components/aaos/overview-ask-panel";

const { useMutationMock } = vi.hoisted(() => ({
  useMutationMock: vi.fn(),
}));

vi.mock("@tanstack/react-query", async () => ({
  ...(await vi.importActual<typeof import("@tanstack/react-query")>(
    "@tanstack/react-query",
  )),
  useMutation: useMutationMock,
}));

describe("overview Ask AAOS panel", () => {
  beforeEach(() => {
    useMutationMock.mockReset();
    useMutationMock.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    });
  });

  it("renders an inline grounded question experience without fake state", () => {
    render(<OverviewAskPanel />);

    expect(screen.getByRole("textbox", { name: "Ask a grounded question" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ask AAOS" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open full Ask AAOS" })).toHaveAttribute(
      "href",
      "/ask",
    );
    expect(screen.getByText(/Grounded answers keep citations visible/)).toBeInTheDocument();
    expect(screen.queryByText(/GPT|latency|provider|schedule/i)).not.toBeInTheDocument();
  });

  it("submits only the existing global RAG request contract", () => {
    const mutateAsync = vi.fn();
    useMutationMock.mockReturnValue({ mutateAsync, isPending: false });
    render(<OverviewAskPanel />);

    fireEvent.change(screen.getByRole("textbox", { name: "Ask a grounded question" }), {
      target: { value: "What is the difference between paging and segmentation?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Ask AAOS" }));

    expect(mutateAsync).toHaveBeenCalledWith({
      question: "What is the difference between paging and segmentation?",
      scope: { type: "global" },
    });
  });

  it("keeps grounded answer citations visible when a result is available", () => {
    useMutationMock.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
      data: {
        status: "ANSWER",
        answer_text: "Paging divides memory into fixed-size pages.",
        language: "en",
        citations: [
          {
            document_id: 7,
            document_title: "Operating Systems",
            page_start: 2,
            page_end: 4,
          },
        ],
      },
    });

    render(<OverviewAskPanel />);

    expect(screen.getByText("Grounded answer")).toBeInTheDocument();
    expect(screen.getByText("Paging divides memory into fixed-size pages.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Operating Systems" })).toHaveAttribute(
      "href",
      "/documents/7",
    );
  });

  it("renders insufficient evidence as a distinct truthful result", () => {
    useMutationMock.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
      data: {
        status: "INSUFFICIENT_EVIDENCE",
        answer_text: "There is not enough evidence in the active academic content.",
        language: "en",
        citations: [],
      },
    });

    render(<OverviewAskPanel />);

    expect(screen.getByText("Insufficient evidence")).toBeInTheDocument();
    expect(screen.getByText("There is not enough evidence in the active academic content.")).toBeInTheDocument();
  });

  it("renders structural citations in the inline grounded answer", () => {
    useMutationMock.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
      data: {
        status: "ANSWER",
        answer_text: "Normalization reduces update anomalies.",
        language: "en",
        citations: [
          {
            document_id: 15,
            document_title: "Database Systems",
            unit_start: 2,
            unit_end: 3,
          },
        ],
      },
    });

    render(<OverviewAskPanel />);

    expect(screen.getByRole("link", { name: "Database Systems" })).toHaveAttribute(
      "href",
      "/documents/15",
    );
    expect(screen.getByText(/units 2–3/)).toBeInTheDocument();
    expect(screen.queryByText(/page/i)).not.toBeInTheDocument();
  });
});
