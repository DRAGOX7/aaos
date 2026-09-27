# Phase C — Local Study Task Actions

**Milestone:** `NATURAL_LANGUAGE_LOCAL_STUDY_TASK_ACTIONS`
**Base:** `91e305cd873a6a8e1831940b265f0e5013253046`

## Purpose and domain boundary

`StudyTask` is a local AAOS-owned study or work item that a student can create,
read, rename, reprioritize, and complete. It is not a Calendar event or an
approval proposal.

The existing `TaskRecord` belongs to the Calendar approval workflow. It tracks
preparation state and links to a Phase 6 prepared action; its states describe
the approval and external execution lifecycle. A StudyTask has a student-owned
open/completed lifecycle and no provider action. Reusing `TaskRecord` would
couple these different identities, persistence rules, and state machines.

## Persisted schema

Add `study_tasks` with:

| Field | Meaning |
| --- | --- |
| `id` | Stable integer primary key, consistent with academic canonical IDs |
| `title` | Required bounded task title |
| `status` | Closed values `OPEN` or `COMPLETED`; defaults to `OPEN` |
| `priority` | Closed values `LOW`, `NORMAL`, or `HIGH`; defaults to `NORMAL` |
| `course_id` | Optional canonical course link |
| `assignment_id` | Optional canonical assignment link |
| `created_at`, `updated_at` | Aware UTC values stored with the shared `UTCDateTime` type |

At most one of `course_id` and `assignment_id` is stored. An assignment-linked
task derives its course through the canonical assignment row, avoiding a
second copy of that relationship. Assignment `due_at` remains authoritative;
StudyTask has no independent due-date field. Task reads may project that
assignment due time without persisting a divergent copy.

Add `study_task_mutations` as the durable idempotency ledger. Its primary key is
the caller-provided UUID request ID; each row stores one closed operation kind,
a canonical request fingerprint, the resulting StudyTask ID, and its creation
time. One API request can perform at most one task mutation. The task change and
ledger row commit in one database transaction.

## Persistence and service boundaries

`StudyTaskRepository` owns short SQLAlchemy transactions and exposes only typed
create, get, bounded list, allowed-field update, complete, and idempotent replay
operations. It uses ORM parameters and canonical foreign keys; it accepts no
SQL, caller filters, or arbitrary field dictionaries. Rollback covers both the
domain mutation and idempotency record.

`StudyTaskService` validates titles, status transitions, priorities, links,
request IDs, and canonical fingerprints. Assignment links must resolve to an
active canonical assignment. If a task title is omitted for an assignment
link, trusted Python uses the canonical assignment title. A course-only task
must have a title. A replay with the same request ID and fingerprint returns
the recorded task; reuse of that ID for a different operation or payload is a
typed conflict. Similar titles with distinct request IDs remain distinct
tasks.

The migration is an Alembic child of `0009_rag_structural_provenance`, adds only
the two new tables and their bounded constraints/indexes, and has a reversible
downgrade. Verification uses disposable test databases only.

## Local write governance

Repository guidance requires Phase 6 human approval for external writes and
keeps capability permission separate from approval. StudyTask mutations are
local AAOS-owned state changes, so the task service is callable only through a
closed `LOCAL_STATE_WRITE` tool granted by trusted application composition.
They do not create a Phase 6 action, approval, Calendar event, or Moodle write.
Existing Calendar approval requirements are unchanged.

## Agent and API contract

Extend the existing `/api/v1/ask` structured agent with fixed task read and
mutation tools. Read filters are closed (open/completed and canonical course
scope) and results are bounded. Mutations expose only create, title/priority
update, and complete. Task and academic identities must come from validated
request-local evidence or the existing Phase B `ctx-1` resolver; model-supplied
numeric IDs are not accepted as authority.

The caller supplies a stable UUID `request_id` for an ask that may mutate a
task. It is captured by trusted Python and never included in model-visible
context. Without it, the ask remains read-only. Callers retry a timed-out or
uncertain request with the same UUID. The idempotency ledger prevents a second
task or repeated update, while rejecting changed intent under that UUID.

LM Studio uses a strict structured-output schema matched to the runtime
registry: task reads have read-only variants, task mutations appear only when
the trusted request ID enables their registry entries, and contextual variants
require the fixed `ctx-1` argument. When the active context is a StudyTask, a
task-context-specific write schema omits `context_ref` only for
`study_task.create`, matching the runtime rule that a new task does not inherit
another task's context. The legacy Phase A/B response schemas stay limited to
their existing read-only capability surface.

Only a successful trusted service result becomes mutation evidence. A terminal
success response is rendered from that result after commit; tool errors,
cancellation, timeout, invalid references, and persistence failures cannot be
rendered as a successful mutation. Observations contain bounded task fields,
never ORM objects or exception text.

## Trust and future Calendar relationship

Requests, model decisions, academic titles/descriptions, and observations stay
untrusted. They cannot expand schemas, permissions, tools, context, or query
scope. Trusted Python owns entity resolution, IDs, authorization, validation,
transactions, and final mutation wording. Shell, HTTP, SQL, Calendar, and Moodle
write capabilities remain absent.

A future Calendar milestone may propose an explicitly requested Calendar event
from a StudyTask through the existing approval architecture. This milestone
does not create or synchronize Calendar events automatically.
