import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { AskView } from "@/components/aaos/ask-view";
import { ApiError } from "@/lib/api/errors";

const { useMutationMock, useQueryMock } = vi.hoisted(() => ({
  useMutationMock: vi.fn(),
  useQueryMock: vi.fn(),
}));

vi.mock("@tanstack/react-query", async () => {
  const actual = await vi.importActual<typeof import("@tanstack/react-query")>(
    "@tanstack/react-query",
  );
  return {
    ...actual,
    useMutation: useMutationMock,
    useQuery: useQueryMock,
  };
});

function setState({
  mutateAsync = vi.fn(),
  mutation,
  courses,
}: {
  mutateAsync?: ReturnType<typeof vi.fn>;
  mutation?: Record<string, unknown>;
  courses?: unknown;
} = {}) {
  useQueryMock.mockReturnValue({ data: courses ?? { courses: [] }, isPending: false });
  useMutationMock.mockReturnValue({
    mutateAsync,
    isPending: false,
    ...mutation,
  });
}

describe("Ask AAOS view", () => {
  beforeEach(() => {
    useMutationMock.mockReset();
    useQueryMock.mockReset();
  });

  it("does not submit an empty question", () => {
    const mutateAsync = vi.fn();
    setState({ mutateAsync });
    render(<AskView />);

    fireEvent.click(screen.getByRole("button", { name: "Ask AAOS" }));

    expect(screen.getByText("Enter a question to continue.")).toBeInTheDocument();
    expect(mutateAsync).not.toHaveBeenCalled();
  });

  it("submits global scope with only question and scope", async () => {
    const mutateAsync = vi.fn().mockResolvedValue({
      status: "ANSWER",
      answer_text: "Validated answer",
      language: "en",
      citations: [],
    });
    setState({ mutateAsync });
    render(<AskView />);

    fireEvent.change(screen.getByLabelText("Question"), {
      target: { value: "What is self-attention?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Ask AAOS" }));

    expect(mutateAsync).toHaveBeenCalledWith({
      question: "What is self-attention?",
      scope: { type: "global" },
    });
  });

  it("offers only active course metadata for course scope", () => {
    setState({
      courses: {
        courses: [{ id: 12, title: "Operating Systems", code: "CS-12" }],
      },
    });
    render(<AskView />);

    fireEvent.change(screen.getByLabelText("Scope"), {
      target: { value: "course" },
    });

    expect(screen.getByRole("option", { name: "Operating Systems (CS-12)" })).toBeInTheDocument();
    expect(screen.queryByText(/provider|model|approval|write/i)).not.toBeInTheDocument();
  });

  it("renders insufficient evidence distinctly", () => {
    setState({
      mutation: {
        data: {
          status: "INSUFFICIENT_EVIDENCE",
          answer_text: "There is not enough evidence.",
          language: "en",
          citations: [],
        },
      },
    });
    render(<AskView />);

    expect(screen.getByText("Not enough evidence")).toBeInTheDocument();
    expect(screen.getByText("There is not enough evidence.")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("renders bounded operational recovery copy", () => {
    setState({
      mutation: {
        isError: true,
        error: new ApiError(503, "RAG_UNAVAILABLE", "safe message"),
      },
    });
    render(<AskView />);

    expect(screen.getByText("AAOS could not answer from the local knowledge base.")).toBeInTheDocument();
    expect(screen.queryByText(/safe message|traceback|provider/i)).not.toBeInTheDocument();
  });

  it("renders structural citations without reading page fields", () => {
    setState({
      mutation: {
        data: {
          status: "ANSWER",
          answer_text: "The table defines candidate keys.",
          language: "en",
          citations: [
            {
              document_id: 12,
              document_title: "Database Systems",
              unit_start: 8,
              unit_end: 8,
              heading: "Keys",
              table_index: 1,
              row_index: 2,
              column_index: 0,
            },
          ],
        },
      },
    });
    render(<AskView />);

    expect(screen.getByRole("link", { name: "Database Systems" })).toHaveAttribute(
      "href",
      "/documents/12",
    );
    expect(screen.getByText(/unit 8/)).toBeInTheDocument();
    expect(screen.getByText(/heading Keys/)).toBeInTheDocument();
    expect(screen.getByText(/table 2, row 3, column 1/)).toBeInTheDocument();
    expect(screen.queryByText(/page/i)).not.toBeInTheDocument();
  });
});
