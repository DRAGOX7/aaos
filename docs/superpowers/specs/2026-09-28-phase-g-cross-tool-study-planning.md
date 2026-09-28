# Phase G: Cross-Tool Study Planning

## Objective and authority

Phase G adds bounded study-plan recommendations to the existing `/api/v1/ask` flow. A generated plan is a response artifact only. Planning has no task, Calendar, Moodle, database, shell, or network write authority. A plan never creates a task or Calendar proposal. Explicit later write requests continue through the accepted Phase C and Phase D paths.

The Python layer owns all trusted academic facts, time interpretation, scope resolution, provenance, and constraint validation. The model returns only a closed list of recommendation items. The returned `StudyPlan` separates current facts from recommendations.

## Accepted inputs and bounds

- Resolve requested courses only against the trusted active course reader and existing bounded conversation entities. Ambiguous names and unresolved ordinal references fail safely.
- Read at most 50 current assignments, 50 StudyTasks, and 5 Moodle change records. If an unscoped assignment result exceeds the 30-obligation planning cap, require a narrower course scope instead of silently planning a truncated set.
- Consider at most 12 selected courses, 30 obligations, 50 task records, and 5 change records. Current assignments define active obligations; removed Moodle assignments are retained as change facts but cannot become plan targets.
- Accept windows of at most 14 local calendar days. `tonight` means the current local date, `this week` means the remaining dates through Sunday, `next three days` includes today and the following two dates, and `this weekend` means the next Saturday and Sunday. A missing window defaults to the next seven local dates and is recorded as an assumption.
- Use the configured AAOS IANA calendar timezone. If it is absent or invalid, do not infer UTC; return a safe clarification/refusal. Plan items are date-level recommendations, with no start/end time because availability is unavailable.
- Bound planning input to 16 KiB, structured model output to 8 KiB, each output to 12 items, each duration to 15–180 minutes, and explicit total available hours to 16. User constraints are parsed by Python; absent constraints are never invented as preferences.
- Calendar supports create-with-approval only. Its known-ID `get_event` reconciliation read is not an availability reader. The plan must say `calendar_conflict_aware=false` and carry a warning that conflicts were not checked.

## Structured model and trusted plan

The model schema permits only recommendation items containing a local date, canonical course/assignment/task IDs or null, one closed activity enum, a bounded duration, and a closed rationale code. It cannot supply a plan ID, deadline, title, timestamp, source reference, warning, or action/tool name.

Trusted Python constructs the immutable `StudyPlan` envelope: request-scoped UUID, window, configured timezone, UTC generation time, conflict-awareness flag, assumptions and warnings, current assignment facts, task status/priority facts, Moodle change and snapshot facts, validated recommendation items, and provenance references. Facts and recommendations use distinct fields. Titles and Moodle strings remain inert untrusted text. Recommendation durations are labeled as estimates unless a trusted domain supplied them (none currently does).

Validation rejects unknown object keys, malformed JSON, output or item overflow, invalid dates, zero/negative/out-of-bound duration, IDs not in the bounded trusted input, course/entity mismatches, completed-task targets, invalid reason codes, and items on or after an assignment's trusted local deadline date. An already-overdue assignment remains an explicit fact and is not assigned speculative work. No plan item may contain an execution verb or tool name.

Priorities use a closed rationale enum. Python checks each selected reason against trusted inputs and renders the public reason from facts. The model cannot provide free-text factual explanations or claim hidden chain-of-thought. Provenance is assembled by Python from assignment, task, Moodle-change, and (only where requested) validated RAG evidence.

## Moodle changes, snapshots, and stale plans

Planning reads current structured assignments and StudyTasks and the bounded Phase E change page. Current assignment records remain authoritative for deadlines. Change facts may influence rationale only when entity identity and current assignment membership match. Removed assignments cannot be targets. The plan records available source snapshot IDs and generation time. A follow-up update performs fresh bounded reads and generates a new plan; a prior plan never overrides newer current state or Moodle history.

## RAG

Routine deadline/task planning does not call RAG. A document-grounded request may call the existing Phase 4 RAG service in an explicitly resolved course scope. A RAG `INSUFFICIENT_EVIDENCE` response remains authoritative and prevents the planner from inventing document topics. The planner receives only the validated rendered answer and citation provenance; model-suggested study focus remains a recommendation, not a document fact.

## Conversation references

The existing process-local Phase B context may retain one bounded typed plan reference: opaque plan ID, local window/timezone, Moodle snapshot ID, and up to 12 item references with canonical IDs/date/activity/duration/rationale and the relevant deadline/task/change metadata. It stores no generated prose and no durable record is added. Follow-ups such as “second block”, “Tuesday session”, or “why first?” resolve only against this typed reference; missing or multiple matches fail safely. “Focus more on [course]” resolves a trusted course identity and regenerates from fresh current reads. “Update based on changes” always reads current state again.

## Safety and acceptance

Prompt injection embedded in assignment, task, change, or document text is data only. The planner cannot invoke arbitrary shell, HTTP, SQL, Moodle writes, Calendar mutations, or StudyTask mutations. The response remains on `/api/v1/ask`. Empty current obligations produce a typed empty plan; oversized scope requests require narrowing. Tests cover cross-tool grounding, deadlines/timezone/window validation, Moodle additions/changes/removals, stale refresh, plan references and session bounds, RAG refusal, injection, zero mutations, and regressions across Phases A–F.
