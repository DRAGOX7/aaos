"use client";

import { useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import { LoaderCircle, Plus, Send, Square } from "lucide-react";

import { AskConversation, type AskIssueKind, type AskThreadEntry } from "@/components/aaos/ask-conversation";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api/errors";
import { takeAskHandoff } from "@/lib/ask/handoff";
import {
  fetchAcademicAsk,
  MalformedAskResponseError,
  type AcademicAskRequest,
} from "@/lib/api/ask";

const ASK_CLIENT_TIMEOUT_MS = 60_000;

type RetryAttempt = Readonly<{
  question: string;
  conversationId: string | null;
  requestId: string;
}>;

function safeRequestIssue(
  error: unknown,
  abortCause: "timeout" | "stopped" | null,
): { kind: AskIssueKind; message: string } {
  if (abortCause === "timeout") {
    return {
      kind: "timeout",
      message: "No response arrived before the page time limit, so the connection was closed. The backend may still be determining whether a local action committed. Retrying this same question reuses its request ID.",
    };
  }
  if (abortCause === "stopped") {
    return {
      kind: "stopped",
      message: "The request connection was closed. A local task change may already have committed before the response was received; retrying this same question reuses its request ID.",
    };
  }
  if (error instanceof MalformedAskResponseError) {
    return {
      kind: "malformed",
      message: "AAOS returned an unreadable response. No success is displayed; retrying this question reuses its request ID.",
    };
  }
  if (error instanceof ApiError) {
    if (error.status === 408 || error.status === 504) {
      return {
        kind: "timeout",
        message: "AAOS did not finish this request in time. No success is shown; retrying this same question reuses its request ID.",
      };
    }
    return {
      kind: error.status === 503 || error.status >= 500 ? "unavailable" : "malformed",
      message: error.message,
    };
  }
  if (error instanceof TypeError) {
    return {
      kind: "network",
      message: "The page could not reach AAOS. Retrying this same question reuses its request ID.",
    };
  }
  return {
    kind: "unavailable",
    message: "AAOS could not complete this request. No success is shown.",
  };
}

function createRequestId(): string | null {
  if (typeof globalThis.crypto?.randomUUID !== "function") return null;
  return globalThis.crypto.randomUUID();
}

export function AskView() {
  const [question, setQuestion] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [entries, setEntries] = useState<AskThreadEntry[]>([]);
  const [pending, setPending] = useState(false);
  const [validationMessage, setValidationMessage] = useState<string | null>(null);
  const requestInFlight = useRef(false);
  const activeController = useRef<AbortController | null>(null);
  const abortCause = useRef<"timeout" | "stopped" | null>(null);
  const retryAttempt = useRef<RetryAttempt | null>(null);

  // A question started on the Overview arrives through a one-time handoff
  // and is sent as if typed here. takeAskHandoff removes it, so a remount
  // never sends it twice.
  useEffect(() => {
    const handoff = takeAskHandoff();
    if (handoff) void submitQuestion(handoff);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once on arrival
  }, []);

  async function submitQuestion(text: string = question) {
    if (requestInFlight.current) return;
    const normalizedQuestion = text.trim();
    if (!normalizedQuestion) {
      setValidationMessage("Enter a question to continue.");
      return;
    }
    if (normalizedQuestion.length > 4096) {
      setValidationMessage("Your question is too long.");
      return;
    }

    const previousAttempt = retryAttempt.current;
    const requestId =
      previousAttempt?.question === normalizedQuestion &&
      previousAttempt.conversationId === conversationId
        ? previousAttempt.requestId
        : createRequestId();
    if (!requestId) {
      setValidationMessage("This browser cannot create a secure request ID. Use a current browser to continue.");
      return;
    }

    const request: AcademicAskRequest = {
      question: normalizedQuestion,
      request_id: requestId,
      ...(conversationId ? { conversation_id: conversationId } : {}),
    };
    retryAttempt.current = {
      question: normalizedQuestion,
      conversationId,
      requestId,
    };
    requestInFlight.current = true;
    abortCause.current = null;
    setValidationMessage(null);
    setEntries((current) => [
      ...current,
      { id: crypto.randomUUID(), kind: "user", text: normalizedQuestion },
    ]);
    setQuestion("");
    setPending(true);

    const controller = new AbortController();
    activeController.current = controller;
    const timeoutHandle = window.setTimeout(() => {
      abortCause.current = "timeout";
      controller.abort();
    }, ASK_CLIENT_TIMEOUT_MS);

    try {
      const response = await fetchAcademicAsk(request, controller.signal);
      if (response.refusal_code === "STALE_CONTEXT") {
        setConversationId(null);
      } else if (response.conversation_id) {
        setConversationId(response.conversation_id);
      }
      setEntries((current) => [
        ...current,
        { id: crypto.randomUUID(), kind: "assistant", response },
      ]);
      if (
        response.status === "TIMED_OUT" ||
        response.status === "CANCELLED" ||
        response.status === "FAILED"
      ) {
        retryAttempt.current = {
          question: normalizedQuestion,
          conversationId: response.conversation_id ?? conversationId,
          requestId,
        };
      } else {
        retryAttempt.current = null;
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 422) {
        retryAttempt.current = null;
      }
      const issue = safeRequestIssue(error, abortCause.current);
      setEntries((current) => [
        ...current,
        { id: crypto.randomUUID(), kind: "issue", issue },
      ]);
    } finally {
      window.clearTimeout(timeoutHandle);
      requestInFlight.current = false;
      activeController.current = null;
      setPending(false);
    }
  }

  function startNewConversation() {
    if (requestInFlight.current) return;
    setConversationId(null);
    setEntries([]);
    setQuestion("");
    setValidationMessage(null);
    retryAttempt.current = null;
  }

  function stopWaiting() {
    if (!requestInFlight.current || !activeController.current) return;
    abortCause.current = "stopped";
    activeController.current.abort();
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submitQuestion();
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void submitQuestion();
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight text-foreground sm:text-2xl">
            Ask AAOS
          </h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Ask about courses, documents, tasks, Moodle changes, and study plans.
          </p>
        </div>
        {entries.length > 0 ? (
          <Button
            type="button"
            variant="outline"
            className="mt-1"
            onClick={startNewConversation}
            disabled={pending}
          >
            <Plus aria-hidden="true" />
            New conversation
          </Button>
        ) : null}
      </div>

      {entries.length === 0 ? (
        <section className="rounded-2xl border border-border bg-[color:var(--aaos-surface-glass)] px-5 py-8 shadow-[var(--aaos-shadow-card)] sm:px-8">
          <h2 className="text-lg font-semibold tracking-tight text-foreground">
            What would you like to work on?
          </h2>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
            Ask a question or continue a bounded conversation about your academic data.
            AAOS can answer from trusted sources, review changes, manage StudyTasks,
            prepare Calendar proposals, and recommend study plans.
          </p>
        </section>
      ) : null}

      <AskConversation entries={entries} pending={pending} />

      <div className="sticky bottom-0 z-10 -mx-2 border-t border-border/80 bg-[color:var(--aaos-surface-glass)] px-2 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-3 backdrop-blur-xl">
        <form onSubmit={handleSubmit} className="mx-auto flex w-full max-w-5xl items-end gap-2">
          <label className="min-w-0 flex-1 space-y-1.5 text-sm font-medium" htmlFor="ask-composer">
            <span className="sr-only">Your message</span>
            <textarea
              id="ask-composer"
              aria-describedby={validationMessage ? "ask-validation ask-session-note" : "ask-session-note"}
              autoComplete="off"
              className="min-h-14 max-h-40 w-full resize-y rounded-xl border border-input bg-white px-4 py-3 text-sm leading-5 text-foreground outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
              maxLength={4096}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={handleComposerKeyDown}
              placeholder="Ask about courses, documents, tasks, Moodle changes, or study plans…"
              value={question}
            />
          </label>
          <Button type="submit" disabled={pending} className="h-12 min-w-12 px-4">
            {pending ? (
              <LoaderCircle aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
            ) : (
              <Send aria-hidden="true" />
            )}
            <span className="sr-only">{pending ? "Sending" : "Send"}</span>
          </Button>
          {pending ? (
            <Button
              type="button"
              variant="outline"
              className="h-12"
              onClick={stopWaiting}
            >
              <Square aria-hidden="true" />
              Stop waiting
            </Button>
          ) : null}
        </form>
        {validationMessage ? (
          <p id="ask-validation" className="mx-auto mt-2 w-full max-w-5xl text-sm text-destructive" role="alert">
            {validationMessage}
          </p>
        ) : null}
        <p id="ask-session-note" className="mx-auto mt-2 w-full max-w-5xl text-xs text-muted-foreground">
          This conversation stays in this page and is not restored after refresh.
          {pending ? "" : " Press Enter to send; use Shift+Enter for a new line."}
        </p>
        <p aria-live="polite" className="sr-only">
          {pending ? "AAOS is thinking." : ""}
        </p>
      </div>
    </div>
  );
}
