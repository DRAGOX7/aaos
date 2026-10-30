"use client";

import { useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import {
  ArrowUp,
  CalendarClock,
  FileText,
  ListChecks,
  LoaderCircle,
  MessageCircle,
  Plus,
  RefreshCw,
  Square,
  type LucideIcon,
} from "lucide-react";

import { AskConversation, type AskIssueKind, type AskThreadEntry } from "@/components/aaos/ask-conversation";
import { EvidenceWorkspace } from "@/components/aaos/citation-evidence";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api/errors";
import { takeAskHandoff } from "@/lib/ask/handoff";
import { useI18n, type MessageKey } from "@/lib/i18n";
import {
  MalformedAskResponseError,
  streamAcademicAsk,
  type AcademicAskRequest,
  type AskStage,
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
  // Messages stay in English here; the conversation shows them translated.
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

// Starting points that map to what AAOS can actually answer.
const ASK_SUGGESTIONS: readonly { topic: MessageKey; question: MessageKey; icon: LucideIcon }[] = [
  { topic: "Course files", question: "What does the syllabus say about the final exam?", icon: FileText },
  { topic: "Deadlines", question: "What's due this week?", icon: CalendarClock },
  { topic: "Moodle changes", question: "What changed in Moodle since yesterday?", icon: RefreshCw },
  { topic: "Study planning", question: "Plan my study week", icon: ListChecks },
];

export function AskView() {
  const { t } = useI18n();
  const [question, setQuestion] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [entries, setEntries] = useState<AskThreadEntry[]>([]);
  const [pending, setPending] = useState(false);
  const [stage, setStage] = useState<AskStage | null>(null);
  const [validationMessage, setValidationMessage] = useState<MessageKey | null>(null);
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
    setStage(null);

    const controller = new AbortController();
    activeController.current = controller;
    const timeoutHandle = window.setTimeout(() => {
      abortCause.current = "timeout";
      controller.abort();
    }, ASK_CLIENT_TIMEOUT_MS);

    try {
      // The answer is delivered as a stream of stage updates followed by the
      // final, evidence-verified result; only the waiting indicator changes.
      const response = await streamAcademicAsk(request, controller.signal, setStage);
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
      setStage(null);
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
    <EvidenceWorkspace key={entries[0]?.id ?? "empty"} header={
      <div className="mb-8 flex flex-col items-start gap-5">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold tracking-[-0.02em] text-foreground sm:text-[1.75rem]">
            {t("Ask AAOS")}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {t("Answers from your course files, Moodle data and tasks, with their sources.")}
          </p>
        </div>
        {entries.length > 0 ? (
          <Button
            type="button"
            variant="outline"
            className="min-h-11 self-end rounded-xl"
            onClick={startNewConversation}
            disabled={pending}
          >
            <Plus aria-hidden="true" />
            {t("New conversation")}
          </Button>
        ) : null}
      </div>
    }><div className="mx-auto flex min-h-[calc(100dvh-16rem)] w-full max-w-3xl flex-col gap-8">
      {entries.length === 0 ? (
        <section aria-labelledby="ask-empty-title" className="flex flex-col items-center gap-6 py-6 text-center sm:py-10">
          <span
            aria-hidden="true"
            className="flex size-14 items-center justify-center rounded-2xl bg-hero text-white shadow-[0_14px_30px_rgb(16_33_75_/_0.22)]"
          >
            <MessageCircle className="size-6" strokeWidth={2} />
          </span>
          <div className="flex flex-col gap-2">
            <h2 id="ask-empty-title" className="text-xl font-bold tracking-tight text-foreground sm:text-2xl">
              {t("What would you like to work on?")}
            </h2>
            <p className="mx-auto max-w-xl text-sm leading-6 text-muted-foreground">
              {t("Ask about your courses, deadlines and Moodle changes, or plan your week. AAOS says so when your files do not support an answer.")}
            </p>
          </div>
          <ul aria-label={t("Suggested questions")} className="grid w-full gap-3 text-start sm:grid-cols-2">
            {ASK_SUGGESTIONS.map(({ topic, question: suggestion, icon: Icon }) => (
              <li key={suggestion}>
                <button
                  type="button"
                  onClick={() => void submitQuestion(t(suggestion))}
                  className="flex h-full w-full items-start gap-3 rounded-2xl border border-border bg-card p-4 text-start transition-[border-color,box-shadow] duration-200 hover:border-border-hover hover:shadow-[0_10px_28px_rgb(16_33_75_/_0.08)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-primary-soft text-primary">
                    <Icon aria-hidden="true" className="size-[18px]" strokeWidth={2} />
                  </span>
                  <span className="flex min-w-0 flex-col gap-0.5">
                    <span className="text-xs font-semibold text-muted-foreground">{t(topic)}</span>
                    <span className="text-sm font-semibold text-foreground">{t(suggestion)}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <AskConversation entries={entries} pending={pending} stage={stage} />

      <div className="sticky bottom-0 z-10 mt-auto bg-[linear-gradient(to_top,var(--aaos-background)_70%,transparent)] pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-4">
        <form
          onSubmit={handleSubmit}
          className="flex items-end gap-2 rounded-2xl border border-border-hover bg-card p-3 focus-within:ring-2 focus-within:ring-ring"
        >
          <label className="min-w-0 flex-1" htmlFor="ask-composer">
            <span className="sr-only">{t("Your message")}</span>
            <textarea
              id="ask-composer"
              aria-describedby={validationMessage ? "ask-validation ask-session-note" : "ask-session-note"}
              autoComplete="off"
              dir="auto"
              rows={1}
              className="block max-h-40 min-h-11 w-full resize-none rounded-xl bg-transparent px-3 py-2.5 text-[15px] leading-6 text-foreground outline-none placeholder:text-text-subtle focus-visible:ring-2 focus-visible:ring-ring [field-sizing:content]"
              maxLength={4096}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={handleComposerKeyDown}
              placeholder={t("Ask about your courses, deadlines or files…")}
              value={question}
            />
          </label>
          {pending ? (
            <Button
              type="button"
              variant="outline"
              className="h-11 rounded-xl"
              onClick={stopWaiting}
            >
              <Square aria-hidden="true" />
              {t("Stop waiting")}
            </Button>
          ) : null}
          <Button type="submit" disabled={pending} className="size-11 shrink-0 rounded-xl">
            {pending ? (
              <LoaderCircle aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
            ) : (
              <ArrowUp aria-hidden="true" strokeWidth={2.4} />
            )}
            <span className="sr-only">{pending ? t("Sending") : t("Send")}</span>
          </Button>
        </form>
        {validationMessage ? (
          <p id="ask-validation" className="mt-2 text-sm text-destructive" role="alert">
            {t(validationMessage)}
          </p>
        ) : null}
        <p id="ask-session-note" className="mt-2 text-center text-xs text-muted-foreground">
          {t("This conversation stays in this page and is not restored after refresh.")}
          {pending ? "" : ` ${t("Enter to send · Shift+Enter for a new line.")}`}
        </p>
        <p aria-live="polite" className="sr-only">
          {pending ? t("AAOS is thinking.") : ""}
        </p>
      </div>
    </div></EvidenceWorkspace>
  );
}
