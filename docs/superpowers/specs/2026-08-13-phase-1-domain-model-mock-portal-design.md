# Phase 1 — Domain Model and Mock Portal Design

## 1. Purpose and scope

Phase 1 establishes the smallest deterministic academic domain and
synchronization substrate before AAOS connects to a real Moodle portal. It
defines canonical records, stable source identity, SQLite persistence,
normalization, an idempotent snapshot sync, and an offline mock source.

Phase 1 has no credentials, network access, Moodle authentication, document
downloads, LLM behavior, agents, or external writes. The accepted Phase 0
boundary remains frozen at the `phase-00-passed` tag.

## 2. Existing foundation

The repository is clean on `main`, `phase-00-passed` exists, and both resolve
to `55d2293c49f3f7ab73e4920a5e97c346efb362cd`. The current persistence layer
provides explicit SQLite URL/engine/session-factory helpers. Alembic revision
`0001_baseline` intentionally creates no domain tables, so Phase 1 can add one
forward migration without redesigning the Phase 0 database boundary.

The current `src/aaos` and `tests` contain Phase 0 runtime, inference, doctor,
health, and persistence checks only; no Phase 1 academic entities, adapters,
repositories, or synchronization implementation exists.

## 3. Architecture options

### Option A — deterministic fixture source (recommended)

```text
in-memory mock snapshot
        -> source records
        -> pure normalization
        -> canonical snapshot
        -> one transactional sync
        -> SQLite
```

This has no server, sockets, HTTP lifecycle, or timing variability. Fixtures can
represent multiple courses and controlled changes while the source protocol is
reusable by a future Moodle adapter. It is the smallest implementation and
gives the strongest deterministic test signal.

### Option B — local HTTP mock portal

```text
local HTTP server -> source client -> normalization -> canonical sync -> SQLite
```

This exercises transport and serialization more realistically, but adds a
server lifecycle, HTTP error cases, port management, and an API shape that can
accidentally couple the domain to a fake Moodle. Those concerns belong in the
Phase 2 source adapter boundary and are not needed to prove Phase 1 sync.

### Decision

Use Option A only. Do not build both variants or a visual fake portal. The
source protocol keeps Option B or a real Moodle adapter replaceable later
without changing canonical entities or sync semantics.

## 4. Chosen architecture

The source adapter owns provider-shaped records and must report whether
enumeration completed successfully. A pure normalizer validates only a
successful complete result and converts it into canonical sync input. The sync
service owns one transaction and talks to persistence models; it never parses
Moodle HTML or provider-specific payloads.

```text
MockAcademicSource
        -> RawSnapshot
        -> normalize_snapshot()
        -> CanonicalSnapshot
        -> AcademicSyncService
        -> SQLAlchemy models / SQLite
```

Phase 2 can implement the same source protocol with Moodle API or browser
extraction. Authentication and session state remain outside this Phase 1
contract.

## 5. Minimal domain model

The approved minimum is four source-backed entities. No separate Task/Event
aggregate is introduced: an assignment is the deadline-bearing academic task
for this phase.

### Course

- internal integer primary key;
- required canonical title;
- optional course code and description;
- `source_system`, `source_id`;
- `is_active`;
- optional source `updated_at`.

### Assignment

- internal integer primary key;
- required `course_id` foreign key;
- required title;
- optional description and `due_at`;
- `source_system`, `source_id`;
- `is_active` and optional source `updated_at`.

### Announcement

- internal integer primary key;
- required `course_id` foreign key;
- required title and optional body;
- optional `published_at`;
- `source_system`, `source_id`;
- `is_active` and optional source `updated_at`.

### RemoteFile

- internal integer primary key;
- required `course_id` foreign key;
- required display name/title;
- optional normalized remote URL (metadata only; never downloaded in Phase 1);
- `source_system`, `source_id`;
- `is_active` and optional source `updated_at`.

Descriptions, bodies, and URLs are ordinary academic metadata, not identity.
No document bytes, local paths, hashes, ingestion state, or embeddings are
stored by this phase.

## 6. Identity rules

Internal database primary keys are AAOS implementation identities and must not
be exposed as provider identity. Every entity table has a unique composite
constraint on `(source_system, source_id)`.

`source_system` is a normalized, non-blank, non-secret source namespace (source
instance key), not merely a generic adapter type. Conceptual values include
`mock:primary` and `moodle:just`; they must remain stable without encoding
credentials, sessions, tokens, or unstable user-visible labels. Every lookup,
parent resolution, deactivation, reactivation, and snapshot-ownership
operation uses the exact namespace. `source_id` is a non-blank opaque
provider identifier. The uniqueness scope is per entity table, so an
assignment and a course may both use `42`, but two assignments in the same
source namespace may not. If a future provider only exposes parent-scoped IDs,
its adapter must compose a stable scoped `source_id` before the canonical
boundary. Titles, URLs, dates, or row order never identify a record.

Parent links use AAOS internal foreign keys, not copied provider IDs. A child
record is valid only when its parent course is present in the same canonical
snapshot or already exists in the same exact source namespace.

## 7. Source and normalization boundary

The source layer exposes typed, provider-neutral records with strings and
optional source timestamp strings. It does not return SQLAlchemy instances.
Its collection result distinguishes:

- a successfully completed enumeration, which may be normalized into a
  `CanonicalSnapshot`;
- a partial, interrupted, failed, or unknown-completeness result, which becomes
  a typed source/validation failure and never reaches `sync()`.

The normalizer:

1. validates required non-blank fields and source identity;
2. requires an explicit complete-collection marker, including for an empty
   snapshot;
3. rejects duplicate `(source_system, source_id)` identities per entity type;
4. validates each child reference exactly once against a course in the same
   source namespace;
5. parses and canonicalizes timestamps;
6. validates optional URLs without fetching them;
7. returns an immutable `CanonicalSnapshot` or a typed validation error.

Normalization is pure: identical raw input produces identical canonical input,
and malformed, incomplete, duplicate, or ambiguous input is rejected before a
database transaction starts. An explicitly complete empty snapshot is valid;
it is the only empty result permitted to deactivate an existing namespace.

## 8. Synchronization semantics

`sync(snapshot)` accepts only an immutable, successfully completed snapshot for
one exact source namespace (all courses and children in that source's scope).
Partial, interrupted, failed, intermediate-page, or unknown-completeness
results are not `CanonicalSnapshot` values and cannot enter the sync service.

- **First discovery:** insert each course, then its children; database-generated
  internal IDs are stable after insertion.
- **Repeated identical snapshot:** locate rows by `(source_system, source_id)`
  and update them with the same canonical values. No duplicate rows result and
  the final logical state is unchanged.
- **Changed record:** update mutable canonical fields in place, preserving the
  internal primary key and source identity.
- **New record:** insert it and its parent relationship.
- **Disappearance:** before upsert, mark existing rows for that exact source
  namespace inactive; rows present in the snapshot are upserted as active.
  Rows are retained for history and can be reactivated if they reappear. No
  hard delete occurs in Phase 1. A complete empty snapshot may therefore
  deactivate every record in that namespace, but never records in another one.
- **Parent/child handling:** courses are upserted first. A child reference must
  resolve exactly once to a course in the same namespace. A missing, ambiguous,
  or cross-namespace parent is a normalization failure; it cannot create an
  orphan.
- **Parent identity immutability:** an existing Assignment, Announcement, or
  RemoteFile keeps its persisted course identity. If the same child source
  identity later resolves to a different course, synchronization raises a
  typed conflict and rolls back; it never silently re-parents the child.
- **Failure:** normalization happens before writes. The deactivation and all
  upserts then run in one transaction. Any constraint or persistence failure
  rolls back the entire sync, including deactivation; no partial academic
  state is committed.

The same complete snapshot and canonical input produce the same database state
regardless of how many times they are synchronized.

### Remote URL safety

`RemoteFile.remote_url` is optional, non-secret locator metadata only. The
canonical boundary rejects embedded username/password userinfo, non-HTTP(S)
schemes, and control characters. It must not contain authentication headers,
cookies, session state, access tokens, or credential-bearing/signed URL
material. Phase 2 adapters must remove or omit provider-specific
authentication material before normalization. Phase 1 does not add a broad
secret scanner, infer provider tokens, or fetch any URL.

## 9. Time and timezone policy

All canonical application datetimes are timezone-aware UTC values
(`datetime` with `timezone.utc`). A source timestamp must include an explicit
offset or `Z`; a naive timestamp is malformed and rejected. No local timezone,
including Jordan time, is inferred.

The normalizer converts offsets to UTC. Optional source timestamps that are
absent remain `None`; AAOS does not invent publication or deadline times. The
SQLite representation is a canonical ISO-8601 UTC string ending in `Z`, handled
by a small SQLAlchemy UTC type that returns aware UTC datetimes to application
code. This avoids SQLite's loss of timezone offsets and keeps comparisons
unambiguous.

## 10. Persistence and migration design

Add one Alembic revision after `0001_baseline` containing `courses`,
`assignments`, `announcements`, and `remote_files`.

Each table has an integer primary key, required source identity columns,
required display fields, `is_active NOT NULL` with a true default, and nullable
source/date or descriptive fields as listed above. Child tables have required
foreign keys to `courses.id`; hard deletion is not used by the sync service.

Constraints and indexes:

- unique `(source_system, source_id)` on every entity table;
- foreign-key enforcement enabled for SQLite connections;
- indexes on each child `course_id` and on `(course_id, is_active)`;
- indexes on `(source_system, is_active)` where the migration/backend benefits
  from them.

The implementation may place SQLAlchemy mappings in
`src/aaos/persistence/academic_models.py` and keep canonical dataclasses
separate from ORM classes. No Moodle authentication, cookie, token, or session
tables are introduced.

## 11. Mock source design

`MockAcademicSource` is an in-memory adapter initialized with a named sequence
of complete snapshots. A fixture can contain multiple courses, assignments
with deadlines, announcements, and remote-file metadata. Tests select a
snapshot explicitly, so changes (new, changed, and missing records) are
deterministic and do not require a server.

The mock source emits sanitized academic examples only. It has no network,
credential, browser, or filesystem side effect.

## 12. Transaction and error semantics

The sync service validates the complete snapshot before opening its write
transaction and commits once. It uses the existing SQLAlchemy session factory;
the service owns rollback on any expected persistence error and re-raises a
fixed typed synchronization error without raw SQL, paths, or environment
details.

Foreign-key and unique-constraint violations are failures, not silent repairs.
The service does not retry, partially commit, or fall back to another source.

## 13. Testing strategy

Phase 1 verification is deterministic and offline:

- migration upgrade/downgrade creates and removes exactly the Phase 1 tables;
- unique source identity, required fields, foreign keys, and active-state
  constraints are enforced;
- two independent source namespaces may reuse provider IDs without collision;
- same source IDs across different entity tables are valid;
- normalization accepts aware offsets and returns UTC, accepts `Z` input,
  preserves absent optionals as `None`, and rejects naive timestamps, blank or
  unsafe identity, malformed records, incomplete collection, duplicate
  identities, missing/ambiguous/cross-source parents, unsafe URLs, and child
  re-parent conflicts;
- initial sync inserts the expected graph;
- identical sync is idempotent and does not duplicate rows;
- changed and new records update/insert as specified;
- disappeared records become inactive and reappearing records reactivate;
- an explicitly complete empty snapshot deactivates only its exact namespace;
- invalid or incomplete input never deactivates existing rows;
- malformed input leaves the database untouched;
- an injected persistence failure rolls back all rows and prior deactivation;
- parent/child foreign-key integrity is verified;
- mock fixtures cover multiple courses, deadlines, announcements, and remote
  files;
- importing the new modules performs no I/O or network access.

No ML benchmark, live Moodle call, HTTP server, credential fixture, or private
academic data is part of Phase 1 acceptance.

## 14. Acceptance criteria

Phase 1 is ready for human acceptance only when:

1. the migration upgrades and downgrades cleanly from the accepted baseline;
2. the four canonical entities and their constraints are exercised by tests;
3. normalization is deterministic, timezone-safe, and rejects malformed input;
4. the complete-snapshot sync is idempotent, update-safe, insertion-safe,
   disappearance-safe, and transactionally atomic;
5. deterministic mock fixtures prove the expected relationships and changes;
6. all Phase 1 tests, existing Phase 0 tests, formatting, lint, type, lock,
   and diff checks pass without weakening existing gates;
7. no credentials, private academic data, network access, or Phase 2 behavior
   appears in the implementation or fixtures;
8. an evidence report records failures, limitations, and the explicit human
   decision to proceed to Phase 2.

## 15. Explicit exclusions

Phase 1 does not implement Moodle authentication or sessions, Playwright,
real university access, portal writes, file downloads or ingestion, RAG,
embeddings/vector databases, LLM inference, agents, MCP, Google Calendar,
GitHub, dashboards, telemetry UI, restricted REPL, vision, or Phase 2
scaffolding disguised as domain work.

## 16. Proposed implementation files

The later implementation plan may add only the focused Phase 1 files needed:

- `src/aaos/academic/models.py` — canonical records and UTC handling;
- `src/aaos/academic/source.py` — source protocol and raw/canonical snapshot
  contracts;
- `src/aaos/academic/normalization.py` — pure validation/conversion;
- `src/aaos/academic/mock.py` — deterministic in-memory source;
- `src/aaos/academic/sync.py` — transactional upsert/deactivation service;
- `src/aaos/persistence/academic_models.py` — SQLAlchemy mappings;
- `migrations/versions/0002_phase1_academic_domain.py` — schema migration;
- focused `tests/test_academic_*.py` and migration tests.

The existing database helper should change only if needed to enable SQLite
foreign keys; no new dependency or database technology is proposed.

## 17. Design-review decisions and handoff

The human design review approves the in-memory source, four canonical entities,
retain-and-deactivate semantics, strict UTC handling, and the source/normalizer/
sync separation. The review also fixes these implementation contracts:

1. `source_system` is an exact stable source namespace, not a generic adapter
   name;
2. only a proven complete snapshot, including a proven complete empty snapshot,
   may reach synchronization;
3. duplicate identities and ambiguous or cross-namespace parents fail before
   writes;
4. child parent identity is immutable in Phase 1;
5. `RemoteFile.remote_url` is non-secret HTTP(S) metadata and never a
   credential-storage channel.

The specification is ready for the focused Phase 1 implementation plan. No
production code, migration, test, dependency, network, Moodle, or Phase 2
functionality is authorized by this document alone.
