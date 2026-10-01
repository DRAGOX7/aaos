"use client";

import type { AcademicAskResponse, AcademicAskSource, RagProvenance } from "@/lib/api/ask";
import {
  CalendarProposalCard,
  MoodleChangeCard,
  RagCitationList,
  StudyPlanView,
  StudyTaskCard,
} from "@/components/aaos/ask-result-cards";

function AnswerText({ text }: { text: string | null | undefined }) {
  if (!text) return null;
  return (
    <p className="whitespace-pre-wrap break-words text-sm leading-6 text-foreground">
      {text}
    </p>
  );
}

function RefusalState({ response }: { response: AcademicAskResponse }) {
  const insufficient = response.status === "INSUFFICIENT_EVIDENCE";
  return (
    <section
      aria-label={insufficient ? "Insufficient evidence" : "Request refused"}
      className="rounded-xl border border-border bg-muted/60 px-4 py-3"
      role="note"
    >
      <h3 className="text-sm font-semibold text-foreground">
        {insufficient ? "Not enough evidence" : "Request could not be completed"}
      </h3>
      {response.answer_text ? (
        <p className="mt-1 whitespace-pre-wrap break-words text-sm leading-6 text-muted-foreground">
          {response.answer_text}
        </p>
      ) : null}
    </section>
  );
}

const FAILURE_GUIDANCE: Partial<Record<string, { title: string; detail: string }>> = {
  MODEL_UNAVAILABLE: {
    title: "The required local model is unavailable.",
    detail: "Try again after the model is available.",
  },
  MODEL_TIMEOUT: {
    title: "The model response timed out.",
    detail: "Try the request again. No action is shown as successful without backend confirmation.",
  },
  RAG_QUERY_FAILED: {
    title: "AAOS could not search academic documents.",
    detail: "The document search failed. Try again, or ask a question that does not need document evidence.",
  },
  NO_INDEXED_DOCUMENTS: {
    title: "This course has no indexed documents yet.",
    detail: "There is nothing to search for this course. Sync and ingest its materials first, or ask about another course.",
  },
  SUMMARY_NOT_PREPARED: {
    title: "This summary is not prepared yet.",
    detail: "Long documents are summarized ahead of time. Prepare summaries with \"aaos rag summarize --all\", then ask again.",
  },
  SUMMARY_UNVERIFIED: {
    title: "No summary point could be verified.",
    detail: "AAOS shows only points it can verify against the document. Ask a specific question about the document instead.",
  },
  TOOL_TIMEOUT: {
    title: "An academic data request timed out.",
    detail: "Try again. If this request could change a task or Calendar, reuse it from this page so its request ID stays the same.",
  },
  TOOL_OUTCOME_UNKNOWN: {
    title: "AAOS could not confirm an action result.",
    detail: "Check the current task or proposal status before retrying the action.",
  },
  TOOL_EXECUTION_IN_PROGRESS: {
    title: "An academic action is still in progress.",
    detail: "Check the current task or proposal status before retrying the action.",
  },
};

function FailureState({ response }: { response: AcademicAskResponse }) {
  const guidance = response.failure_code ? FAILURE_GUIDANCE[response.failure_code] : undefined;
  let title = guidance?.title ?? "AAOS could not complete this request.";
  let detail = guidance?.detail ?? "Try again. If a task action may have been in progress, keep the same request and retry it from this page.";

  if (response.status === "TIMED_OUT") {
    title = "AAOS reached its time limit.";
    detail = "Retrying the same question from this page reuses its request ID.";
  } else if (response.status === "CANCELLED") {
    title = "AAOS cancelled this request.";
    detail = "No success is shown without a confirmed backend result.";
  }

  return (
    <section
      aria-label="Ask request status"
      className="rounded-xl border border-warning/35 bg-warning-soft/70 px-4 py-3"
      role="status"
    >
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      <p className="mt-1 break-words text-sm text-muted-foreground">{detail}</p>
    </section>
  );
}

function GenericSource({ source }: { source: AcademicAskSource }) {
  return (
    <li className="min-w-0 rounded-xl border border-border bg-white/70 px-3 py-2.5">
      <p className="break-words text-sm font-medium text-foreground">
        {source.title || source.entity_kind}
      </p>
      <p className="mt-1 break-words text-xs text-muted-foreground">
        {source.entity_kind}
        {source.entity_id != null ? " · ID " + source.entity_id : ""}
        {source.source_turn != null ? " · conversation turn " + source.source_turn : ""}
        {source.from_snapshot_id != null && source.to_snapshot_id != null
          ? " · snapshots " + source.from_snapshot_id + " → " + source.to_snapshot_id
          : ""}
      </p>
      {source.changed_fields.length > 0 && source.change_kind == null ? (
        <p className="mt-1 break-words text-xs text-muted-foreground">
          Changed fields: {source.changed_fields.join(", ")}
        </p>
      ) : null}
    </li>
  );
}

function SourceList({
  sources,
  heading,
}: {
  sources: AcademicAskSource[];
  heading: string;
}) {
  if (sources.length === 0) return null;
  return (
    <section className="mt-4 border-t border-border pt-4">
      <h3 className="text-sm font-semibold text-foreground">{heading}</h3>
      <ul className="mt-2 space-y-2">
        {sources.map((source, index) => {
          const key = [
            source.tool_name,
            source.entity_kind,
            source.entity_id ?? "query",
            source.change_id ?? "",
            source.source_turn ?? index,
          ].join(":");
          if (source.entity_kind === "study_task" && source.study_task_status != null) {
            return <li key={key}><StudyTaskCard source={source} /></li>;
          }
          if (source.change_kind != null) {
            return <li key={key}><MoodleChangeCard source={source} /></li>;
          }
          return <GenericSource key={key} source={source} />;
        })}
      </ul>
    </section>
  );
}

export function AskResponseRenderer({ response }: { response: AcademicAskResponse }) {
  if (response.status === "REFUSED" || response.status === "INSUFFICIENT_EVIDENCE") {
    return <RefusalState response={response} />;
  }
  if (
    response.status === "FAILED" ||
    response.status === "CANCELLED" ||
    response.status === "TIMED_OUT"
  ) {
    return <FailureState response={response} />;
  }

  const ragProvenance: RagProvenance | null = response.rag_provenance
    && "citations" in response.rag_provenance
    ? response.rag_provenance
    : null;

  return (
    <div className="min-w-0 space-y-4">
      <AnswerText text={response.answer_text} />
      {response.calendar_proposal ? (
        <CalendarProposalCard proposal={response.calendar_proposal} />
      ) : null}
      {response.study_plan ? <StudyPlanView plan={response.study_plan} /> : null}
      {ragProvenance ? <RagCitationList provenance={ragProvenance} /> : null}
      <SourceList sources={response.sources} heading="Trusted sources" />
      <SourceList sources={response.context_sources} heading="Conversation context" />
      {!response.answer_text &&
      !response.calendar_proposal &&
      !response.study_plan &&
      !ragProvenance &&
      response.sources.length === 0 &&
      response.context_sources.length === 0 ? (
        <p className="text-sm text-muted-foreground">AAOS returned no additional content.</p>
      ) : null}
    </div>
  );
}
