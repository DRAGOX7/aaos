"use client";

import Link from "next/link";
import { MessageCircle, Send } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";

import { AskCitation } from "@/components/aaos/ask-citation";
import { SectionCard } from "@/components/aaos/section-card";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api/errors";
import { fetchRagQuery, ragCitationKey, type RagQueryRequest } from "@/lib/api/rag";

export function OverviewAskPanel() {
  const [question, setQuestion] = useState("");
  const [validationMessage, setValidationMessage] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: (request: RagQueryRequest) => fetchRagQuery(request),
  });

  function submitQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion) {
      setValidationMessage("Enter a question to continue.");
      return;
    }
    if (trimmedQuestion.length > 4096) {
      setValidationMessage("Your question is too long.");
      return;
    }
    setValidationMessage(null);
    void mutation.mutateAsync({
      question: trimmedQuestion,
      scope: { type: "global" },
    });
  }

  const answer = mutation.data;
  const operationalError =
    mutation.error instanceof ApiError && mutation.error.code === "RAG_UNAVAILABLE"
      ? mutation.error
      : null;

  return (
    <SectionCard
      title="Ask AAOS"
      className="border-primary/20 bg-[linear-gradient(145deg,rgba(255,255,255,0.97),rgba(239,248,255,0.88))]"
      action={
        <MessageCircle aria-hidden="true" className="size-5 text-primary" strokeWidth={1.8} />
      }
    >
      <div className="space-y-4">
        <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
          <div className="max-w-2xl">
            <p className="text-lg font-semibold tracking-tight text-primary-deep sm:text-xl">
              Ask a question grounded in your academic knowledge base.
            </p>
            <p className="mt-1.5 text-sm leading-5 text-muted-foreground">
              Grounded answers keep citations visible and say when the evidence is not enough.
            </p>
          </div>
          <Link
            href="/ask"
            className="shrink-0 text-sm font-semibold text-primary underline-offset-4 transition-colors hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Open full Ask AAOS
          </Link>
        </div>

        <form className="flex flex-col gap-2 sm:flex-row" onSubmit={submitQuestion}>
          <label className="sr-only" htmlFor="overview-ask-question">
            Ask a grounded question
          </label>
          <input
            id="overview-ask-question"
            type="text"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            maxLength={4096}
            placeholder="What do you want to understand?"
            className="min-w-0 flex-1 rounded-xl border border-primary/20 bg-white/80 px-3.5 py-2.5 text-sm text-foreground outline-none transition-shadow placeholder:text-text-muted focus-visible:ring-2 focus-visible:ring-ring"
          />
          <Button type="submit" disabled={mutation.isPending} className="shrink-0">
            <Send aria-hidden="true" className="size-4" strokeWidth={1.8} />
            {mutation.isPending ? "Checking…" : "Ask AAOS"}
          </Button>
        </form>

        {validationMessage ? (
          <p className="text-sm text-destructive" role="alert">
            {validationMessage}
          </p>
        ) : null}

        {operationalError ? (
          <p className="border-t border-border pt-3 text-sm text-muted-foreground">
            AAOS could not answer from the local knowledge base.
          </p>
        ) : null}

        {answer?.status === "INSUFFICIENT_EVIDENCE" ? (
          <div className="border-t border-border pt-3" role="status">
            <p className="text-sm font-semibold text-warning">Insufficient evidence</p>
            <p className="mt-1 text-sm leading-5 text-muted-foreground">{answer.answer_text}</p>
          </div>
        ) : answer ? (
          <div className="border-t border-border pt-3">
            <p className="text-sm font-semibold text-primary-deep">Grounded answer</p>
            <p className="mt-1 text-sm leading-5 text-foreground">{answer.answer_text}</p>
            {answer.citations.length ? (
              <ul className="mt-3 space-y-1.5" aria-label="Citations">
                {answer.citations.map((citation) => (
                  <AskCitation
                    key={ragCitationKey(citation)}
                    citation={citation}
                  />
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
      </div>
    </SectionCard>
  );
}
