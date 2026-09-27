# Phase F — Change-Aware Conversation

## Objective and boundary

Phase F carries validated Phase E Moodle change records through the existing
Phase B conversation lease, then permits narrowly scoped follow-up explanation
or explicitly requested local actions. `/api/v1/ask` remains the only
conversation API. Conversation state stays process-local and inherits Phase B
limits: eight turns, sixteen entities, 128 sessions, and a 30-minute idle TTL.

No action runs from a change query alone. The LLM receives a closed registry
selected by Python intent gates. Moodle remains read-only. Calendar CREATE is
only a pending proposal through the existing Phase 6 approval binding; Calendar
update, move, and delete are unsupported. No notification, long-term memory,
frontend, generic tool, HTTP, shell, or SQL capability is introduced.

## Trusted change references

A successful `moodle.list_changes` observation is accepted only after the
existing Phase E output validator. Its records are converted to a typed
`moodle_change` context reference containing the canonical local course/entity
IDs, change ID, from/to snapshot IDs, entity and change kinds, changed-field
enum values, safe before/after values, source tool/reference/turn, title, and
course title. Change references share the existing entity count, ordering,
session isolation, turn count, and expiry behavior; they are never accepted
from model arguments or user-supplied IDs.

The resolver supports explicit `this/that change`, bounded ordinals such as
`the second change`, and an unambiguous generic reference to the active change.
Missing and ambiguous references use the existing deterministic refusal path.
Only the bounded, safe facts needed for a follow-up are serialized into the
model context hint; canonical database IDs remain application-side.

Before/after deadline questions report the selected change's retained values.
Questions asking for the current, now, today, or latest deadline re-read the
canonical Assignment through its trusted context ID, attach that read as an
Ask source, and refuse if the current value cannot be verified.

## Follow-up composition

### Explanation with RAG

For an explicit explanation request tied to a trusted change, Python registers
only `rag.answer`. A changed document is resolved to its current canonical
`DocumentSummary` and sent through the existing RAG adapter with a one-version
`HistoricalScope`; assignment/course explanations use the trusted course
scope. RAG explains the change but cannot establish that a change exists or
replace Phase E dates. Existing citation provenance and refusal behavior are
preserved. A RAG result is not written back as trusted change state.

### StudyTask deadline acknowledgement

A change-aware StudyTask action is registered only when all conditions hold:
the request explicitly asks to update a StudyTask deadline, the resolved
trusted change is an assignment `due_at` modification with a non-null after
value, the existing task-write request ID is present, and the configured task
service is available. The model receives no date, assignment ID, change ID, or
snapshot ID arguments. Python derives all of them from the trusted context.

Phase C keeps an assignment-linked StudyTask deadline as a live projection of
the canonical Assignment row; it has no independent due-date field. Applying a
change therefore updates the task's `updated_at` and writes one idempotency
ledger record linked to the exact Moodle change/snapshots/`due_at` field. It
does not copy the date into a second task field or mutate the Moodle assignment.
In the same task transaction, the repository verifies the task is linked to
the referenced assignment, both source snapshots are complete, the latest
source attempt is complete, and the canonical assignment deadline still equals
the trusted after value. A stale or mismatched change fails without a task or
ledger write. Replaying the same request ID and fingerprint returns the saved
task result; reusing that ID for a different change conflicts.

Task resolution is bounded to the existing StudyTask cap and exact linked
assignment. A missing or multiply linked task is refused; the model cannot
choose an arbitrary local task by inventing an ID. The user's explicit intent
is required even when trusted change context exists.

### Calendar CREATE and unsupported update

An explicit Calendar CREATE request from a trusted assignment deadline change
may use the existing proposal tool. Python re-reads the canonical assignment,
checks its current deadline against the trusted after value, and builds the
existing CREATE_EVENT proposal. The established approval service remains the
only path to execution. An update/move/delete request returns a deterministic
unsupported-operation response before model execution and creates no fallback
CREATE proposal.

## Provenance

Change query and context sources retain the Moodle tool name, course/entity
identity, change ID, from/to snapshot IDs, changed field(s), and source turn.
An applied StudyTask mutation stores the same change linkage in the existing
mutation ledger and includes it in the structured Ask source projection. A RAG
explanation retains the change in `context_sources` and the Phase 4 citation
provenance in `rag_provenance`.

## Verification

Use deterministic fakes and disposable migrated SQLite databases only. Cover:

1. Moodle change query followed by a trusted before/after deadline reference,
   including turn bounds and session isolation.
2. Changed-document explanation with exact document-version RAG scope and a
   preserved RAG refusal.
3. Explicit linked StudyTask deadline acknowledgement, idempotent replay,
   persisted source linkage, and stale-change rejection.
4. Calendar CREATE proposal from a trusted assignment change through the
   existing approval boundary, plus deterministic Calendar-update refusal
   with no duplicate CREATE.
5. Prompt-like metadata remains inert and the registered tool set stays
   closed; no live Moodle, Calendar, personal database, or protected evaluation
   operation is used.

The Phase F gate is the focused scenario suite, existing conversation/change,
StudyTask, Calendar, RAG, and lineage regressions; full mypy, Ruff, lock check,
and diff hygiene. Independent Spec and Standards reviews must inspect the
committed candidate SHA. Phase G does not begin in this change.
