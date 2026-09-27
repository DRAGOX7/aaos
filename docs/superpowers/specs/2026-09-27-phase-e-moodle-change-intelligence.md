# Phase E — Moodle Change Intelligence

## Objective and boundary

Phase E detects and explains changes between complete, normalized Moodle
states. The only source of change truth is a deterministic Python diff over
persisted academic snapshots. The agent may select a bounded result and
explain its already-computed facts. Moodle titles and other metadata remain
untrusted observations.

This phase does not write Moodle, StudyTasks, or Calendar state. It does not
schedule, notify, add a frontend feed, or retain conversational memory.

## Snapshot and identity strategy

Persist one compact snapshot per Moodle synchronization attempt in the AAOS
database. Create a `RUNNING` attempt before discovery, transition failures to
`FAILED`, and write the `COMPLETE` snapshot in the same transaction as the
academic upserts. Thus readers cannot see newly synchronized current state
without its corresponding completed snapshot. An unfinished latest attempt
also fails closed.

The payload contains only normalized academic meaning: course title/code,
assignment title/deadline, announcement title/publication time, and Moodle
resource title plus parent course identity. It excludes descriptions, bodies,
URLs, HTML, document content, hashes, row timestamps, and database IDs.

Entity identity is `(source_system, provider source_id)`, with child course
identity included for comparison. The existing Moodle adapters produce
provider-backed course IDs and parent-scoped assignment/resource IDs. Internal
database IDs, ordering, and source update timestamps do not determine whether
an entity changed.

Document content versions reuse immutable `DocumentVersionORM` history. The
reader compares only the latest successful version number for a stable Moodle
resource identity as of each query endpoint; content, blob hashes, filenames,
and internal version IDs never enter model-visible change records.

## Completeness and comparison

Only `CompleteSourceSnapshot` values accepted by current normalization can
complete a history row. Failed collection, invalid normalization, persistence
failure, unsupported payload version, or corrupted history remains unavailable
and cannot create removal records. At most one active sync per Moodle source
is allowed.

With no explicit time phrase, compare the previous complete sync to the latest
complete sync. For `since yesterday`, resolve a rolling 24-hour UTC boundary
from the request clock. For an ISO date/time, use the parsed UTC instant. Then
compare the latest complete state at or before that boundary to the latest
complete state now. Multiple intermediate syncs therefore produce a net diff.
An unrecognized `since ...` phrase, absent baseline, ambiguous source, failed
latest attempt, or non-comparable schema yields a typed
`CHANGE_COMPARISON_UNAVAILABLE` result. A valid equal-state comparison returns
an available empty set.

## Change contract and diff

Use closed entity, kind, and field enums. Entity types are course, assignment,
announcement, and document; kinds are `ADDED`, `REMOVED`, and `MODIFIED`.
Semantic fields include course title/code, assignment title/deadline, and
announcement title/publication time. A remote file is a document; its title,
parent, presence, and immutable version number are compared. Change records
carry safe before/after values, source sync IDs, course/entity references, and
a deterministic detection time.

The pure diff indexes each entity set by canonical identity and compares maps
in O(n) expected time. It returns a stable sort by course identity, entity
type, entity identity, and kind. Read results are limited to 15 records per
page with total counts and `has_more`; the agent may request a bounded offset.
The renderer reports the total and whether more changes are available.
Only a zero total is rendered as no changes; an empty page at a nonzero
offset remains distinct from an empty comparison.

## Agent and provenance

Expose one read-only `moodle.list_changes` tool through `/api/v1/ask` only for
change-intelligence requests. The original user text, not an LLM-generated
timestamp, drives deterministic time-window parsing. Named courses are
resolved by the existing exact course reader; conversational references use
the Phase B `context_ref` lease. The tool accepts no provider IDs, SQL, URLs,
or generic query expressions.

Change results preserve from/to sync IDs, stable entity identity internally,
and validated local course/entity source references. User-visible records are
projected through the existing bounded observation sanitizer. The renderer
reports unavailable, no-change, and concrete field-change states without
turning model prose into evidence. Prompt-like titles remain data and cannot
change registered tools or permissions.

## Persistence and verification

Add a non-destructive migration for the sync-attempt/history table. Tests use
synthetic normalized Moodle snapshots and disposable migrated SQLite files.
They cover deterministic field diffs, provider identity, document versions,
failure/completeness gates, time boundaries, net comparison across multiple
syncs, Phase B references, prompt injection, and preservation of existing
agent/RAG/approval boundaries. No configured Moodle, Calendar, or personal
database operation is part of Phase E verification.

Change-aware task updates, calendar proposals, notifications, broader time
language, and history pruning remain future work.
