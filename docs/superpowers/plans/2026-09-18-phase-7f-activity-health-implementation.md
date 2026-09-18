# Phase 7F Activity, SSE, and System Health Implementation Plan

## Overview

**Goal:** Add a read-only Activity history/stream and passive System Health
surface to the accepted Phase 7 dashboard without creating a second audit log,
changing Phase 6/7E authority, adding a migration, or introducing Phase 7G.

**Architecture:** Existing Phase 6 `ActionEventORM` rows remain the durable
transition source. Phase 7E `TaskRecordORM`, `ApprovalRecordORM`, and
`OperationRecordORM` are joined to produce a closed browser projection. Task
creation/preparation failure rows are derived from `TaskRecordORM` at read time;
no new event table is created. A versioned compound cursor provides deterministic
ordering across per-action sequences. FastAPI serves history and a database-tail
SSE stream through the existing Next.js same-origin rewrite. System Health uses
bounded local database/persisted-index reads only.

**Execution order:** Task 1 → Task 2 → Task 3 → Task 4 → Task 5 → Task 6 →
Task 7 → Task 8 → Task 9. Each implementation task has its own RED, smallest
GREEN, regression verification, and one commit. Task 9 is verification-only and
creates no implementation commit.

## Global constraints

- Work only on `phase-07-dashboard` from the reviewed Phase 7F docs checkpoint.
- Do not modify frozen Phase 4/5/6 implementation or Phase 7A–7E semantics.
- Do not add migrations, tables, brokers, WebSockets, background workers, or
  new provider integrations. Existing Alembic head remains authoritative.
- Activity and health routes are GET-only. They cannot approve, reject, claim,
  execute, retry, reconcile, acknowledge, or mutate a cursor.
- No model, RAG, Moodle, LM Studio, Google, network-provider, credential, or
  external-write call is allowed in the Phase 7F acceptance harness.
- Do not expose raw ActionEvent details, TaskRecord internals, operation/claim
  identities, hashes, arguments, actor context, provider payloads, paths, SQL,
  exceptions, credentials, or model output.
- `/health` remains the existing process-liveness contract exactly.
- Pydantic browser models are frozen, strict, bounded, and `extra="forbid"`;
  tuple response fields remain immutable projections. OpenAPI and generated
  TypeScript are regenerated through the existing workflow, never hand-edited.
- Native browser `EventSource` is the only live client transport. It exists
  only while the Activity page is mounted and is cleaned up on unmount.
- A disposable acceptance database is migrated through Alembic and frozen
  before any browser/result inspection. No normal AAOS database is touched.
- If a task would require a migration, direct FastAPI browser access, provider
  I/O, authority changes, or a global durable event table, stop for a new
  human architecture decision.

## Current repository interfaces reused

- `ApprovalRepository.append_action_event` and `read_action_event_history`
  preserve Phase 6 transactional append-only semantics.
- `ActionEventORM` exposes `event_id`, `action_id`, per-action `sequence`,
  `event_kind`, typed `detail_payload`, `occurred_at`, and actor identity.
- `TaskRepository` exposes immutable task identity and CAS creation state;
  `TaskRecordORM.action_id` is unique and joins a task to one prepared action.
- `TaskReadService` and `AcademicReadService` remain read-only projections.
- `create_api_v1_router` owns route registration; routes delegate to services and
  do not construct SQL.
- `main.create_app` uses lazy database/model composition; importing the app
  must remain side-effect free.
- `frontend/next.config.ts` rewrites same-origin `/api/:path*` to FastAPI.
- `frontend/lib/api/errors.ts`, generated `frontend/types/api.generated.ts`,
  TanStack Query, `AppShell`, `PageHeader`, and `SectionCard` are reused.

## Task 1: Define closed Activity and System Health contracts

**Files**

- Create `src/aaos/web/activity.py`.
- Create `src/aaos/web/health.py`.
- Create `tests/test_phase7f_activity_contracts.py`.
- Create `tests/test_phase7f_health_contracts.py`.

**Interfaces produced**

```text
class ActivityCategory(StrEnum):
    TASK = "TASK"
    APPROVAL = "APPROVAL"
    INTEGRATION = "INTEGRATION"
    SYSTEM = "SYSTEM"

class ActivityEventCode(StrEnum):
    TASK_CREATED = "TASK_CREATED"
    PREPARATION_FAILED = "PREPARATION_FAILED"
    ACTION_PREPARED = "ACTION_PREPARED"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CLAIMED = "CLAIMED"
    EXECUTING = "EXECUTING"
    CONFIRMED_SUCCESS = "CONFIRMED_SUCCESS"
    CONFIRMED_FAILURE = "CONFIRMED_FAILURE"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    RECONCILED = "RECONCILED"

class ActivityStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CLAIMED = "CLAIMED"
    EXECUTING = "EXECUTING"
    CONFIRMED_SUCCESS = "CONFIRMED_SUCCESS"
    CONFIRMED_FAILURE = "CONFIRMED_FAILURE"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    NOT_DISPATCHED = "NOT_DISPATCHED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"

class ActivityResource(StrEnum):
    TASK = "TASK"
    APPROVAL = "APPROVAL"

class HealthStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"
    NOT_CONFIGURED = "NOT_CONFIGURED"

class HealthComponentName(StrEnum):
    DATABASE = "DATABASE"
    ACADEMIC_DATA = "ACADEMIC_DATA"
    ACADEMIC_SYNC = "ACADEMIC_SYNC"
    DOCUMENT_PIPELINE = "DOCUMENT_PIPELINE"
    RAG_INDEX = "RAG_INDEX"
    INFERENCE = "INFERENCE"
    GOOGLE_CALENDAR = "GOOGLE_CALENDAR"

class HealthDetailCode(StrEnum):
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    SCHEMA_NOT_READY = "SCHEMA_NOT_READY"
    ACADEMIC_READ_UNAVAILABLE = "ACADEMIC_READ_UNAVAILABLE"
    RAG_INDEX_READY = "RAG_INDEX_READY"
    RAG_INDEX_PARTIAL = "RAG_INDEX_PARTIAL"
    RAG_INDEX_NOT_CONFIGURED = "RAG_INDEX_NOT_CONFIGURED"
    PASSIVE_STATUS_UNAVAILABLE = "PASSIVE_STATUS_UNAVAILABLE"
    INTERNAL_STATUS_UNAVAILABLE = "INTERNAL_STATUS_UNAVAILABLE"

class ActivityCursor(NamedTuple):
    occurred_at: datetime
    source_rank: int
    stable_source_id: str
    source_sequence: int

encode_activity_cursor(cursor: ActivityCursor) -> str
decode_activity_cursor(value: str) -> ActivityCursor

class TaskDerivedDetail:
    failure_code: Literal["INVALID_REQUEST", "POLICY_DENIED", "INTERNAL_ERROR"] | None

class ActionEventDetail:
    event_code: ActivityEventCode
    status: ActivityStatus | None

class ActivitySourceRow:
    source_kind: Literal["TASK_DERIVED", "ACTION_EVENT"]
    occurred_at: datetime
    stable_source_id: str
    source_sequence: int
    task_id: int | None
    approval_id: str | None
    action_event_code: ActivityEventCode | None
    task_creation_state: str | None
    task_failure_code: str | None
    approval_status: ActivityStatus | None
    operation_status: ActivityStatus | None
    typed_detail: TaskDerivedDetail | ActionEventDetail | None

class TaskActivityContext(BaseModel):
    kind: Literal["TASK"]
    task_id: str

class ApprovalActivityContext(BaseModel):
    kind: Literal["APPROVAL"]
    approval_id: str
    task_id: str

class OperationActivityContext(BaseModel):
    kind: Literal["OPERATION"]
    status: ActivityStatus

ActivityContext = Annotated[
    TaskActivityContext | ApprovalActivityContext | OperationActivityContext,
    Field(discriminator="kind"),
]

class ActivityEvent(BaseModel):
    schema_version: Literal["phase7-activity-v1"]
    activity_id: str
    occurred_at: datetime
    category: ActivityCategory
    event_code: ActivityEventCode
    status: ActivityStatus | None
    resource_type: ActivityResource | None
    resource_id: str | None
    title: str
    detail: str | None
    context: ActivityContext | None

class ActivityHistoryResponse(BaseModel):
    schema_version: Literal["phase7-activity-v1"]
    events: tuple[ActivityEvent, ...]
    next_cursor: str | None

class HealthComponent(BaseModel):
    name: HealthComponentName
    status: HealthStatus
    required: bool
    detail_code: HealthDetailCode

class SystemHealthResponse(BaseModel):
    schema_version: Literal["phase7-health-v1"]
    checked_at: datetime
    overall: HealthStatus
    components: tuple[HealthComponent, ...]
```

`ActivityEvent` has exactly the fields and event-code mapping in the approved
specification. `ActivityHistoryResponse.events` and
`SystemHealthResponse.components` are tuples. Cursor decoding rejects unknown
fields, invalid UTC, negative sequence, oversized IDs, malformed base64, and
non-canonical JSON. Health detail codes are closed to
`DATABASE_UNAVAILABLE`, `SCHEMA_NOT_READY`, `ACADEMIC_READ_UNAVAILABLE`,
`RAG_INDEX_READY`, `RAG_INDEX_PARTIAL`, `RAG_INDEX_NOT_CONFIGURED`,
`PASSIVE_STATUS_UNAVAILABLE`, and `INTERNAL_STATUS_UNAVAILABLE`.

**RED**

1. Add exact field/enum allowlist tests and strict/extra-field rejection tests.
2. Add cursor round-trip, identical-timestamp ordering, malformed/oversized
   rejection, and no-secret-content tests.
3. Add activity projection tests for every supported event code and exclusion
   of `LATE_RESULT_IGNORED`/raw detail payloads.
4. Run:

```text
uv run pytest tests/test_phase7f_activity_contracts.py tests/test_phase7f_health_contracts.py -q
```

Expected RED: modules/contracts do not yet exist.

**GREEN**

Implement only frozen contracts, cursor codec, and pure trusted-to-browser
projection helpers. Do not import SQLAlchemy, providers, models, or EventSource.
Run the focused tests, `uv run ruff check src/aaos/web/activity.py
src/aaos/web/health.py tests/test_phase7f_*contracts.py`, `uv run mypy src/aaos`,
and `git diff --check`.

**Commit**

`feat(phase7f): define activity and health contracts`

**Stop condition:** any free-form context, raw event payload, mutable response
list, or cursor containing a query language is a failure; fix before commit.

## Task 2: Implement Activity read repository, projection service, and history API

**Files**

- Create `src/aaos/web/activity_read.py`.
- Modify `src/aaos/web/routes.py` only to register `GET /api/v1/activity`.
- Modify `src/aaos/main.py` only for lazy Activity service wiring/disposal.
- Create `tests/test_phase7f_activity_read.py`.
- Create `tests/test_phase7f_activity_api.py`.

**Interfaces produced/consumed**

```text
ActivityReadRepository.list_source_rows(
    *, before: ActivityCursor | None, after: ActivityCursor | None, limit: int
) -> tuple[ActivitySourceRow, ...]
ActivityReadRepository.current_high_watermark() -> ActivityCursor | None
ActivityReadService.list_history(
    *, limit: int, before: ActivityCursor | None = None
) -> ActivityHistoryResponse
ActivityReadService.tail(
    *, after: ActivityCursor | None, limit: int
) -> tuple[ActivityEvent, ...]
```

The repository performs read-only SQLAlchemy joins across TaskRecord,
ActionEvent, PreparedAction, ActionLifecycle, ApprovalRecord, and
OperationRecord. It constructs the total-order tuple from UTC timestamp,
source rank, stable source identity, and sequence, and uses deterministic
strict-before/strict-after predicates. Derived `TASK_CREATED` and
`PREPARATION_FAILED` rows are read-time projections. Orphaned/malformed rows
are omitted or converted to the existing safe `INTERNAL_ERROR`; no repair or
write occurs. The route validates `limit` in `1..200`, decodes `before`, and
returns only `ActivityHistoryResponse`.

**RED**

1. Build a migrated temporary SQLite fixture containing identical timestamps,
   linked tasks/actions/events, an orphan action, and a preparation-failed task.
2. Assert newest-first order, strict cursor pagination, no duplicate rows,
   stable next cursor, exclusion of `LATE_RESULT_IGNORED`, and absence of raw
   hashes/IDs/JSON/actors.
3. Assert route is GET-only, bounded, read-only, and returns stable
   `INVALID_REQUEST` for malformed cursor/limit.
4. Run the two focused test files; expected RED is missing service/routes.

**GREEN**

Implement the repository/service and lazy route wiring. Use existing session
factory conventions; do not call providers, RAG, models, or migrations at
startup. Run the focused tests, relevant Phase 6 repository/event tests, all
Phase 7E task-read/approval-read tests, OpenAPI export tests, Ruff/mypy, and
`git diff --check`.

**Commit**

`feat(phase7f): add durable activity history reads`

**Stop condition:** any SQL in the route, mutation during a GET, raw internal
field in the response, or nondeterministic tie handling blocks the task.

## Task 3: Add database-tail SSE activity streaming

**Files**

- Create `src/aaos/web/activity_stream.py`.
- Modify `src/aaos/web/routes.py` to register
  `GET /api/v1/activity/stream`.
- Create `tests/test_phase7f_activity_stream.py`.
- Extend `frontend/tests/proxy-config.test.ts` only if a stream-header proxy
  regression assertion is needed.

**Interfaces produced/consumed**

```text
iter_activity_sse(
    request: Request,
    service: ActivityReadService,
    *,
    after: ActivityCursor | None,
    sleep: Callable[[float], Awaitable[None]],
    clock: Callable[[], datetime],
) -> AsyncIterator[bytes]
```

The route returns a `StreamingResponse` whose body iterator is
`iter_activity_sse(request, service, after=after, sleep=asyncio.sleep,
clock=utc_now)`, with `media_type="text/event-stream"` and no-cache/no-buffer
headers. Query `after` takes
precedence over `Last-Event-ID`; no cursor starts at the current high-water
mark. Poll interval is 500 ms, batch size is 50, heartbeat is a comment every
15 seconds. Every durable row receives `id`, `event: activity`, and the exact
browser-safe `ActivityEvent` JSON. Invalid replay emits one bounded
`recovery_required` event and closes. No adapter/repository write is called.

**RED**

1. Use a deterministic fake read service, fake clock, and controllable async
   sleep to prove first event, multiple events, identical timestamps, strict
   cursor replay, heartbeat, disconnect cleanup, bounded batches, and no domain
   mutation.
2. Assert malformed cursor is rejected and stale/recovery condition never
   silently skips a durable row.
3. Assert response headers are `text/event-stream`, no-cache, and no-buffer.
4. Run `uv run pytest tests/test_phase7f_activity_stream.py -q`; expected RED
   is absent stream route/generator.

**GREEN**

Implement only the async generator and route. Do not add a broker or dependency.
Run focused stream tests, Activity history tests, FastAPI route/OpenAPI tests,
and frontend proxy tests. Run Ruff/mypy/diff checks.

**Commit**

`feat(phase7f): stream durable activity events`

**Stop condition:** adapter/provider I/O, an in-memory-only source, a stream
that mutates domain state, or a response that buffers/caches fails the task.

## Task 4: Implement passive System Health service and API

**Files**

- Create `src/aaos/web/health_read.py`.
- Modify `src/aaos/web/routes.py` to register `GET /api/v1/system-health`.
- Modify `src/aaos/main.py` for lazy health-service wiring only.
- Create `tests/test_phase7f_health_service.py`.
- Create `tests/test_phase7f_health_api.py`.
- Extend `tests/test_health.py` only to prove `/health` is unchanged.

**Interfaces produced/consumed**

```text
PassiveRagIndexReader.snapshot() -> RagIndexHealthSnapshot
SystemHealthService.check(*, now: datetime) -> SystemHealthResponse
```

`RagIndexHealthSnapshot` is a typed read-only value containing only the
persisted active-generation state and published coverage needed by the health
mapping: `configured: bool`, `active: bool`, `coverage: Literal["FULL",
"PARTIAL", "NONE"]`, and `failed: bool`. `PassiveRagIndexReader` is the
trusted protocol implemented by the existing repository-backed reader; it
does not expose embeddings, model handles, provider identities, paths, or raw
database rows. `SystemHealthService` receives a session factory, the existing
read-only `AcademicReadService`, a `PassiveRagIndexReader`, and an optional
component-registration snapshot from trusted composition. The service calls
only those read interfaces.

The service uses a session factory, a read-only academic reader, and a passive
persisted RAG-index reader. It performs `SELECT 1`, schema-readiness inspection,
bounded academic reads, and persisted generation/index reads. It never imports
the Phase 4 runtime builder, opens LM Studio, reads credentials, contacts
Moodle/Google, runs migrations, starts indexing, or changes rows. Required
database/academic components and optional `UNKNOWN`/`NOT_CONFIGURED` mappings
follow the architecture spec exactly.

**RED**

1. Test healthy empty local database, DB unavailable, schema-not-ready, and
   academic read failure.
2. Test RAG active/full, partial, and no-active-generation mappings using
   persisted rows only.
3. Test optional passive statuses and aggregation; assert no model/provider
   constructor/call, no writes, no credential/path/exception leakage.
4. Test `/health` response remains exactly the accepted process contract.
5. Run focused health tests; expected RED is missing service/route.

**GREEN**

Implement bounded local probes and the safe projection. Wire lazily and dispose
only owned engines. Run focused tests, Phase 7A health/lazy-wiring tests, RAG
repository regressions, OpenAPI export, Ruff, mypy, and diff check.

**Commit**

`feat(phase7f): add passive system health`

**Stop condition:** any health probe performs network/provider/model/migration
I/O or claims healthy status without evidence.

## Task 5: Regenerate OpenAPI/types and add typed frontend read clients

**Files**

- Do not modify `src/aaos/web/contracts.py`; Task 1 contracts remain in the
  dedicated activity/health modules and routes import them from there.
- Modify `frontend/lib/api/errors.ts` only for newly proven closed error codes.
- Create `frontend/lib/api/activity.ts`.
- Create `frontend/lib/api/health.ts`.
- Modify `frontend/lib/query/keys.ts`.
- Create `frontend/lib/query/activity.ts` and `frontend/lib/query/health.ts`.
- Regenerate `frontend/openapi/aaos-v1.json` and
  `frontend/types/api.generated.ts` through the existing command.
- Create `frontend/tests/activity-api.test.ts` and
  `frontend/tests/health-api.test.ts`.

**Interfaces produced**

```typescript
export function fetchActivity(
  options?: { before?: string; limit?: number; signal?: AbortSignal },
): Promise<ActivityHistoryResponse>;
export function fetchSystemHealth(
  signal?: AbortSignal,
): Promise<SystemHealthResponse>;
export const activityKeys: {
  history: (before?: string) => readonly ["activity", "history", string | undefined];
};
export function activityHistoryQueryOptions(
  before?: string,
): UseQueryOptions<ActivityHistoryResponse, Error>;
export function systemHealthQueryOptions(): UseQueryOptions<SystemHealthResponse, Error>;
```

All calls are same-origin relative GETs to `/api/v1/activity` and
`/api/v1/system-health`, with `Accept`, abort propagation, bounded query
encoding, generated response types, and existing error normalization. The
EventSource URL is defined separately in Task 6 and is not routed through
TanStack Query.

**RED**

1. Assert exact URLs/query encoding, GET-only method, abort signal, bounded
   limit/cursor validation, safe non-2xx normalization, query-key separation,
   and generated schemas for activity/health.
2. Run frontend focused tests; expected RED is absent clients/types/routes.

**GREEN**

Implement clients/query options and regenerate artifacts using the repository
OpenAPI workflow. Run focused tests, `npm run check:api-types`, frontend typecheck,
lint, full frontend tests, backend OpenAPI tests, Ruff, mypy, and diff check.

**Commit**

`feat(phase7f): add activity and health browser clients`

**Stop condition:** handwritten duplicate API types, cross-origin URLs, POSTs,
  or generated drift blocks the task.

## Task 6: Build the Activity page and native EventSource boundary

**Files**

- Create `frontend/components/aaos/activity-list-view.tsx`.
- Create `frontend/components/aaos/activity-stream.tsx` with a client-only
  hook/controller.
- Create `frontend/app/activity/page.tsx`.
- Create `frontend/tests/activity-views.test.tsx` and
  `frontend/tests/activity-stream.test.ts`.

**Interfaces produced**

```typescript
export function useActivityStream(options: {
  cursor: string | null;
  onEvent: (event: ActivityEvent, cursor: string) => void;
  onConnectionState: (state: "connected" | "reconnecting" | "unavailable") => void;
}): void;
export function ActivityListView(): JSX.Element;
```

The page is a Server Component shell with the smallest client boundary for
EventSource and local deduplication. It loads history through TanStack Query,
renders loading/empty/populated/error states, then opens one
`/api/v1/activity/stream?after=<cursor>` EventSource while mounted. Durable
rows are merged by `activity_id`; the latest cursor is advanced only after a
valid event. `recovery_required` closes the source, refetches history, and
reopens from the returned cursor. Unmount always calls `close()`. There is no
global stream, mutation control, retry action, fake activity, or live-health
claim. Existing `/tasks/<id>` and `/approvals/<id>` links are used only when a
resource ID is present.

**RED**

1. Component tests cover loading, empty, populated, history error, connected,
   reconnecting, and live-unavailable-with-history states.
2. Stream tests use a deterministic fake EventSource to prove one connection,
   cleanup, cursor advancement, duplicate suppression, recovery refetch, and
   no EventSource import in a Server Component.
3. Assert `UNKNOWN_OUTCOME` is rendered as unknown, never success/failure, and
   no private field appears in DOM text.
4. Run `npm test -- --run frontend/tests/activity-views.test.tsx
   frontend/tests/activity-stream.test.ts`; expected RED is absent page/hook.

**GREEN**

Implement the Activity view using `PageHeader`, `SectionCard`, existing status
and empty/error components, generated types, and the native EventSource API.
Run focused tests, all frontend tests, typecheck, lint, production build, API
drift, and `git diff --check`.

**Commit**

`feat(phase7f): add activity history and live view`

**Stop condition:** a persistent background connection, direct FastAPI URL,
  optimistic domain status, duplicate rows after reconnect, or a client write
  endpoint blocks the task.

## Task 7: Build the System Health page

**Files**

- Create `frontend/components/aaos/system-health-view.tsx`.
- Create `frontend/app/system-health/page.tsx`.
- Create `frontend/tests/health-views.test.tsx`.

**Interfaces produced**

```typescript
export function SystemHealthView(): JSX.Element;
```

The page calls `systemHealthQueryOptions`, renders overall and component states
with accessible text/icons (never color alone), and supports loading, error,
healthy, degraded, unavailable, unknown, and not-configured states. It has no
restart, reconnect, repair, clear-cache, execute-diagnostic, approval, or
provider controls. Activity connection status is not rendered as health.

**RED**

1. Test exact status labels, required/optional grouping, passive unavailable
   state, no fake green metrics, no credentials/paths, and responsive semantic
   headings.
2. Run `npm test -- --run frontend/tests/health-views.test.tsx`; expected RED
   is absent view/page.

**GREEN**

Implement the read-only view using existing AAOS components and generated
contracts. Run focused tests, all frontend tests, typecheck, lint, production
build, API drift, and diff check.

**Commit**

`feat(phase7f): add system health view`

**Stop condition:** fabricated uptime/latency, provider restart controls, or
  conflating SSE connection state with AAOS health fails the task.

## Task 8: Harden cross-surface integration and security boundaries

**Files**

- Create `tests/test_phase7f_integration.py`.
- Create `tests/test_phase7f_security.py`.
- Create `frontend/tests/activity-health-integration.test.tsx`.
- Extend `frontend/tests/proxy-config.test.ts` for SSE response-header and
  same-origin assertions if the focused proxy tests do not already cover them.
- No production file is expected to change in this task. If a regression
  proves that composition must be corrected, the only permitted production
  files are `src/aaos/web/routes.py`, `src/aaos/main.py`, or
  `frontend/next.config.ts`; any other file requires a new scope decision.

**RED**

1. Prove Activity/Health routes compose with Tasks, Approvals, Ask AAOS,
   Courses, Assignments, and Documents without changing their contracts.
2. Prove all 7F browser requests are same-origin GETs; no POST/PUT/PATCH/DELETE,
   provider URL, credentials, raw event field, SQL, path, traceback, or
   `TrustedApproverContext` reaches browser artifacts.
3. Prove `import aaos.main` and app construction do not open a database,
   initialize RAG/model runtime, contact providers, or start an SSE task.
4. Prove stream disconnect, history replay, cursor pagination, health checks,
   and client deduplication do not mutate Action/Approval/Operation/Task rows.
5. Run the focused integration/security suites; expected RED is any missing
   cross-surface assertion or leak.

**GREEN**

Make only narrowly necessary integration corrections. Run all Phase 7 tests,
Phase 6A/6B authority and unknown-outcome regressions, Phase 4/5 regressions,
backend/Frontend quality gates, OpenAPI drift, and diff check.

**Commit**

`test(phase7f): harden activity health boundaries`

**Stop condition:** any authority regression, artifact leak, hidden side effect,
  or weakened historical test requires a focused fix and re-review before Task 9.

## Task 9: Deterministic production acceptance and independent review

Task 9 is verification-only. It creates no product, test, migration, generated
artifact, or implementation commit.

**Acceptance preparation**

1. Verify branch `phase-07-dashboard`, clean tracked tree, expected implementation
   HEAD, no normal database modification, no ports 3000/8000, and no stale
   runtime.
2. Create/migrate only a disposable database through the normal Alembic chain;
   do not use `create_all()` for the production fixture.
3. Populate the approved deterministic Phase 7F fixture through existing fake
   Phase 6/7E repository/adapter harnesses. Freeze a manifest containing row
   identities, timestamps, event codes, expected order, and health states
   before any browser or model result is inspected. After freeze, no fixture
   row or expected result may change.

**Fresh evidence**

- Run focused Phase 7F tests, all Phase 7 tests, relevant Phase 4/5/6
  regressions, canonical `uv run pytest -q`, Ruff, mypy, `uv lock --check`,
  `git diff --check`, frontend tests, typecheck, lint, build, and API drift.
- Start FastAPI and `npm run start -- --hostname 127.0.0.1 --port 3000` only.
- Verify `/health`, direct history/stream/health APIs, and same-origin proxy
  responses. Confirm SSE content type, incremental delivery, replay, strict
  cursor behavior, and no mutation.
- At 1440, 1280, 768, and 390 widths verify Activity and System Health states,
  keyboard/focus behavior, readable mobile rows, no horizontal overflow, no
  Next dev badge, and no 7G controls.
- Perform console/network and browser artifact scans for credentials, paths,
  SQL, raw events, raw exceptions, provider calls, external-write endpoints,
  and unexpected requests. Persist screenshots plus semantic/network evidence.
- Stop servers, verify ports are inactive, delete only the disposable database,
  and require a clean tracked worktree. Do not create `phase-07-passed`.

**Independent review checklist**

Review the final implementation against the spec for authoritative event
source, no duplicate ledger, deterministic cursor/replay, no skipped or
duplicate rows, disconnect cleanup, same-origin streaming, GET-only/no
mutation, passive truthful health, import-inert startup, safe projections,
no migration, no Phase 7G leakage, and no real external writes. Classify
findings as CRITICAL/IMPORTANT/MINOR. Resolve every CRITICAL/IMPORTANT finding
with a new focused commit and rerun affected checks. Do not tune fixtures or
acceptance criteria after evidence is observed.

**Stop gate**

`HUMAN_REVIEW_OF_PHASE7F_IMPLEMENTATION_AND_ACCEPTANCE` — Phase 7F is not
human-accepted and Phase 7 remains IN_PROGRESS until that gate is explicitly
passed.

## Invariant-to-task coverage

| Invariant | Tasks |
| --- | --- |
| Existing ActionEvent/TaskRecord authority; no duplicate event log | 1, 2, 8, 9 |
| Exact closed browser Activity projection and no leakage | 1, 2, 6, 8, 9 |
| Deterministic ordering, identical timestamps, cursor pagination | 1, 2, 3, 8, 9 |
| Durable replay, reconnect, no skipped/duplicate events | 3, 6, 8, 9 |
| Same-origin non-buffered SSE and disconnect cleanup | 3, 6, 8, 9 |
| GET-only/no mutation/no authority change | 2, 3, 4, 6, 7, 8, 9 |
| `/health` preserved and passive detailed health | 4, 7, 8, 9 |
| No model/RAG/provider/credential/external-write side effects | 2, 3, 4, 8, 9 |
| Import-inert startup and bounded local resource use | 3, 4, 8, 9 |
| OpenAPI/generated type synchronization | 5, 8, 9 |
| Responsive/accessibility/browser production acceptance | 6, 7, 9 |
| No Phase 7G or Phase 6C leakage | Global constraints, 8, 9 |

## Plan self-review

Every implementation task names exact files, public interfaces, focused RED
tests, smallest GREEN boundary, regression commands, and a commit message.
Task dependencies are ordered so no task consumes a later uncommitted file.
The plan contains no migration, placeholder, open-ended provider probe,
background worker, or Phase 7G acceptance claim. The only intentional
discovery limitation is the absence of a global durable event ID; the compound
cursor and strict ordering are explicit and shared by history, SSE, and client
deduplication. If production proxy streaming fails its focused test, the plan
stops rather than bypassing same-origin routing.

## Execution handoff

Implementation is not authorized by this plan. Human review must approve the
architecture and this plan first. If approved, execute Tasks 1–8 sequentially
with one commit per task, then run Task 9 verification-only. Do not begin
Phase 7G or create `phase-07-passed`.
