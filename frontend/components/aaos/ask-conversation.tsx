"use client";

import { GraduationCap } from "lucide-react";

import { AskResponseRenderer } from "@/components/aaos/ask-response-renderer";
import type { AcademicAskResponse } from "@/lib/api/ask";
import { useI18n } from "@/lib/i18n";

export type AskIssueKind = "network" | "timeout" | "stopped" | "unavailable" | "malformed";

export type AskThreadEntry =
  | { id: string; kind: "user"; text: string }
  | { id: string; kind: "assistant"; response: AcademicAskResponse }
  | { id: string; kind: "issue"; issue: { kind: AskIssueKind; message: string } };

function AskIssueMessage({ kind, message }: { kind: AskIssueKind; message: string }) {
  const { t, tx } = useI18n();
  const heading = kind === "timeout"
    ? "Request timed out"
    : kind === "stopped"
      ? "Stopped waiting"
      : kind === "network"
        ? "Connection problem"
        : kind === "malformed"
          ? "Unexpected response"
          : "AAOS is unavailable";

  return (
    <section className="rounded-xl border border-warning/35 bg-warning-soft/70 px-4 py-3" role="status">
      <h3 className="text-sm font-semibold text-foreground">{t(heading)}</h3>
      <p className="mt-1 break-words text-sm text-muted-foreground">{tx(message)}</p>
    </section>
  );
}

function AssistantMark() {
  return (
    <span
      aria-hidden="true"
      className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg bg-hero text-white"
    >
      <GraduationCap className="size-4" strokeWidth={2.1} />
    </span>
  );
}

export function AskConversation({
  entries,
  pending,
}: {
  entries: AskThreadEntry[];
  pending: boolean;
}) {
  const { t } = useI18n();
  return (
    <div
      aria-label={t("Conversation")}
      aria-live="polite"
      aria-relevant="additions"
      className="flex min-h-0 flex-col gap-6"
      role="log"
    >
      {entries.map((entry) =>
        entry.kind === "user" ? (
          <article aria-label={t("Your question")} className="flex justify-end" key={entry.id}>
            <p
              dir="auto"
              className="max-w-[88%] whitespace-pre-wrap break-words rounded-2xl rounded-ee-md bg-hero px-4 py-3 text-[15px] leading-6 text-white sm:max-w-[75%]"
            >
              {entry.text}
            </p>
          </article>
        ) : (
          <article aria-label={t("AAOS answer")} className="flex gap-3" key={entry.id}>
            <AssistantMark />
            <div className="min-w-0 flex-1 rounded-2xl rounded-ss-md border border-border bg-card px-5 py-4">
              {entry.kind === "assistant"
                ? <AskResponseRenderer response={entry.response} />
                : <AskIssueMessage kind={entry.issue.kind} message={entry.issue.message} />}
            </div>
          </article>
        ),
      )}
      {pending ? (
        <div aria-live="polite" className="flex items-center gap-3">
          <AssistantMark />
          <p className="flex items-center gap-2 rounded-2xl rounded-ss-md border border-border bg-card px-4 py-3 text-sm text-muted-foreground">
            <span aria-hidden="true" className="flex gap-1">
              <span className="size-1.5 animate-bounce rounded-full bg-primary [animation-delay:-0.3s] motion-reduce:animate-none" />
              <span className="size-1.5 animate-bounce rounded-full bg-primary [animation-delay:-0.15s] motion-reduce:animate-none" />
              <span className="size-1.5 animate-bounce rounded-full bg-primary motion-reduce:animate-none" />
            </span>
            {t("AAOS is thinking…")}
          </p>
        </div>
      ) : null}
    </div>
  );
}
