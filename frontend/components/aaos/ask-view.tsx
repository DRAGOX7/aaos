"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { PageHeader } from "@/components/aaos/page-header";
import { AskCitation } from "@/components/aaos/ask-citation";
import { SectionCard } from "@/components/aaos/section-card";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api/errors";
import { fetchRagQuery, ragCitationKey, type RagQueryRequest } from "@/lib/api/rag";
import { courseListQueryOptions } from "@/lib/query/courses";

export function AskView() {
  const [question, setQuestion] = useState("");
  const [scopeType, setScopeType] = useState<"global" | "course">("global");
  const [courseId, setCourseId] = useState("");
  const [validationMessage, setValidationMessage] = useState<string | null>(null);
  const coursesQuery = useQuery(courseListQueryOptions());
  const mutation = useMutation({
    mutationFn: (request: RagQueryRequest) => fetchRagQuery(request),
  });

  function submitQuestion() {
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion) {
      setValidationMessage("Enter a question to continue.");
      return;
    }
    if (trimmedQuestion.length > 4096) {
      setValidationMessage("Your question is too long.");
      return;
    }
    const request: RagQueryRequest = scopeType === "global"
      ? { question: trimmedQuestion, scope: { type: "global" } }
      : {
          question: trimmedQuestion,
          scope: { type: "course", course_id: Number(courseId) },
        };
    if (scopeType === "course" && (!Number.isSafeInteger(Number(courseId)) || Number(courseId) <= 0)) {
      setValidationMessage("Choose an active course to continue.");
      return;
    }
    setValidationMessage(null);
    void mutation.mutateAsync(request);
  }

  const answer = mutation.data;
  const operationalError =
    mutation.error instanceof ApiError && mutation.error.code === "RAG_UNAVAILABLE"
      ? mutation.error
      : null;

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6">
      <PageHeader
        title="Ask AAOS"
        description="Ask questions about your trusted academic knowledge base."
      />

      <SectionCard title="Ask a question">
        <div className="space-y-4">
          <label className="block space-y-1.5 text-sm font-medium" htmlFor="ask-question">
            <span>Question</span>
            <textarea
              id="ask-question"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              className="min-h-28 w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              maxLength={4096}
            />
          </label>

          <label className="block space-y-1.5 text-sm font-medium" htmlFor="ask-scope">
            <span>Scope</span>
            <select
              id="ask-scope"
              value={scopeType}
              onChange={(event) => setScopeType(event.target.value as "global" | "course")}
              className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <option value="global">All active academic content</option>
              <option value="course">One active course</option>
            </select>
          </label>

          {scopeType === "course" ? (
            <label className="block space-y-1.5 text-sm font-medium" htmlFor="ask-course">
              <span>Course</span>
              <select
                id="ask-course"
                value={courseId}
                onChange={(event) => setCourseId(event.target.value)}
                className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <option value="">Choose an active course</option>
                {coursesQuery.data?.courses.map((course) => (
                  <option key={course.id} value={course.id}>
                    {course.title}{course.code ? ` (${course.code})` : ""}
                  </option>
                ))}
              </select>
            </label>
          ) : null}

          {validationMessage ? (
            <p className="text-sm text-destructive">{validationMessage}</p>
          ) : null}

          <Button type="button" onClick={submitQuestion} disabled={mutation.isPending}>
            {mutation.isPending ? "Checking evidence…" : "Ask AAOS"}
          </Button>
        </div>
      </SectionCard>

      {operationalError ? (
        <p className="text-sm text-muted-foreground">AAOS could not answer from the local knowledge base.</p>
      ) : null}

      {answer?.status === "INSUFFICIENT_EVIDENCE" ? (
        <SectionCard title="Not enough evidence">
          <p className="text-sm text-muted-foreground">{answer.answer_text}</p>
        </SectionCard>
      ) : answer ? (
        <SectionCard title="Validated answer">
          <p className="whitespace-pre-wrap text-sm leading-6 text-foreground">{answer.answer_text}</p>
          {answer.citations.length ? (
            <ul className="mt-4 space-y-2 border-t border-border pt-4" aria-label="Citations">
              {answer.citations.map((citation) => (
                <AskCitation
                  key={ragCitationKey(citation)}
                  citation={citation}
                />
              ))}
            </ul>
          ) : null}
        </SectionCard>
      ) : null}
    </div>
  );
}
