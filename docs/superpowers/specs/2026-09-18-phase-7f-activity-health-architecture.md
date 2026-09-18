# Phase 7F Activity, SSE, and System Health Architecture

**Status:** Discovery-complete design for human review; implementation is not
authorized by this document.

**Baseline:** `phase-06b-passed` at
`40ff9d3ef6e0e3010982dabfb37cb597aceeb6ed`, with Phase 7E human-accepted at
implementation boundary `459431a11f81d567642b3d531a2db4c2ad4976dc` and the
current Phase 7 governance checkpoint at `e2e2f5f23407240d6372eefdec02a38bb1a3ea2e`.

**Scope:** Phase 7F only. Activity is read-only operational history, SSE is a
notification/replay transport, and System Health is a passive browser
projection. Phase 6/7E authority remains unchanged. Phase 7G is not included.

## 1. Discovery findings

### 1.1 Durable sources

The repository contains these durable records:

| Source | Owner | Durable guarantees | Browser suitability |
| --- | --- | --- | --- |
| `action_events` / `ActionEventORM` | Phase 6A `ApprovalRepository` | Append-only rows, stable `event_id`, per-action atomic `sequence`, UTC `occurred_at`, typed serialized details, and transactional insertion with lifecycle/CAS changes | Not directly safe: details include hashes, operation identities, approval identities, and trusted actors |
| `task_records` / `TaskRecordORM` | Phase 7E `TaskRepository` | Durable task identity, idempotent request binding, creation/update timestamps, and CAS creation state | Safe only after explicit projection; it has no event sequence and no provider payload |
| `approval_records` / `ApprovalRecordORM` | Phase 6A | Approval state, expiry, decision timestamps, and action binding | Not directly safe: approver context and binding hashes are private |
| `operation_records` / `OperationRecordORM` | Phase 6A | One operation, durable dispatch/outcome/reconciliation state and revision | Not directly safe: receipt evidence and internal operation identity are private |

`AgentEvent` is an in-memory Phase 5 sink and is not restart durable. RAG and
inference telemetry are not a user-facing durable activity stream. No existing
global ActivityRecord table, audit log, global sequence, SSE helper, or event
bus exists. `GET /health` is the only current health route and reports process
liveness (`status`, `service`, `version`) without opening the database or
contacting a provider.

### 1.2 Event creation and ownership

Phase 6 service transitions create `ActionEventDraft` values and append them
through `append_action_event` in the same repository transaction as the
authoritative lifecycle/approval/operation change. The event kinds are closed:

`ACTION_PREPARED`, `APPROVAL_REQUESTED`, `APPROVAL_DECIDED`,
`APPROVAL_CONSUMED`, `DISPATCH_STARTED`, `OUTCOME_RECORDED`,
`RECONCILIATION_RECORDED`, `ACTION_INVALIDATED`, and `LATE_RESULT_IGNORED`.

Per-action sequence allocation is database-atomic through
`action_event_counters`; `(action_id, sequence)` is unique. Events survive
process restart. There is no task ID on an ActionEvent, so a browser projection
joins it through the unique Phase 7E `task_records.action_id` link. Orphaned
actions are omitted rather than exposing internal action identifiers.

Task creation and preparation failure have no corresponding ActionEvent. A
bounded history projection may therefore derive `TASK_CREATED` and
`PREPARATION_FAILED` from the immutable `TaskRecord` identity/creation fields,
but it must not pretend these are Phase 6 events. The derived records are
read-time views, not a second persisted event log.

## 2. Authoritative Activity decision

Phase 7F uses a composite projection over existing authoritative records:

1. `ActionEventORM` is the authoritative durable transition stream for every
   linked Phase 6 action.
2. `TaskRecordORM` supplies the browser task identity, creation event, and
   action join.
3. `ApprovalRecordORM` and `OperationRecordORM` are joined only to verify the
   current safe status and to suppress malformed/orphaned projections; they do
   not become a second event source.

No `activity_records` table, duplicate audit ledger, migration, event bus, or
parallel write path is introduced. If a future requirement needs a durable
global activity identifier or retention policy, that is a separately reviewed
architecture change rather than a hidden 7F migration.

Activity is subordinate to Phase 6/7E authority. A stale or inconsistent event
row is rejected from the browser projection and reported only through the
existing safe internal error path; it is never repaired by the read service.

## 3. Browser-safe activity contract

The backend adds closed, frozen Pydantic projections in a new web activity
module. The public response uses tuples, not arbitrary mutable lists:

```text
ActivityCategory = TASK | APPROVAL | INTEGRATION | SYSTEM
ActivityEventCode =
  TASK_CREATED | PREPARATION_FAILED | ACTION_PREPARED |
  APPROVAL_REQUESTED | APPROVED | REJECTED | CLAIMED | EXECUTING |
  CONFIRMED_SUCCESS | CONFIRMED_FAILURE | UNKNOWN_OUTCOME |
  INVALIDATED | EXPIRED | REVOKED | RECONCILED
ActivityStatus = PENDING | APPROVED | REJECTED | CLAIMED | EXECUTING |
  CONFIRMED_SUCCESS | CONFIRMED_FAILURE | UNKNOWN_OUTCOME | NOT_DISPATCHED |
  INVALIDATED | EXPIRED | REVOKED
ActivityResource = TASK | APPROVAL
```

`ActivityEvent` contains exactly:

```text
schema_version: "phase7-activity-v1"
activity_id: bounded opaque stable ID
occurred_at: aware UTC timestamp
category: ActivityCategory
event_code: ActivityEventCode
status: ActivityStatus | None
resource_type: ActivityResource | None
resource_id: bounded task/approval ID | None
title: bounded application-owned display text
detail: bounded application-owned display text | None
context: typed ActivityContext union | None
```

The only context variants are `TaskActivityContext(task_id)`,
`ApprovalActivityContext(approval_id, task_id)`, and
`OperationActivityContext(status)`. Context is a discriminated closed union;
it is never a free-form dictionary. No activity field contains action IDs,
operation IDs, claim identities, actor/context IDs, fingerprints, canonical
arguments, target/precondition data, provider payloads, raw detail JSON,
exception text, SQL, filesystem paths, credentials, model text, or URLs to
unimplemented routes.

### 3.1 Supported event mapping

| Browser event | Authoritative source | Safe projection | Historical/live |
| --- | --- | --- | --- |
| `TASK_CREATED` | `TaskRecord.created_at` | Task resource only | Both |
| `PREPARATION_FAILED` | `TaskRecord.creation_state=FAILED` and closed failure code | Task resource and safe failure status | Both |
| `ACTION_PREPARED` | `ACTION_PREPARED` ActionEvent | “Action prepared” with task link | Both |
| `APPROVAL_REQUESTED` | `APPROVAL_REQUESTED` ActionEvent | Approval resource and expiry status | Both |
| `APPROVED` / `REJECTED` / `REVOKED` / `EXPIRED` | `APPROVAL_DECIDED` plus authoritative approval/action state | Closed decision status; never raw reason/context | Both |
| `CLAIMED` | `APPROVAL_CONSUMED` ActionEvent | Task/approval claimed | Both |
| `EXECUTING` | `DISPATCH_STARTED` ActionEvent | Task executing | Both |
| `CONFIRMED_SUCCESS` / `CONFIRMED_FAILURE` / `UNKNOWN_OUTCOME` / `NOT_DISPATCHED` | `OUTCOME_RECORDED` plus operation/action state | Closed outcome status | Both |
| `RECONCILED` | `RECONCILIATION_RECORDED` ActionEvent | Reconciliation status only | Both |
| `INVALIDATED` | `ACTION_INVALIDATED` ActionEvent | Trusted invalidation status | Both |

`LATE_RESULT_IGNORED` remains a private audit event and is not a user-facing
Activity row. It cannot be converted into a current outcome. No event is
invented for an unsupported state.

## 4. Ordering and cursor

Existing per-action sequence numbers are not globally ordered, and timestamps
are not unique. The read projection therefore defines a total order without
changing persistence:

```text
(occurred_at UTC,
 source_rank [TASK_DERIVED=0, ACTION_EVENT=1],
 stable_source_id,
 source_sequence)
```

`stable_source_id` is the task ID for derived task rows and the stored
`ActionEvent.event_id` for action rows. The internal `action_id` is used only
to join the server-side projection; it is never returned as a browser resource
ID and is never placed in the cursor. `source_sequence` is zero for derived
TaskRecord rows and the stored ActionEvent sequence otherwise. Ties are
therefore deterministic across connections and restarts.

History returns newest-first. The response is:

```text
ActivityHistoryResponse:
  events: tuple[ActivityEvent, ...]
  next_cursor: opaque string | None
```

The cursor is versioned URL-safe base64 of canonical JSON containing only the
last total-order tuple (`v`, UTC timestamp, source rank, stable source ID,
sequence). It is a position token, not authority and not a raw query/filter
language. Unknown fields, invalid encoding, invalid UTC, invalid sequence, or
oversized values produce the existing bounded `INVALID_REQUEST` error. The
default page size is 50 and the hard maximum is 200. History uses a strict
“before” position for older pages; the stream uses a strict “after” position.

The cursor is not a claim that the underlying event still exists. Because 7F
introduces no retention/deletion policy, a structurally valid cursor remains
replayable. If a future retention policy makes a cursor unreplayable, the
server must return a closed recovery condition rather than silently skip rows.

## 5. History API

```text
GET /api/v1/activity?limit=<1..200>&before=<cursor>
```

Both parameters are optional (`limit=50` by default). No arbitrary filtering,
SQL, event-kind expressions, or provider search is exposed. The route calls a
read-only `ActivityReadService.list_history(limit, before)` and maps malformed
cursor/limit input to the existing safe API error boundary. It never mutates a
cursor, marks events read, or loads providers.

## 6. SSE architecture

The new read-only endpoint is:

```text
GET /api/v1/activity/stream?after=<cursor>
```

The FastAPI route returns Starlette/FastAPI `StreamingResponse` with
`text/event-stream`, `Cache-Control: no-cache, no-store`, and
`X-Accel-Buffering: no`. No new SSE dependency is required. The current
Next.js rewrite maps `/api/:path*` to the backend origin without changing
method or response framing; focused tests must prove that the production
proxy preserves the stream headers and does not cache/buffer it.

The stream uses a read-only database tail, not an in-memory event bus:

1. Validate `after` query or `Last-Event-ID` (query wins; header is the native
   EventSource reconnect value).
2. If neither is provided, capture the current high-water cursor and wait for
   later rows; the browser obtains existing history through HTTP first.
3. Poll the composite projection every 500 ms with a bounded batch of 50.
4. For each row strictly after the cursor, emit:

```text
id: <opaque cursor after this row>
event: activity
data: <the exact serialized ActivityEvent JSON>

```

5. Emit a comment heartbeat every 15 seconds when no durable row is ready.
6. Check `request.is_disconnected()` before each poll and batch; close the
   generator without changing any domain record.

The stream never emits raw ORM rows or private event details. There is no
ephemeral progress stream in 7F; existing RAG progress remains outside this
slice. If a cursor cannot be safely replayed, emit one bounded
`recovery_required` event without an ID and close. The frontend then refetches
history and opens a fresh stream. No silent event loss is permitted.

### 6.1 Reconnect and duplicate rules

The Activity page loads history, remembers the final cursor, then opens one
EventSource while mounted. Native EventSource reconnects with `Last-Event-ID`;
the hook also carries the cursor in the URL after a recovery refetch. The
server's strict-after query and the client's `activity_id` set prevent duplicate
rendering. A page refresh starts from HTTP history, not from an in-memory
client cache. A disconnect has no domain side effect.

The stream is not authority, an acknowledgement channel, a retry mechanism, or
the database. No POST/PUT/PATCH/DELETE is reachable from the Activity page.

## 7. System Health

`GET /health` remains byte-for-byte semantically unchanged: it is process
liveness only and returns `status`, `service`, and `version`.

Phase 7F adds a separate read-only endpoint:

```text
GET /api/v1/system-health
```

The browser contract is:

```text
HealthStatus = HEALTHY | DEGRADED | UNAVAILABLE | UNKNOWN | NOT_CONFIGURED
HealthComponentName = DATABASE | ACADEMIC_DATA | ACADEMIC_SYNC |
  DOCUMENT_PIPELINE | RAG_INDEX | INFERENCE | GOOGLE_CALENDAR

HealthComponent:
  name: HealthComponentName
  status: HealthStatus
  required: bool
  detail_code: closed HealthDetailCode

SystemHealthResponse:
  schema_version: "phase7-health-v1"
  checked_at: aware UTC
  overall: HealthStatus
  components: tuple[HealthComponent, ...]
```

The service performs bounded local reads only:

- `DATABASE` is required and runs `SELECT 1` plus a safe current-schema
  check. Failure is `UNAVAILABLE` and makes overall health `UNAVAILABLE`.
- `ACADEMIC_DATA` is required and runs a bounded active-course/assignment
  read through the existing read service. Empty data is healthy, not failure.
- `RAG_INDEX` is optional and reads persisted Phase 4 generation/index rows;
  no embedding, model, FTS rebuild, or query is run. `ACTIVE` with complete
  published coverage is `HEALTHY`; partial/failed coverage is `DEGRADED`; no
  active generation is `NOT_CONFIGURED`.
- `ACADEMIC_SYNC`, `DOCUMENT_PIPELINE`, `INFERENCE`, and `GOOGLE_CALENDAR`
  expose only a truthful passive state. No current cheap durable health signal
  or provider-safe probe exists for these components, so the initial service
  returns `UNKNOWN` with a closed `PASSIVE_STATUS_UNAVAILABLE` code when the
  subsystem is configured/expected, and `NOT_CONFIGURED` only when the trusted
  composition has no registered capability. It never contacts Moodle, LM
  Studio, Google, or another provider to improve the label.

Overall aggregation follows the approved design: required database/academic
failure is `UNAVAILABLE`; required unknown or any enabled optional degraded,
unavailable, or unknown component is `DEGRADED`; `HEALTHY` requires every
required component healthy and every enabled optional component healthy; an
optional `NOT_CONFIGURED` component does not make the system unhealthy. React
renders these states and never infers them from strings.

The response contains no database URL, filesystem path, credential/provider
identity, token, raw exception, package inventory, network address, model cache
path, or health history. There are no restart/reconnect/repair buttons.

## 8. Backend and frontend boundaries

Backend modules are additive and read-only:

```text
src/aaos/web/activity.py       typed projections, cursor, projector
src/aaos/web/activity_read.py  SQLAlchemy read repository/service
src/aaos/web/health.py         passive health contracts/service
src/aaos/web/routes.py         GET history, GET stream, GET system-health
src/aaos/main.py               lazy composition and shutdown only
```

The route layer performs no SQL construction and no provider/model calls. The
read repository owns joins and bounded ordering. `main.py` keeps import/startup
inert: constructing the app does not open a database, create a model runtime,
start a task, or start a poller. An SSE poll loop exists only for an active
request and is cancelled on disconnect/shutdown.

Frontend modules reuse the accepted same-origin architecture:

```text
frontend/lib/api/activity.ts
frontend/lib/api/health.ts
frontend/lib/query/activity.ts
frontend/lib/query/health.ts
frontend/lib/query/keys.ts
frontend/components/aaos/activity-list-view.tsx
frontend/components/aaos/activity-stream.tsx   (client-only)
frontend/components/aaos/system-health-view.tsx
frontend/app/activity/page.tsx
frontend/app/system-health/page.tsx
```

`fetchActivity` and `fetchSystemHealth` call only same-origin `/api/v1/...`
GET paths with `Accept`, `AbortSignal`, generated OpenAPI types, and existing
safe error normalization. The EventSource hook is the only client-only live
boundary; it is mounted by Activity, cleans up on unmount, and does not become
a global connection. Server Components never import it.

Activity states are loading, empty, populated, history error, connected,
reconnecting, and live-unavailable-with-history. Health states render the
closed component statuses. Navigation links are created only for existing
`/tasks/<id>` and `/approvals/<id>` routes; no dead or provider links are
invented.

## 9. Acceptance and fixture design

Phase 7F acceptance uses a disposable database migrated through the existing
Alembic chain. It adds no migration and never uses `create_all()` for the
production fixture. A deterministic local harness uses the existing Phase 6A
fake adapter/Phase 7E trusted repositories to create a frozen set of linked
task/action/approval/operation records and ActionEvents covering requested,
approved, rejected, executing, confirmed success, confirmed failure,
`UNKNOWN_OUTCOME`, invalidated/expired/revoked, and a preparation-failure
TaskRecord. The fixture is frozen before any browser result is inspected.

No model calls, RAG calls, provider network, Google/Moodle write, credential
access, or external write are permitted; no real external write is required
for Phase 7F acceptance. A new live event is produced after
the browser stream connects by a deterministic local fake-adapter/repository
transition only. If a desired event cannot be generated without a real
provider write, that event is omitted from acceptance rather than simulated by
editing production rows after the freeze.

Required semantic evidence:

- history order and cursor pagination, including identical timestamps;
- browser receives one newly persisted event over SSE without reload;
- refresh preserves the event through history;
- reconnect from the last cursor replays no duplicate and skips no durable row;
- stream disconnect performs no domain mutation;
- `UNKNOWN_OUTCOME` is visibly distinct and never rendered as failure/success;
- history and SSE contain no raw payload, context, fingerprint, path, SQL,
  credential, exception, or provider field;
- health reports a healthy core and an intentionally passive unavailable/
  unknown optional subsystem without fake green claims;
- direct FastAPI and same-origin proxy responses agree;
- production `next start` is used at 1440, 1280, 768, and 390 widths;
- browser console/network and artifact scans show same-origin GET-only 7F
  traffic, no credentials/provider calls, no Next development badge, no
  horizontal overflow, and no Phase 7G controls.

## 10. Security and invariants

1. Activity is a projection, not the audit ledger; Phase 6 authority always
   wins over a stale browser event.
2. History and SSE are GET-only and cannot acknowledge, execute, reconcile,
   retry, approve, reject, or mutate cursors server-side.
3. One composite cursor order is used by history, SSE IDs, replay, and client
   deduplication.
4. The same closed `ActivityEvent` projection is used for HTTP and SSE.
5. No raw ActionEvent, approval row, operation row, TaskRecord internals, or
   RAG/inference event is serialized to the browser.
6. Browser contracts are frozen, strict, explicit, bounded, and extra-forbid.
7. `/health` remains process liveness; detailed health is separate and passive.
8. Health never runs writes, migrations, provider requests, model loads,
   ingestion, indexing, or credential refresh.
9. Importing `aaos.main` opens no database and starts no loop.
10. No Phase 6/7E state transition, approval context, capability, provider
    authority, retry, or `UNKNOWN_OUTCOME` rule is changed.
11. No migration, new event table, broker, WebSocket, or Phase 7G feature is
    introduced.

## 11. Known limitations and stop conditions

- The composite projection has no global database sequence. Its versioned
  opaque tuple cursor is deterministic for the current no-retention local
  deployment, but a future retention policy needs a separately reviewed
  cursor/retention design.
- Task creation/preparation failure is derived from TaskRecord state because
  Phase 6 ActionEvent has no task event. It is explicitly not represented as
  a Phase 6 event.
- The current Next rewrite is a generic same-origin rewrite, not a dedicated
  SSE proxy. Production acceptance must prove stream headers and incremental
  delivery; if it buffers or caches, stop and redesign rather than exposing
  FastAPI directly.
- Several optional subsystem health states are honestly `UNKNOWN` or
  `NOT_CONFIGURED` because no side-effect-free durable signal exists. No
  percentage, latency chart, uptime claim, or provider availability claim is
  manufactured.
- If deterministic replay cannot be implemented from this composite source,
  if a global durable ID or retention becomes mandatory, or if health would
  need provider/model I/O, stop for a new architecture review.

## 12. Explicit non-goals

Phase 7F does not add task creation, approval decisions, execution,
reconciliation controls, retry actions, provider integrations, credentials,
model/RAG execution, Phase 6C, WebSockets, brokers, a new distributed job
framework, migrations, or Phase 7G acceptance/freeze logic.

Implementation remains unauthorized until a reviewed Phase 7F plan is accepted
by a human. The next gate is
`HUMAN_AUTHORIZATION_FOR_PHASE7F_IMPLEMENTATION`.
