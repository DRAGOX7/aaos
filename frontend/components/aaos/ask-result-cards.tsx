"use client";

import { useState } from "react";
import { FileText } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api/errors";
import {
  approveApproval,
  fetchApproval,
  fetchTask,
  rejectApproval,
  type ApprovalDetail,
  type TaskDetail,
} from "@/lib/api/tasks";
import type {
  AcademicAskSource,
  CalendarProposal,
  RagProvenance,
  StudyPlan,
} from "@/lib/api/ask";

function formatDate(value: string): string {
  const parsed = new Date(value + "T00:00:00Z");
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeZone: "UTC",
  }).format(parsed);
}

function formatTimestampAsProvided(value: string): string {
  return value.replace("T", " ");
}

function statusLabel(value: string): string {
  return value.replaceAll("_", " ").toLocaleLowerCase();
}

function fieldLabel(field: string): string {
  switch (field) {
    case "title": return "Title";
    case "code": return "Course code";
    case "due_at": return "Deadline";
    case "published_at": return "Published date";
    case "course": return "Course";
    case "document_version": return "Document version";
    default: return field;
  }
}

export function RagCitationList({ provenance }: { provenance: RagProvenance }) {
  if (!("citations" in provenance) || provenance.citations.length === 0) return null;

  return (
    <section aria-label="RAG citations" className="mt-5 border-t border-border pt-4">
      <h3 className="text-sm font-semibold text-foreground">Sources</h3>
      <ul className="mt-2 space-y-2">
        {provenance.citations.map((citation, index) => (
          <li
            className="flex min-w-0 gap-3 rounded-xl border border-border bg-white/70 px-3 py-2.5 text-sm"
            key={citation.claim_id + ":" + index}
          >
            <FileText aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-primary" />
            <span className="min-w-0">
              <span className="block break-words font-medium text-foreground">
                {citation.document_title}
              </span>
              <span className="block break-words text-muted-foreground">
                {"page_start" in citation
                  ? citation.page_start === citation.page_end
                    ? "Page " + citation.page_start
                    : "Pages " + citation.page_start + "–" + citation.page_end
                    : citation.unit_start === citation.unit_end
                      ? "Unit " + citation.unit_start
                      : "Units " + citation.unit_start + "–" + citation.unit_end}
                {" · Version " + citation.document_version_id}
                {"heading" in citation && citation.heading ? " · " + citation.heading : ""}
                {"table_index" in citation && citation.table_index != null
                  ? " · Table " + (citation.table_index + 1)
                    + ", row " + ((citation.row_index ?? 0) + 1)
                    + ", column " + ((citation.column_index ?? 0) + 1)
                  : ""}
                {"from_past_course" in provenance && provenance.from_past_course
                  ? " · past course"
                  : ""}
              </span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function MoodleChangeCard({ source }: { source: AcademicAskSource }) {
  const fields = source.changed_fields.map(fieldLabel).join(", ");
  return (
    <article className="min-w-0 rounded-xl border border-border bg-white/75 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="min-w-0 break-words font-semibold text-foreground">
          {source.title || "Moodle change"}
        </h3>
        {source.change_kind ? (
          <span className="rounded-md border border-teal/30 bg-teal-soft px-2 py-1 text-xs font-semibold text-teal">
            {source.change_kind}
          </span>
        ) : null}
      </div>
      <dl className="mt-3 grid min-w-0 gap-x-4 gap-y-2 text-sm sm:grid-cols-2">
        {fields ? (
          <div className="min-w-0">
            <dt className="text-muted-foreground">Changed fields</dt>
            <dd className="break-words font-medium text-foreground">{fields}</dd>
          </div>
        ) : null}
        {source.before_due_at || source.after_due_at ? (
          <div className="min-w-0">
            <dt className="text-muted-foreground">Deadline</dt>
            <dd className="break-words font-medium text-foreground">
              {source.before_due_at ? formatTimestampAsProvided(source.before_due_at) : "No previous deadline"}
              {" → "}
              {source.after_due_at ? formatTimestampAsProvided(source.after_due_at) : "No current deadline"}
            </dd>
          </div>
        ) : null}
        {source.from_snapshot_id != null && source.to_snapshot_id != null ? (
          <div className="min-w-0">
            <dt className="text-muted-foreground">Moodle snapshots</dt>
            <dd className="break-words text-foreground">
              {source.from_snapshot_id} → {source.to_snapshot_id}
            </dd>
          </div>
        ) : null}
      </dl>
    </article>
  );
}

export function StudyTaskCard({ source }: { source: AcademicAskSource }) {
  return (
    <article className="min-w-0 rounded-xl border border-border bg-white/75 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="min-w-0 break-words font-semibold text-foreground">
          {source.title || "Study task"}
        </h3>
        {source.study_task_status ? (
          <span className="rounded-md border border-border bg-muted px-2 py-1 text-xs font-semibold text-foreground">
            {source.study_task_status}
          </span>
        ) : null}
      </div>
      <dl className="mt-3 grid min-w-0 gap-x-4 gap-y-2 text-sm sm:grid-cols-2">
        {source.study_task_priority ? (
          <div>
            <dt className="text-muted-foreground">Priority</dt>
            <dd className="font-medium text-foreground">{source.study_task_priority}</dd>
          </div>
        ) : null}
        {source.study_task_due_at ? (
          <div className="min-w-0">
            <dt className="text-muted-foreground">Due</dt>
            <dd className="break-words text-foreground">
              {formatTimestampAsProvided(source.study_task_due_at)}
            </dd>
          </div>
        ) : null}
        {source.study_task_course_title ? (
          <div className="min-w-0">
            <dt className="text-muted-foreground">Course</dt>
            <dd className="break-words text-foreground">{source.study_task_course_title}</dd>
          </div>
        ) : source.study_task_course_id != null ? (
          <div>
            <dt className="text-muted-foreground">Course ID</dt>
            <dd className="text-foreground">{source.study_task_course_id}</dd>
          </div>
        ) : null}
        {source.study_task_assignment_title ? (
          <div className="min-w-0">
            <dt className="text-muted-foreground">Assignment</dt>
            <dd className="break-words text-foreground">{source.study_task_assignment_title}</dd>
          </div>
        ) : source.study_task_assignment_id != null ? (
          <div>
            <dt className="text-muted-foreground">Assignment ID</dt>
            <dd className="text-foreground">{source.study_task_assignment_id}</dd>
          </div>
        ) : null}
      </dl>
    </article>
  );
}

function getSafeActionMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "AAOS could not confirm the approval state.";
}

function proposalState(
  proposal: CalendarProposal,
  approval: ApprovalDetail | null,
  task: TaskDetail | null,
): { label: string; detail: string } {
  const approvalState = approval?.status ?? proposal.approval_status;
  const operationState = task?.operation_state ?? approval?.operation_state ?? proposal.operation_state;
  const taskState = task?.status ?? proposal.task_status;

  if (operationState === "CONFIRMED_SUCCESS" && taskState === "CONFIRMED_SUCCESS") {
    return { label: "Executed", detail: "AAOS confirmed that the event was created." };
  }
  if (operationState === "CONFIRMED_FAILURE" || taskState === "CONFIRMED_FAILURE") {
    return { label: "Execution failed", detail: "Approval did not result in a confirmed Calendar event." };
  }
  if (taskState === "INVALIDATED") {
    return {
      label: "Invalidated · not dispatched",
      detail: "The proposal was invalidated before execution; no Calendar event was created.",
    };
  }
  if (taskState === "NOT_DISPATCHED" || operationState === "NOT_DISPATCHED") {
    return { label: "Not dispatched", detail: "AAOS confirmed that the Calendar event was not created." };
  }
  if (operationState === "UNKNOWN_OUTCOME" || taskState === "UNKNOWN_OUTCOME") {
    return { label: "Outcome unknown", detail: "AAOS could not confirm whether the Calendar event was created." };
  }
  if (approvalState === "REJECTED" || taskState === "REJECTED") {
    return { label: "Rejected", detail: "This proposal was rejected." };
  }
  if (approvalState === "EXPIRED" || taskState === "EXPIRED") {
    return { label: "Expired", detail: "This proposal has expired." };
  }
  if (approvalState === "REVOKED" || taskState === "REVOKED") {
    return { label: "Revoked", detail: "This proposal is no longer available." };
  }
  if (approvalState === "PENDING" && taskState === "AWAITING_APPROVAL") {
    return { label: "Awaiting approval", detail: "This event has not been added to Calendar." };
  }
  if (approvalState === "APPROVED" && operationState === "EXECUTING") {
    return { label: "Approved · executing", detail: "Approval is recorded; Calendar execution is still in progress." };
  }
  if (approvalState === "APPROVED" || approvalState === "CONSUMED") {
    return { label: "Approved", detail: "Approval is recorded. Calendar execution is not yet confirmed." };
  }
  return {
    label: statusLabel(taskState),
    detail: "The proposal state is shown as returned by AAOS.",
  };
}

const TERMINAL_TASK_STATES = new Set([
  "PREPARATION_FAILED",
  "CONFIRMED_SUCCESS",
  "CONFIRMED_FAILURE",
  "NOT_DISPATCHED",
  "REJECTED",
  "INVALIDATED",
  "EXPIRED",
  "REVOKED",
]);

const TERMINAL_OPERATION_STATES = new Set([
  "NOT_DISPATCHED",
  "CONFIRMED_SUCCESS",
  "CONFIRMED_FAILURE",
]);

export function CalendarProposalCard({ proposal }: { proposal: CalendarProposal }) {
  const [approval, setApproval] = useState<ApprovalDetail | null>(null);
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [pending, setPending] = useState(false);
  const [decisionUncertain, setDecisionUncertain] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const currentState = decisionUncertain
    ? {
        label: "Status not confirmed",
        detail: "AAOS could not confirm whether the last decision was recorded.",
      }
    : proposalState(proposal, approval, task);
  const mayDecide =
    proposal.operation === "CREATE_EVENT" &&
    (approval?.status ?? proposal.approval_status) === "PENDING" &&
    (task?.status ?? proposal.task_status) === "AWAITING_APPROVAL" &&
    !decisionUncertain;
  const approvalStatus = approval?.status ?? proposal.approval_status;
  const executionStatusNeedsRefresh =
    (approvalStatus === "APPROVED" || approvalStatus === "CONSUMED") &&
    (!task ||
      !TERMINAL_TASK_STATES.has(task.status) ||
      (task.operation_state != null && !TERMINAL_OPERATION_STATES.has(task.operation_state)));

  async function decide(decision: "approve" | "reject") {
    if (pending || !mayDecide) return;
    setPending(true);
    setError(null);
    try {
      const result = decision === "approve"
        ? await approveApproval(proposal.approval_id)
        : await rejectApproval(proposal.approval_id);
      setApproval(result);
      setDecisionUncertain(false);
      try {
        setTask(await fetchTask(result.task_id));
      } catch {
        setError("The decision was recorded, but execution status could not be refreshed.");
      }
    } catch (caught) {
      try {
        const currentApproval = await fetchApproval(proposal.approval_id);
        setApproval(currentApproval);
        setDecisionUncertain(false);
        let taskRefreshFailed = false;
        try {
          setTask(await fetchTask(currentApproval.task_id));
        } catch {
          taskRefreshFailed = true;
          if (currentApproval.status === "PENDING") {
            setDecisionUncertain(true);
            setError("AAOS confirmed the approval remains pending, but could not refresh the task state. Check again before deciding.");
          } else {
            setError("The approval state was refreshed, but Calendar execution status could not be refreshed.");
          }
        }
        if (currentApproval.status === "PENDING" && !taskRefreshFailed) {
          setError(getSafeActionMessage(caught));
        } else if (currentApproval.status !== "PENDING") {
          setError("The decision response was not received. AAOS refreshed the current approval state.");
        }
      } catch {
        setDecisionUncertain(true);
        setError("AAOS could not confirm whether this decision was recorded. Check the proposal status before trying again.");
      }
    } finally {
      setPending(false);
    }
  }

  async function refreshProposalState() {
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      const currentApproval = await fetchApproval(proposal.approval_id);
      setApproval(currentApproval);
      setDecisionUncertain(false);
      try {
        setTask(await fetchTask(currentApproval.task_id));
      } catch {
        if (currentApproval.status === "PENDING") {
          setDecisionUncertain(true);
          setError("AAOS confirmed the approval remains pending, but could not refresh the task state. Check again before deciding.");
        } else {
          setError("The approval state was refreshed, but Calendar execution status could not be refreshed.");
        }
      }
    } catch {
      setDecisionUncertain(true);
      setError("AAOS could not confirm the proposal state. Check again before making another decision.");
    } finally {
      setPending(false);
    }
  }

  return (
    <section aria-labelledby={"calendar-proposal-" + proposal.proposal_id} className="min-w-0 rounded-2xl border border-border bg-white p-4 shadow-[var(--aaos-shadow-card)] sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 id={"calendar-proposal-" + proposal.proposal_id} className="text-base font-semibold text-foreground">
            Calendar event proposal
          </h3>
          <p className="mt-1 text-sm text-muted-foreground">{currentState.detail}</p>
        </div>
        <span className="rounded-md border border-border bg-muted px-2.5 py-1.5 text-xs font-semibold text-foreground">
          {currentState.label}
        </span>
      </div>

      <dl className="mt-4 grid min-w-0 gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
        <div className="min-w-0 sm:col-span-2">
          <dt className="text-muted-foreground">Title</dt>
          <dd className="break-words font-medium text-foreground">{proposal.summary}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-muted-foreground">Starts</dt>
          <dd className="break-words text-foreground">{proposal.start}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-muted-foreground">Ends</dt>
          <dd className="break-words text-foreground">{proposal.end}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-muted-foreground">Timezone</dt>
          <dd className="break-words text-foreground">{proposal.timezone}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-muted-foreground">Linked {proposal.source_entity_kind}</dt>
          <dd className="break-words text-foreground">ID {proposal.source_entity_id}</dd>
        </div>
        {task?.failure_code ? (
          <div className="min-w-0 sm:col-span-2">
            <dt className="text-muted-foreground">Failure code</dt>
            <dd className="break-words text-foreground">{task.failure_code}</dd>
          </div>
        ) : null}
      </dl>

      {error ? <p className="mt-3 text-sm text-destructive" role="alert">{error}</p> : null}
      <p className="sr-only" aria-live="polite">
        {pending ? "Submitting Calendar decision." : ""}
      </p>

      {mayDecide ? (
        <div className="mt-5 flex flex-wrap justify-end gap-2 border-t border-border pt-4">
          <Button
            type="button"
            variant="outline"
            disabled={pending}
            onClick={() => void decide("reject")}
          >
            {pending ? "Updating…" : "Reject"}
          </Button>
          <Button
            type="button"
            disabled={pending}
            onClick={() => void decide("approve")}
          >
            {pending ? "Updating…" : "Approve"}
          </Button>
        </div>
      ) : null}
      {decisionUncertain || executionStatusNeedsRefresh ? (
        <div className="mt-5 flex justify-end border-t border-border pt-4">
          <Button type="button" variant="outline" disabled={pending} onClick={() => void refreshProposalState()}>
            {pending ? "Checking…" : "Check proposal status"}
          </Button>
        </div>
      ) : null}
    </section>
  );
}

type PlanSource = StudyPlan["rag_provenance"][number];

function StudyPlanSourceList({ sources }: { sources: PlanSource[] }) {
  if (sources.length === 0) return null;
  return (
    <section className="mt-4 border-t border-border pt-3">
      <h4 className="text-sm font-semibold text-foreground">Plan provenance</h4>
      <ul className="mt-2 space-y-1 text-sm text-muted-foreground">
        {sources.map((source, index) => (
          <li key={source.claim_id ?? source.change_id ?? source.entity_id ?? index} className="break-words">
            {source.kind}
            {source.document_version_id ? " · document version " + source.document_version_id : ""}
            {source.citation_location ? " · " + source.citation_location.replace("page:", "page ").replace("unit:", "unit ") : ""}
            {source.change_id ? " · change " + source.change_id : ""}
            {source.from_snapshot_id && source.to_snapshot_id
              ? " · snapshots " + source.from_snapshot_id + " → " + source.to_snapshot_id
              : ""}
          </li>
        ))}
      </ul>
    </section>
  );
}

function StudyPlanMoodleChanges({ plan }: { plan: StudyPlan }) {
  if (plan.moodle_changes.length === 0) return null;
  return (
    <section className="mt-4">
      <h4 className="text-sm font-semibold text-foreground">Moodle changes</h4>
      <ul className="mt-2 space-y-2">
        {plan.moodle_changes.map((change) => (
          <li key={change.change_id} className="min-w-0 rounded-xl border border-border bg-white/70 px-3 py-2.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="break-words font-medium text-foreground">
                {change.title || "Moodle change"}
              </span>
              <span className="rounded-md border border-border px-2 py-0.5 text-xs font-semibold text-foreground">
                {change.change_kind}
              </span>
            </div>
            {change.changed_fields.length ? (
              <p className="mt-1 break-words text-sm text-muted-foreground">
                Changed: {change.changed_fields.map(fieldLabel).join(", ")}
              </p>
            ) : null}
            {change.before_due_at || change.after_due_at ? (
              <p className="mt-1 break-words text-sm text-foreground">
                {change.before_due_at ? formatTimestampAsProvided(change.before_due_at) : "No previous deadline"}
                {" → "}
                {change.after_due_at ? formatTimestampAsProvided(change.after_due_at) : "No current deadline"}
              </p>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function StudyPlanView({ plan }: { plan: StudyPlan }) {
  const dateGroups = new Map<string, StudyPlan["items"]>();
  for (const item of plan.items) {
    const group = dateGroups.get(item.study_date) ?? [];
    group.push(item);
    dateGroups.set(item.study_date, group);
  }

  return (
    <section aria-labelledby={"study-plan-" + plan.plan_id} className="min-w-0 rounded-2xl border border-border bg-white p-4 shadow-[var(--aaos-shadow-card)] sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 id={"study-plan-" + plan.plan_id} className="text-base font-semibold text-foreground">
            Study plan
          </h3>
          <p className="mt-1 break-words text-sm text-muted-foreground">
            {formatDate(plan.planning_window_start)} – {formatDate(plan.planning_window_end)} · {plan.timezone}
          </p>
        </div>
        <span className="rounded-md border border-primary/20 bg-primary-soft px-2.5 py-1.5 text-xs font-semibold text-primary">
          Recommendations
        </span>
      </div>

      <div className="mt-4 grid min-w-0 gap-4 lg:grid-cols-2">
        <section className="min-w-0">
          <h4 className="text-sm font-semibold text-foreground">Current facts</h4>
          <p className="mt-1 text-xs text-muted-foreground">Trusted assignment and StudyTask records</p>
          {plan.obligations.length === 0 && plan.study_tasks.length === 0 ? (
            <p className="mt-2 text-sm text-muted-foreground">No current obligations or StudyTasks were found.</p>
          ) : (
            <ul className="mt-2 space-y-2">
              {plan.obligations.map((fact) => (
                <li key={"assignment-" + fact.assignment_id} className="min-w-0 rounded-lg border border-border bg-muted/50 px-3 py-2.5">
                  <p className="break-words text-sm font-medium text-foreground">{fact.title}</p>
                  <p className="mt-1 break-words text-xs text-muted-foreground">
                    Course ID {fact.course_id}
                    {fact.due_at ? " · due " + formatTimestampAsProvided(fact.due_at) : ""}
                    {fact.overdue ? " · overdue" : ""}
                  </p>
                </li>
              ))}
              {plan.study_tasks.map((task) => (
                <li key={"task-" + task.task_id} className="min-w-0 rounded-lg border border-border bg-muted/50 px-3 py-2.5">
                  <p className="break-words text-sm font-medium text-foreground">{task.title}</p>
                  <p className="mt-1 break-words text-xs text-muted-foreground">
                    {task.status} · {task.priority}
                    {task.course_id != null ? " · course " + task.course_id : ""}
                    {task.assignment_id != null ? " · assignment " + task.assignment_id : ""}
                  </p>
                </li>
              ))}
            </ul>
          )}
          <StudyPlanMoodleChanges plan={plan} />
        </section>

        <section className="min-w-0">
          <h4 className="text-sm font-semibold text-foreground">Recommended study blocks</h4>
          <p className="mt-1 text-xs text-muted-foreground">
            Dates and durations are recommendations, not university requirements.
          </p>
          {plan.items.length === 0 ? (
            <p className="mt-2 text-sm text-muted-foreground">No recommended study blocks for this window.</p>
          ) : (
            <ol className="mt-2 space-y-3">
              {[...dateGroups.entries()].map(([date, items]) => (
                <li key={date} className="min-w-0 rounded-lg border border-border bg-white/75 p-3">
                  <h5 className="text-sm font-semibold text-foreground">{formatDate(date)}</h5>
                  <ul className="mt-2 space-y-2">
                    {items.map((item, index) => (
                      <li key={date + "-" + index} className="min-w-0 border-t border-border pt-2 first:border-0 first:pt-0">
                        <p className="break-words text-sm font-medium text-foreground">
                          Course ID {item.course_id}
                          {item.assignment_id != null ? " · assignment " + item.assignment_id : ""}
                          {item.study_task_id != null ? " · StudyTask " + item.study_task_id : ""}
                        </p>
                        {item.focus ? <p className="mt-1 break-words text-sm text-muted-foreground">{item.focus}</p> : null}
                        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                          <span>{item.duration_minutes} minutes</span>
                          {item.estimated_duration ? (
                            <span className="rounded border border-warning/30 bg-warning-soft px-1.5 py-0.5 font-medium text-foreground">
                              Estimated
                            </span>
                          ) : null}
                          <span>{item.rationale_code.replaceAll("_", " ").toLocaleLowerCase()}</span>
                        </div>
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>

      <div className="mt-4 space-y-2 border-t border-border pt-3">
        {!plan.calendar_conflict_aware ? (
          <p className="rounded-lg border border-warning/30 bg-warning-soft px-3 py-2 text-sm text-foreground">
            Calendar conflicts were not checked.
          </p>
        ) : null}
        {plan.assumptions.map((assumption, index) => (
          <p key={"assumption-" + index} className="break-words text-sm text-muted-foreground">
            Assumption: {assumption}
          </p>
        ))}
        {plan.warnings.map((warning, index) => (
          <p key={"warning-" + index} className="break-words text-sm text-muted-foreground">
            {warning}
          </p>
        ))}
      </div>
      <StudyPlanSourceList sources={plan.rag_provenance} />
    </section>
  );
}
