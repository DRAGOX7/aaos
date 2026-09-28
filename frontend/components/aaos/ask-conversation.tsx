"use client";

import { AskResponseRenderer } from "@/components/aaos/ask-response-renderer";
import type { AcademicAskResponse } from "@/lib/api/ask";

export type AskIssueKind = "network" | "timeout" | "stopped" | "unavailable" | "malformed";

export type AskThreadEntry =
  | { id: string; kind: "user"; text: string }
  | { id: string; kind: "assistant"; response: AcademicAskResponse }
  | { id: string; kind: "issue"; issue: { kind: AskIssueKind; message: string } };

function AskIssueMessage({ kind, message }: { kind: AskIssueKind; message: string }) {
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
      <h3 className="text-sm font-semibold text-foreground">{heading}</h3>
      <p className="mt-1 break-words text-sm text-muted-foreground">{message}</p>
    </section>
  );
}

export function AskConversation({
  entries,
  pending,
}: {
  entries: AskThreadEntry[];
  pending: boolean;
}) {
  return (
    <div
      aria-label="Conversation"
      aria-live="polite"
      aria-relevant="additions"
      className="min-h-0 space-y-5"
      role="log"
    >
      {entries.map((entry) => (
        <article
          className={entry.kind === "user" ? "flex justify-end" : "flex justify-start"}
          key={entry.id}
        >
          {entry.kind === "user" ? (
            <div className="max-w-[88%] rounded-2xl rounded-br-md border border-primary/10 bg-primary-soft px-4 py-3 sm:max-w-[75%]">
              <p className="whitespace-pre-wrap break-words text-sm leading-6 text-foreground">
                {entry.text}
              </p>
            </div>
          ) : (
            <div className="w-full max-w-4xl min-w-0 rounded-2xl border border-border bg-[color:var(--aaos-surface-glass)] p-4 shadow-[var(--aaos-shadow-card)] sm:p-5">
              <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-primary">AAOS</p>
              {entry.kind === "assistant"
                ? <AskResponseRenderer response={entry.response} />
                : <AskIssueMessage kind={entry.issue.kind} message={entry.issue.message} />}
            </div>
          )}
        </article>
      ))}
      {pending ? (
        <div aria-live="polite" className="flex justify-start">
          <p className="rounded-xl border border-border bg-white/80 px-4 py-3 text-sm text-muted-foreground">
            AAOS is thinking…
          </p>
        </div>
      ) : null}
    </div>
  );
}
