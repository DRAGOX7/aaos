import type { components } from "@/types/api.generated";

import { createApiError } from "@/lib/api/errors";

export type AcademicAskRequest = components["schemas"]["AcademicAskRequest"];
export type AcademicAskResponse = components["schemas"]["AcademicAskResponse"];
export type AcademicAskSource = components["schemas"]["AcademicAskSource"];
export type CalendarProposal = components["schemas"]["CalendarProposalView"];
export type StudyPlan = components["schemas"]["StudyPlanView"];
export type RagProvenance = NonNullable<AcademicAskResponse["rag_provenance"]>;

export class MalformedAskResponseError extends Error {
  constructor() {
    super("AAOS returned a response that does not match the Ask API contract.");
    this.name = "MalformedAskResponseError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isBoundedString(value: unknown, maximum: number): value is string {
  return isString(value) && value.length <= maximum;
}

function isOptionalBoundedString(value: unknown, maximum: number): boolean {
  return value === undefined || value === null || isBoundedString(value, maximum);
}

function isInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value);
}

function isPositiveIntegerOrNull(value: unknown): boolean {
  return value === null || (isInteger(value) && value > 0);
}

const MOODLE_CHANGE_FIELDS = new Set([
  "title",
  "code",
  "due_at",
  "published_at",
  "course",
  "document_version",
]);
const PLAN_SOURCE_KINDS = new Set(["ASSIGNMENT", "STUDY_TASK", "MOODLE_CHANGE", "DOCUMENT"]);
const PLAN_SOURCE_TOOLS = new Set([
  "academic.list_assignments",
  "study_task.list",
  "moodle.list_changes",
  "rag.answer",
]);
const STUDY_ACTIVITY_TYPES = new Set([
  "ASSIGNMENT_WORK",
  "REVIEW",
  "READING",
  "PRACTICE",
  "PROJECT_WORK",
]);
const STUDY_RATIONALE_CODES = new Set([
  "DEADLINE_NEAR",
  "OPEN_TASK",
  "RECENT_MOODLE_CHANGE",
  "USER_FOCUS",
  "GENERAL_REVIEW",
]);
const IDENTIFIER_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const ASK_STATUSES = new Set([
  "ANSWERED",
  "REFUSED",
  "INSUFFICIENT_EVIDENCE",
  "FAILED",
  "CANCELLED",
  "TIMED_OUT",
  "CALENDAR_PROPOSAL_CREATED",
]);

const APPROVAL_STATES = new Set([
  "PENDING",
  "APPROVED",
  "CONSUMED",
  "REJECTED",
  "REVOKED",
  "EXPIRED",
]);

const TASK_STATES = new Set([
  "PREPARING",
  "PREPARATION_FAILED",
  "AWAITING_APPROVAL",
  "APPROVED",
  "CLAIMED",
  "EXECUTING",
  "UNKNOWN_OUTCOME",
  "CONFIRMED_SUCCESS",
  "CONFIRMED_FAILURE",
  "NOT_DISPATCHED",
  "REJECTED",
  "INVALIDATED",
  "EXPIRED",
  "REVOKED",
]);

const OPERATION_STATES = new Set([
  "CLAIMED",
  "EXECUTING",
  "NOT_DISPATCHED",
  "CONFIRMED_SUCCESS",
  "CONFIRMED_FAILURE",
  "UNKNOWN_OUTCOME",
  "RECONCILIATION_PENDING",
]);

function isSource(value: unknown): value is AcademicAskSource {
  if (!isRecord(value)) return false;
  if (!isBoundedString(value.tool_name, 64) || !isBoundedString(value.entity_kind, 32)) return false;
  if (value.entity_id !== null && (!isInteger(value.entity_id) || value.entity_id <= 0)) return false;
  if (
    !Array.isArray(value.changed_fields) ||
    value.changed_fields.length > 6 ||
    !value.changed_fields.every((field) => isString(field) && MOODLE_CHANGE_FIELDS.has(field))
  ) return false;
  if (!isOptionalBoundedString(value.title, 2048)) return false;
  if (!isOptionalBoundedString(value.query_filter, 4096)) return false;
  if (!isOptionalBoundedString(value.source_ref, 16)) return false;
  if (!isOptionalBoundedString(value.change_id, 255)) return false;
  if (!isOptionalBoundedString(value.change_kind, 16)) return false;
  if (!isOptionalBoundedString(value.before_due_at, 64) || !isOptionalBoundedString(value.after_due_at, 64)) return false;
  if (!isOptionalBoundedString(value.study_task_status, 16)) return false;
  if (!isOptionalBoundedString(value.study_task_priority, 16)) return false;
  if (!isOptionalBoundedString(value.study_task_due_at, 64)) return false;
  if (!isOptionalBoundedString(value.study_task_course_title, 500)) return false;
  if (!isOptionalBoundedString(value.study_task_assignment_title, 500)) return false;
  if (
    ![
      value.course_id,
      value.from_snapshot_id,
      value.to_snapshot_id,
      value.study_task_course_id,
      value.study_task_assignment_id,
    ].every((item) => item === undefined || isPositiveIntegerOrNull(item))
  ) return false;
  if (
    value.source_turn !== undefined &&
    value.source_turn !== null &&
    (!isInteger(value.source_turn) || value.source_turn < 1 || value.source_turn > 8)
  ) return false;
  if (value.change_kind != null && !["ADDED", "REMOVED", "MODIFIED"].includes(String(value.change_kind))) {
    return false;
  }
  if (value.study_task_status != null && !["OPEN", "COMPLETED"].includes(String(value.study_task_status))) {
    return false;
  }
  if (value.study_task_priority != null && !["LOW", "NORMAL", "HIGH"].includes(String(value.study_task_priority))) {
    return false;
  }
  return true;
}

function isRagCitation(value: unknown): boolean {
  if (!isRecord(value)) return false;
  if (
    !isBoundedString(value.claim_id, 255) ||
    value.claim_id.length === 0 ||
    !isInteger(value.document_version_id) ||
    value.document_version_id <= 0 ||
    !isBoundedString(value.document_title, 2048) ||
    value.document_title.length === 0
  ) {
    return false;
  }
  if ("page_start" in value || "page_end" in value) {
    return !("unit_start" in value)
      && isInteger(value.page_start) && value.page_start > 0
      && isInteger(value.page_end) && value.page_end >= value.page_start;
  }
  if (
    !isInteger(value.unit_start) ||
    value.unit_start <= 0 ||
    !isInteger(value.unit_end) ||
    value.unit_end < value.unit_start ||
    !isOptionalBoundedString(value.heading, 2048)
  ) {
    return false;
  }
  const coordinates = [value.table_index, value.row_index, value.column_index];
  const coordinateShapeIsValid = coordinates.every((item) => item == null || (isInteger(item) && item >= 0));
  const coordinatesAreConsistent = coordinates.every((item) => item == null)
    || coordinates.every((item) => item != null);
  return coordinateShapeIsValid && coordinatesAreConsistent;
}

function isRagProvenance(value: unknown): value is RagProvenance {
  if (!isRecord(value) || !isInteger(value.generation_id) || value.generation_id <= 0) return false;
  if (!isInteger(value.content_revision) || value.content_revision < 0) return false;

  if ("citations" in value) {
    return isBoundedString(value.answer_text, 16384)
      && value.answer_text.length > 0
      && isBoundedString(value.rendered_text, 16384)
      && value.rendered_text.length > 0
      && isBoundedString(value.language, 32)
      && value.language.length > 0
      && ["ANSWER", "PARTIAL_ANSWER", "INSUFFICIENT_EVIDENCE"].includes(String(value.status))
      && Array.isArray(value.citations)
      && value.citations.length <= 64
      && value.citations.every(isRagCitation);
  }
  return isInteger(value.citation_count) && value.citation_count >= 0 && value.citation_count <= 64;
}

function isCalendarProposal(value: unknown): value is CalendarProposal {
  if (!isRecord(value)) return false;
  return isBoundedString(value.proposal_id, 128)
    && IDENTIFIER_PATTERN.test(value.proposal_id)
    && isBoundedString(value.approval_id, 128)
    && IDENTIFIER_PATTERN.test(value.approval_id)
    && value.operation === "CREATE_EVENT"
    && isBoundedString(value.summary, 200)
    && value.summary.length > 0
    && isBoundedString(value.start, 64)
    && value.start.length > 0
    && isBoundedString(value.end, 64)
    && value.end.length > 0
    && isBoundedString(value.timezone, 128)
    && value.timezone.length > 0
    && (value.source_entity_kind === "assignment" || value.source_entity_kind === "study_task")
    && isInteger(value.source_entity_id)
    && value.source_entity_id > 0
    && APPROVAL_STATES.has(String(value.approval_status))
    && TASK_STATES.has(String(value.task_status))
    && (value.operation_state == null || OPERATION_STATES.has(String(value.operation_state)));
}

function isStudyPlanItem(value: unknown): boolean {
  return isRecord(value)
    && isBoundedString(value.study_date, 10)
    && /^\d{4}-\d{2}-\d{2}$/.test(value.study_date)
    && isInteger(value.course_id)
    && value.course_id > 0
    && isPositiveIntegerOrNull(value.assignment_id)
    && isPositiveIntegerOrNull(value.study_task_id)
    && STUDY_ACTIVITY_TYPES.has(String(value.activity_type))
    && isInteger(value.duration_minutes)
    && value.duration_minutes >= 15
    && value.duration_minutes <= 180
    && STUDY_RATIONALE_CODES.has(String(value.rationale_code))
    && isOptionalBoundedString(value.focus, 120)
    && typeof value.estimated_duration === "boolean"
    && Array.isArray(value.source_refs)
    && value.source_refs.length <= 4
    && value.source_refs.every(isStudyPlanSource);
}

function isStudyPlanSource(value: unknown): boolean {
  if (!isRecord(value)) return false;
  return PLAN_SOURCE_KINDS.has(String(value.kind))
    && PLAN_SOURCE_TOOLS.has(String(value.tool_name))
    && (value.entity_id === undefined || isPositiveIntegerOrNull(value.entity_id))
    && (value.course_id === undefined || isPositiveIntegerOrNull(value.course_id))
    && isOptionalBoundedString(value.change_id, 255)
    && (value.from_snapshot_id === undefined || isPositiveIntegerOrNull(value.from_snapshot_id))
    && (value.to_snapshot_id === undefined || isPositiveIntegerOrNull(value.to_snapshot_id))
    && (value.document_version_id === undefined || isPositiveIntegerOrNull(value.document_version_id))
    && isOptionalBoundedString(value.citation_location, 256)
    && isOptionalBoundedString(value.claim_id, 255);
}

function isStudyPlanObligation(value: unknown): boolean {
  return isRecord(value)
    && isInteger(value.assignment_id)
    && value.assignment_id > 0
    && isInteger(value.course_id)
    && value.course_id > 0
    && isBoundedString(value.title, 512)
    && isOptionalBoundedString(value.due_at, 64)
    && typeof value.overdue === "boolean"
    && isStudyPlanSource(value.source);
}

function isStudyPlanTask(value: unknown): boolean {
  return isRecord(value)
    && isInteger(value.task_id)
    && value.task_id > 0
    && isBoundedString(value.title, 512)
    && ["OPEN", "COMPLETED"].includes(String(value.status))
    && ["LOW", "NORMAL", "HIGH"].includes(String(value.priority))
    && isPositiveIntegerOrNull(value.course_id)
    && isPositiveIntegerOrNull(value.assignment_id)
    && isOptionalBoundedString(value.due_at, 64)
    && isStudyPlanSource(value.source);
}

function isStudyPlanMoodleChange(value: unknown): boolean {
  return isRecord(value)
    && isBoundedString(value.change_id, 255)
    && value.change_id.length > 0
    && isPositiveIntegerOrNull(value.assignment_id)
    && isPositiveIntegerOrNull(value.course_id)
    && ["ADDED", "REMOVED", "MODIFIED"].includes(String(value.change_kind))
    && isOptionalBoundedString(value.title, 512)
    && Array.isArray(value.changed_fields)
    && value.changed_fields.length <= 6
    && value.changed_fields.every((field) => isString(field) && MOODLE_CHANGE_FIELDS.has(field))
    && isOptionalBoundedString(value.before_due_at, 64)
    && isOptionalBoundedString(value.after_due_at, 64)
    && isInteger(value.from_snapshot_id)
    && value.from_snapshot_id > 0
    && isInteger(value.to_snapshot_id)
    && value.to_snapshot_id > 0
    && isBoundedString(value.detected_at, 64)
    && isStudyPlanSource(value.source);
}

function isStudyPlan(value: unknown): value is StudyPlan {
  return isRecord(value)
    && isBoundedString(value.plan_id, 36)
    && UUID_PATTERN.test(value.plan_id)
    && isBoundedString(value.planning_window_start, 10)
    && /^\d{4}-\d{2}-\d{2}$/.test(value.planning_window_start)
    && isBoundedString(value.planning_window_end, 10)
    && /^\d{4}-\d{2}-\d{2}$/.test(value.planning_window_end)
    && isBoundedString(value.timezone, 128)
    && value.timezone.length > 0
    && isBoundedString(value.generated_at, 64)
    && value.calendar_conflict_aware === false
    && Array.isArray(value.assumptions)
    && value.assumptions.length <= 8
    && value.assumptions.every((item) => isBoundedString(item, 2048))
    && Array.isArray(value.warnings)
    && value.warnings.length <= 12
    && value.warnings.every((item) => isBoundedString(item, 2048))
    && Array.isArray(value.obligations)
    && value.obligations.length <= 30
    && value.obligations.every(isStudyPlanObligation)
    && Array.isArray(value.study_tasks)
    && value.study_tasks.length <= 50
    && value.study_tasks.every(isStudyPlanTask)
    && Array.isArray(value.moodle_changes)
    && value.moodle_changes.length <= 5
    && value.moodle_changes.every(isStudyPlanMoodleChange)
    && Array.isArray(value.items)
    && value.items.length <= 12
    && value.items.every(isStudyPlanItem)
    && Array.isArray(value.rag_provenance)
    && value.rag_provenance.length <= 64
    && value.rag_provenance.every(isStudyPlanSource)
    && (
      value.available_minutes === undefined ||
      value.available_minutes === null ||
      (isInteger(value.available_minutes) && value.available_minutes > 0)
    )
    && (
      value.max_session_minutes === undefined ||
      value.max_session_minutes === null ||
      (isInteger(value.max_session_minutes) && value.max_session_minutes > 0 && value.max_session_minutes <= 180)
    )
    && (value.moodle_from_snapshot_id === undefined || isPositiveIntegerOrNull(value.moodle_from_snapshot_id))
    && (value.moodle_to_snapshot_id === undefined || isPositiveIntegerOrNull(value.moodle_to_snapshot_id));
}

export function isAcademicAskResponse(value: unknown): value is AcademicAskResponse {
  if (!isRecord(value) || !ASK_STATUSES.has(String(value.status))) return false;
  if (!isInteger(value.step_count) || value.step_count < 0 || value.step_count > 8) return false;
  if (!Array.isArray(value.sources) || !value.sources.every(isSource)) return false;
  if (!Array.isArray(value.context_sources) || !value.context_sources.every(isSource)) return false;
  if (!isOptionalBoundedString(value.answer_text, 16384)) return false;
  if (!isOptionalBoundedString(value.failure_code, 64)) return false;
  if (!isOptionalBoundedString(value.failure_message, 1024)) return false;
  if (!isOptionalBoundedString(value.refusal_code, 64)) return false;
  if (value.request_id != null && (!isString(value.request_id) || !UUID_PATTERN.test(value.request_id))) return false;
  if (value.conversation_id != null && (!isString(value.conversation_id) || !UUID_PATTERN.test(value.conversation_id))) return false;
  if (value.rag_provenance != null && !isRagProvenance(value.rag_provenance)) return false;
  if (value.calendar_proposal != null && !isCalendarProposal(value.calendar_proposal)) return false;
  if (value.study_plan != null && !isStudyPlan(value.study_plan)) return false;
  return true;
}

export async function fetchAcademicAsk(
  request: AcademicAskRequest,
  signal?: AbortSignal,
): Promise<AcademicAskResponse> {
  const response = await fetch("/api/v1/ask", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
    signal,
  });

  if (!response.ok) {
    throw await createApiError(response, "ask");
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new MalformedAskResponseError();
  }
  if (!isAcademicAskResponse(payload)) {
    throw new MalformedAskResponseError();
  }
  return payload;
}
