import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";

import DocumentsPage from "@/app/documents/page";
import DocumentDetailPage from "@/app/documents/[documentId]/page";
import { DocumentDetailView } from "@/components/aaos/document-detail-view";
import { DocumentList } from "@/components/aaos/document-list";
import type {
  DocumentListResponse,
  DocumentSummary,
} from "@/lib/api/academic";
import { ApiError } from "@/lib/api/errors";
import { parseDocumentRouteId } from "@/lib/routing/route-ids";

const { useQueryMock } = vi.hoisted(() => ({
  useQueryMock: vi.fn(),
}));

vi.mock("@tanstack/react-query", async () => {
  const actual =
    await vi.importActual<typeof import("@tanstack/react-query")>(
      "@tanstack/react-query",
    );

  return {
    ...actual,
    useQuery: useQueryMock,
  };
});

const readyDocument: DocumentSummary = {
  id: 7,
  course_id: 42,
  title: "Operating Systems notes",
  ingestion_status: "READY",
  extraction_status: "SUCCESS",
  page_count: 12,
  index_readiness: "READY",
  index_coverage: "FULL",
  updated_at: "2026-09-17T12:00:00Z",
};

const documents: DocumentListResponse = {
  documents: [
    readyDocument,
    {
      id: 8,
      course_id: 42,
      title: "Distributed systems outline",
      ingestion_status: "NEVER_ATTEMPTED",
      extraction_status: "PARTIAL",
      page_count: null,
      index_readiness: "PREPARING",
      index_coverage: "PARTIAL",
      updated_at: "2026-09-17T12:00:00Z",
    },
    {
      id: 9,
      course_id: 42,
      title: "No text document",
      ingestion_status: "FAILED",
      extraction_status: "NO_TEXT",
      page_count: null,
      index_readiness: "NOT_SEARCHABLE",
      index_coverage: "NONE",
      updated_at: "2026-09-17T12:00:00Z",
    },
    {
      id: 10,
      course_id: 42,
      title: "Failed index document",
      ingestion_status: "READY",
      extraction_status: "SUCCESS",
      page_count: 3,
      index_readiness: "FAILED",
      index_coverage: "FULL",
      updated_at: "2026-09-17T12:00:00Z",
    },
  ],
};

function setQueryState({
  data,
  error,
  isError = false,
  isPending = false,
}: {
  data?: DocumentListResponse | DocumentSummary;
  error?: unknown;
  isError?: boolean;
  isPending?: boolean;
}) {
  useQueryMock.mockReturnValue({
    data,
    error,
    isError,
    isPending,
  });
}

describe("AAOS document read views", () => {
  beforeEach(() => {
    useQueryMock.mockReset();
  });

  describe("document list", () => {
    it("renders loading and the real empty state", () => {
      setQueryState({ isPending: true });

      render(<DocumentList />);

      expect(
        screen.getByRole("heading", { level: 1, name: "Documents" }),
      ).toBeInTheDocument();
      expect(screen.getByLabelText("Loading documents")).toBeInTheDocument();

      useQueryMock.mockReset();
      setQueryState({ data: { documents: [] } });
      render(<DocumentList />);

      expect(screen.getByText("No documents found.")).toBeInTheDocument();
    });

    it("renders documents in API order with safe readiness metadata", () => {
      setQueryState({ data: documents });

      render(<DocumentList />);

      const rows = screen.getAllByRole("listitem");
      expect(rows.map((row) => within(row).getByRole("heading").textContent)).toEqual([
        "Operating Systems notes",
        "Distributed systems outline",
        "No text document",
        "Failed index document",
      ]);
      expect(screen.getByRole("link", { name: "Operating Systems notes" })).toHaveAttribute(
        "href",
        "/documents/7",
      );
      expect(within(rows[0]!).getByText("Pages: 12")).toBeInTheDocument();
      expect(within(rows[0]!).getByText("Ingestion: Ready")).toBeInTheDocument();
      expect(screen.getByText("Extraction: Partial")).toBeInTheDocument();
      expect(screen.getByText("Index readiness: Preparing")).toBeInTheDocument();
      expect(screen.getByText("Index readiness: Failed")).toBeInTheDocument();
      expect(screen.getByText("Index coverage: None")).toBeInTheDocument();
      expect(screen.queryByText(/page content|citation|traceback|SQL/i)).not.toBeInTheDocument();
    });

    it("renders neutral text when optional readiness is absent", () => {
      setQueryState({
        data: {
          documents: [
            {
              ...readyDocument,
              extraction_status: null,
              index_readiness: null,
              index_coverage: null,
              page_count: null,
            },
          ],
        },
      });

      render(<DocumentList />);

      expect(screen.getByText("Extraction: Not available yet")).toBeInTheDocument();
      expect(screen.getByText("Index readiness: Not available yet")).toBeInTheDocument();
      expect(screen.getByText("Index coverage: Not available yet")).toBeInTheDocument();
      expect(screen.queryByText(/UNKNOWN|UNAVAILABLE/)).not.toBeInTheDocument();
      expect(screen.queryByText(/Pages:/)).not.toBeInTheDocument();
    });

    it("renders bounded not-found and generic errors without backend details", () => {
      setQueryState({
        error: new ApiError(
          404,
          "COURSE_NOT_FOUND",
          "The requested course is not available.",
        ),
        isError: true,
      });

      render(<DocumentList courseId={42} />);
      expect(screen.getByText("Course not found.")).toBeInTheDocument();

      useQueryMock.mockReset();
      setQueryState({
        error: new Error("private traceback, SQL, and local path"),
        isError: true,
      });
      render(<DocumentList />);

      expect(
        screen.getByText("Academic data is temporarily unavailable."),
      ).toBeInTheDocument();
      expect(screen.queryByText(/private traceback|SQL|local path/i)).not.toBeInTheDocument();
    });

    it("uses shared course filters and makes no request for invalid filters", async () => {
      setQueryState({ data: { documents: [] } });
      const filtered = await DocumentsPage({
        searchParams: Promise.resolve({ course_id: "42" }),
      });

      render(filtered);
      expect(useQueryMock.mock.calls[0]?.[0].queryKey).toEqual([
        "documents",
        "list",
        42,
      ]);

      useQueryMock.mockReset();
      const invalid = await DocumentsPage({
        searchParams: Promise.resolve({ course_id: "not-an-integer" }),
      });
      render(invalid);

      expect(screen.getByText("The course filter is invalid.")).toBeInTheDocument();
      expect(useQueryMock).not.toHaveBeenCalled();
    });

    it("omits the filter for a direct unfiltered URL", async () => {
      setQueryState({ data: { documents: [] } });
      const view = await DocumentsPage({ searchParams: Promise.resolve({}) });

      render(view);

      expect(useQueryMock.mock.calls[0]?.[0].queryKey).toEqual([
        "documents",
        "list",
      ]);
    });
  });

  describe("document detail", () => {
    it("renders a valid document through the detail query", () => {
      setQueryState({ data: readyDocument });

      render(<DocumentDetailView documentId={7} />);

      expect(useQueryMock.mock.calls[0]?.[0].queryKey).toEqual([
        "documents",
        "detail",
        7,
      ]);
      expect(screen.getByRole("heading", { name: "Operating Systems notes" })).toBeInTheDocument();
      expect(screen.getByText("Pages: 12")).toBeInTheDocument();
    });

    it("renders bounded invalid, not-found, and generic detail states", () => {
      render(<DocumentDetailView documentId={null} />);
      expect(screen.getByText("The document link is invalid.")).toBeInTheDocument();
      expect(useQueryMock).not.toHaveBeenCalled();

      setQueryState({
        error: new ApiError(
          404,
          "DOCUMENT_NOT_FOUND",
          "The requested document is not available.",
        ),
        isError: true,
      });
      render(<DocumentDetailView documentId={7} />);
      expect(screen.getByText("Document not found.")).toBeInTheDocument();

      useQueryMock.mockReset();
      setQueryState({
        error: new Error("private traceback, SQL, local path"),
        isError: true,
      });
      render(<DocumentDetailView documentId={7} />);
      expect(
        screen.getByText("Academic data is temporarily unavailable."),
      ).toBeInTheDocument();
      expect(screen.queryByText(/private traceback|SQL|local path/i)).not.toBeInTheDocument();
    });

    it("rejects malformed, zero, and negative route IDs without a request", async () => {
      for (const routeId of ["not-an-integer", "0", "-1"]) {
        expect(parseDocumentRouteId(routeId)).toBeNull();
      }

      const view = await DocumentDetailPage({
        params: Promise.resolve({ documentId: "not-an-integer" }),
      });
      render(view);
      expect(screen.getByText("The document link is invalid.")).toBeInTheDocument();

      expect(useQueryMock).not.toHaveBeenCalled();
    });
  });
});
