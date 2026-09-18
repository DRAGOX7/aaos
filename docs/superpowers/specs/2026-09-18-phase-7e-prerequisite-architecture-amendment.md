# Phase 7E Prerequisite Architecture Amendment: Durable Tasks and Trusted Local Approval

**Status:** Proposed prerequisite architecture; implementation is not authorized by this document.

**Starting checkpoint:** `2de4a133257f6b5f613422dd90be5c70a81828ce` on `phase-07-dashboard`.

**Scope:** Phase 7E only. Phase 7F (Activity/SSE/System Health), Phase 6C, and all new provider integrations remain out of scope.

## 1. Decision summary

Phase 7E requires two platform prerequisites that are absent from the accepted
Phase 7D checkpoint:

1. a durable record for a browser-initiated user task; and
2. a trusted server-derived approver context for browser approval decisions.

The existing Phase 5 `AgentTask` is an immutable, in-memory orchestration input.
The existing Phase 6A `PreparedAction`, `ApprovalRecord`, and `OperationRecord`
are durable authority records, not user-task records. They must remain separate.

This amendment introduces a small additive `task_records` ledger that points to
the existing Phase 6 action identity. The effective task execution status is
derived from existing Phase 6 lifecycle, approval, and operation records after an
action is linked. The ledger does not duplicate provider payloads or Phase 6
state and does not change Phase 6 transitions, retries, or outcome semantics.

Browser approval commands obtain `TrustedApproverContext` from a trusted
server-side `LocalApproverContextProvider`. Browser JSON can identify only the
approval and decision endpoint; it cannot supply identity, role, capability,
context, provider data, or action arguments. The current guarantee is an
explicitly local one: a same-machine trusted AAOS user. Remote or multi-user
deployment requires replacing this provider with authenticated principal
resolution before exposure beyond the local host.

No implementation, migration, browser route, frontend code, or provider write is
authorized by this amendment.

## 2. Existing-domain reconstruction

### 2.1 Phase 5

`src/aaos/agent/contracts.py` defines:

- `AgentTaskId`;
- immutable `AgentTask` containing request text, trusted initial scope, metadata,
  and creation time; and
- `AgentState` with `CREATED`, `PLANNING`, `TOOL_PENDING`, `TOOL_RUNNING`,
  `OBSERVING`, `COMPLETED`, `FAILED`, and `CANCELLED`.

`src/aaos/agent/state.py` owns the state machine in memory. The orchestrator
creates and transitions it; no SQLAlchemy model, repository, durable task ID
lookup, restart recovery, or browser projection exists. Phase 5 explicitly
keeps task state and any approved local write on injected disposable paths in
tests and does not authorize general persistence.

The Phase 5 task is therefore an execution input/run context, not a durable
user-facing work item.

### 2.2 Phase 6A

The accepted Phase 6A domain has these distinct records and services:

- `PreparedAction`: immutable approval-bound actor, target, action type,
  canonical arguments/hash, tool/schema/policy fingerprints, remote
  precondition, expiry, and operation identity;
- `ApprovalRecord`: one human approval binding and its state;
- `ApprovalConsumption`: database-enforced one-shot consumption fact;
- `OperationRecord`: one execution operation, durable dispatch state, outcome,
  receipt/reconciliation evidence, and revision;
- `ActionLifecycle` and append-only `ActionEvent` records; and
- `src/aaos/approval/service.py` functions for preparation, approval decisions,
  claim, dispatch, outcome persistence, recovery, and reconciliation.

`TrustedApproverContext` is a trusted value containing `ActorIdentity` and a
bounded context ID. `approve_approval`, `reject_approval`, and `revoke_approval`
require it and compare it to the bound approval. Capability permission remains
separate from approval. Claim and dispatch revalidate capability and binding.

`OperationRecord` has exactly one execution attempt. Durable `EXECUTING` is
persisted before adapter I/O. Transport uncertainty is
`UNKNOWN_OUTCOME`; reconciliation never calls `execute()` and no automatic
retry is permitted. The adapter boundary receives only `ExecutableAction`.

### 2.3 Phase 6B

The frozen Google Calendar adapter composes the existing Phase 6 service and
trusted configuration. Its narrow client/provider boundary is not changed by
Phase 7E. Phase 6B acceptance performed exactly one real Google Calendar
CREATE, reached `CONFIRMED_SUCCESS`, and is frozen at
`40ff9d3ef6e0e3010982dabfb37cb597aceeb6ed` (`phase-06b-passed`). No additional
real external write is authorized for this design or its acceptance harness.

## 3. Durable task meaning

A `TaskRecord` represents one browser-visible user intent and its bounded
trusted lifecycle. It is not a copy of `AgentTask`, an approval, or an
execution attempt.

For the first supported task kind, one task creates at most one
`PreparedAction`, one approval binding, and one Phase 6 operation. A new
execution after reconciliation would require a new task and new Phase 6
identity; Phase 7E never adds a retry path.

The task is created before the prepared action is linked so that an idempotent
browser request has a durable identity even if preparation crashes. A task may
exist without an action while trusted preparation is in progress or has failed.
The preparation sequence links the action before issuing the approval. If a
crash occurs after action preparation but before linkage, the action remains an
unapproved Phase 6 `PREPARED` record and no browser approval is exposed. If a
crash occurs after linkage but before approval issuance, recovery observes any
existing approval by action identity before calling `request_approval`, so the
same task cannot receive duplicate approvals. Once linked, action, approval,
and operation state are authoritative and task status is derived. A successful task can exist without external execution only
when it is rejected, expired, revoked, invalidated, or preparation failed; a
successful external write always has the existing Phase 6 operation record.

The initial browser-initiable task kind is the closed `CALENDAR_CREATE_EVENT`
request already supported by `CalendarCreateRequest` and the Phase 6B action
definition. It accepts only validated summary, start, end, timezone,
description, and location fields plus a transport idempotency key. It does not
accept provider IDs, tool names, capability values, raw canonical arguments,
credentials, or runtime policy. Task creation prepares and requests approval;
it does not execute automatically.

## 4. Task state model

Only the pre-action preparation state is stored on `TaskRecord`. Once an action
is linked, the public task state is a deterministic projection of Phase 6
records.

| Browser task state | Source of truth | Meaning | Terminal |
| --- | --- | --- | --- |
| `PREPARATION_FAILED` | `TaskRecord.creation_state=FAILED` plus closed error code | No action was safely bound | Yes |
| `PREPARING` | `TaskRecord.creation_state=CREATED`, or linked action is `PREPARED` without an approval row | Trusted service is still binding or recovering the approval; no browser approval is exposed yet | No |
| `AWAITING_APPROVAL` | linked `ActionLifecycle` is `AWAITING_APPROVAL` and approval is `PENDING` | Action is bound and awaits the trusted approval decision | No |
| `APPROVED` | linked action `APPROVED` and approval `APPROVED` | Human approval is recorded; no execution has started | No |
| `CLAIMED` | linked action `CLAIMED` | One-shot approval consumption is durable | No |
| `EXECUTING` | linked operation `EXECUTING` | Dispatch start is durable; adapter attempt may be active | No |
| `UNKNOWN_OUTCOME` | linked operation `UNKNOWN_OUTCOME` or `RECONCILIATION_PENDING` | AAOS cannot prove whether the external side effect occurred | No |
| `CONFIRMED_SUCCESS` | linked action/operation success | Authoritative success evidence exists | Yes |
| `CONFIRMED_FAILURE` | linked action/operation authoritative failure | The invocation failed and cannot later succeed | Yes |
| `NOT_DISPATCHED` | precondition-stale operation result | No external side effect was dispatched/committed | Yes |
| `REJECTED` | linked action `REJECTED` | Human rejected the approval | Yes |
| `INVALIDATED` | linked action `INVALIDATED` | Trusted policy/schema/binding/precondition drift invalidated it | Yes |
| `EXPIRED` | linked action `EXPIRED` | Action or approval expiry closed it | Yes |
| `REVOKED` | linked action `REVOKED` | Human or trusted-system revocation closed it | Yes |

`UNKNOWN_OUTCOME` is never mapped to `FAILED`, `CONFIRMED_FAILURE`, or a
successful result. No browser action can retry it. Reconciliation remains the
existing trusted Phase 6 operation.

## 5. Proposed persistence model

### 5.1 `task_records`

The additive table is owned by Phase 7E. It references, but does not alter, the
Phase 6A tables.

| Field | Type/constraint | Source and purpose | Browser-visible | Mutable/owner |
| --- | --- | --- | --- | --- |
| `task_id` | `String(128)` primary key | Server-issued durable task identity | Opaque ID | Immutable; task service |
| `task_kind` | bounded string/check, initially `CALENDAR_CREATE_EVENT` | Closed browser command discriminant | Closed display kind | Immutable; task service |
| `actor_identity` | `String(128)` non-null | Server-derived local principal used as Phase 6 actor | No | Immutable; trusted principal provider |
| `idempotency_key` | `String(128)` non-null; unique with actor | Client request deduplication token, not authority | No | Immutable; HTTP/task service |
| `request_fingerprint` | 64-char SHA-256 | Canonical closed request identity | No | Immutable; task service |
| `creation_state` | bounded check: `CREATED`, `ACTION_BOUND`, `FAILED` | Minimal pre-action crash/recovery state | Derived label only | Mutable by task service with CAS |
| `failure_code` | nullable closed enum: `INVALID_REQUEST`, `CAPABILITY_DENIED`, `PREPARATION_FAILED` | Safe preparation failure category | Safe error category only | Set once by task service |
| `action_id` | nullable FK to `prepared_actions.action_id`, unique | Link to authoritative Phase 6 action | Opaque related ID only if needed | Set once; task service |
| `created_at` | aware UTC | Audit ordering | Yes | Immutable |
| `updated_at` | aware UTC | Projection refresh and CAS | Yes | Task service CAS |

The table has no provider payload, canonical argument blob, credential,
authorization header, local path, exception text, provider response, or raw
Phase 6 event payload. `ON DELETE RESTRICT` preserves Phase 6 lineage. No
cascade or destructive cleanup is introduced.

### 5.2 Derived relationships

```text
TaskRecord --(unique nullable action_id FK)--> PreparedAction
PreparedAction --action_id--> ActionLifecycle
PreparedAction --action_id--> ApprovalRecord
PreparedAction --operation_id--> OperationRecord
ApprovalRecord --approval_id--> ApprovalConsumption (when consumed)
PreparedAction --action_id--> ActionEvent* (private audit, never raw browser API)
```

There is no task foreign key added to Phase 6 tables. Operation identity is
derived from the linked immutable action. One task/action link is sufficient for
6A's one-operation/no-retry rule. If future requirements need multiple
attempts, that is a new architecture decision, not a hidden extension here.

### 5.3 Migration

The current Alembic head is `0005_phase6a_approval_core`. Implementation must
generate the next revision using the repository's existing Alembic naming and
ordering convention; it must not guess a revision ID. Conceptually it is an
additive Phase 7E task-record migration containing only `task_records`, its
checks, indexes, unique actor/idempotency identity, and the restricted FK to
`prepared_actions`.

Upgrade creates the table and indexes. Downgrade drops only `task_records`
after verifying no rows remain; it never alters or deletes Phase 6A tables.
No Phase 6 column, trigger, enum, lifecycle transition, or outcome semantics
change.

## 6. Trusted local approver boundary

`LocalApproverContextProvider` is a server-side protocol:

```python
class LocalApproverContextProvider(Protocol):
    def current(self) -> TrustedApproverContext: ...
```

The default implementation reads non-secret, operator-controlled process
configuration (identity and stable context ID) at the trusted FastAPI
boundary. Suggested names are `AAOS_LOCAL_APPROVER_IDENTITY` and
`AAOS_LOCAL_APPROVER_CONTEXT_ID`; their values never enter browser payloads or
logs. Missing or invalid configuration fails closed with a safe API error and
does not call Phase 6.

Tests inject a deterministic provider returning a `TrustedApproverContext`.
The stable context is deliberately persisted only inside the bound approval;
it is not exposed to the browser. The current guarantee is “same-machine
trusted local AAOS user,” not full authentication. Before remote or
multi-user deployment, this provider must be replaced by an authenticated
principal/session resolver with authorization and CSRF protections.

## 7. Browser mutation threat model

The existing deployment is Next.js same-origin proxy to loopback FastAPI with
no wildcard CORS and no user session. Phase 7E therefore requires:

- mutations only through `/api/v1` FastAPI routes reached by the approved
  same-origin proxy;
- explicit `application/json` content type and bounded body parsing;
- an allowlist of configured loopback `Host`/`Origin` values for mutation
  requests; foreign or malformed values fail closed;
- no browser-supplied approver identity, role, capability, provider target,
  trusted context, or action arguments outside the closed task contract;
- no optimistic approval state; after a successful or uncertain response the
  client refetches authoritative task/approval state;
- no automatic mutation retry; and
- no new permissive CORS middleware.

This protects against accidental cross-origin browser requests in the local
deployment. It does not claim resistance to a malicious same-machine process
or a future remote browser. Remote deployment requires real authentication,
per-user approval authorization, CSRF/session protection, and deployment
origin configuration before enabling these routes.

## 8. Command semantics

### 8.1 Task creation

`POST /api/v1/tasks` accepts a closed discriminated request for
`CALENDAR_CREATE_EVENT` and an idempotency key. The trusted application:

1. validates and canonicalizes the request using the existing Calendar
   canonicalization contract;
2. derives the actor, target, action type, tool/schema/policy versions,
   capability policy, and remote precondition from trusted configuration;
3. creates or returns the idempotent `TaskRecord`;
4. calls existing `prepare_action`;
5. CAS-links the returned action to the task; and
6. calls existing `request_approval` bound to the server-derived local
   approver context only after linkage. Recovery first reads the approval by
   action identity and issues it only when none exists.

It returns a safe task/approval projection. It never claims approval, claims
the operation, dispatches an adapter, or performs a provider write. A repeated
request with the same actor/idempotency key and request fingerprint returns the
same task. A conflicting fingerprint returns a stable idempotency conflict.

### 8.2 Approval decisions

The only browser decision commands are:

```text
POST /api/v1/approvals/{approval_id}/approve
POST /api/v1/approvals/{approval_id}/reject
```

The body is empty (or an explicitly empty JSON object). The route obtains the
trusted local context server-side and calls existing
`approve_approval`/`reject_approval` with fixed trusted reasons. It only
persists the human decision. It does not claim, dispatch, reconcile, or retry.
Concurrent decisions are resolved by existing approval CAS; the losing request
refetches/reports the authoritative current state. A dropped response is
unknown to the browser and causes refetch, never blind replay.

### 8.3 Execution continuation

Execution remains a trusted application/worker concern. A Phase 7E internal
composition may call existing `claim_action`, `dispatch_claimed_operation`,
`record_adapter_outcome`, and `reconcile_operation` with an injected adapter,
but no browser route owns execution. The existing Phase 6 operation state and
unknown-outcome rules remain authoritative.

## 9. Browser-safe projections

The HTTP layer adds explicit Pydantic models, not ORM serialization.

`TaskSummary` contains `task_id`, `task_kind`, derived `status`, `created_at`,
`updated_at`, and a bounded safe action summary. `TaskDetail` adds one
`ApprovalSummary` (when present), derived operation status/outcome, expiry, and
a closed safe failure category.

`ApprovalSummary` contains `approval_id`, related opaque `task_id`, safe action
summary, approval status, issued/expiry timestamps, derived operation state,
and whether approve/reject is currently available. `ApprovalDetail` adds only
the bounded human-decision reason and current authoritative timestamps.

The action summary is a closed projection for `CALENDAR_CREATE_EVENT`: action
kind, integration label, bounded event summary, canonical start/end/timezone,
and optional bounded description/location. It excludes target hashes,
provider IDs, credentials, raw canonical JSON, policies, tool names, adapter
internals, retry/idempotency secrets, SQL, paths, tracebacks, and raw events.

## 10. No-real-write acceptance architecture

Phase 7E acceptance uses temporary SQLite databases migrated through the normal
Alembic path and injects the existing deterministic `FakeExternalAdapter` (or
the already-tested fake Calendar client behind the existing adapter). It never
contacts Google, Moodle, LM Studio, or another provider and performs no real
external write.

The acceptance harness proves:

- idempotent task creation and action/approval binding;
- browser-safe projections and redaction;
- server-derived approver identity;
- approve/reject CAS under concurrency;
- trusted claim and durable `EXECUTING` dispatch ordering;
- confirmed success and authoritative failure;
- `UNKNOWN_OUTCOME` and refetch/reconciliation behavior;
- stale/expired/invalidated actions cannot execute; and
- duplicate clicks, retries, reconnects, and terminal mutations do not create
  duplicate tasks, approval consumption, operations, or adapter attempts.

No model or RAG calls are part of the Phase 7E acceptance harness. The
existing Phase 6 fake adapter is a test dependency, not a new production
provider implementation.

## 11. Ownership and non-goals

These prerequisites remain Phase 7E internal infrastructure because they add a
browser projection and local task ledger without changing Phase 6 provider
authority or outcome semantics. Phase 6C is not resurrected. The frozen Phase
6B Calendar capability remains the only external-write capability.

Explicit non-goals:

- no Phase 6 service/state-machine redesign;
- no new provider or real external write;
- no generic arbitrary task/tool/provider payload;
- no frontend-owned permission, approval, execution, or reconciliation;
- no SSE/activity/system-health work from 7F;
- no multi-user authentication implementation;
- no automatic retry, queue, worker fleet, or distributed job framework;
- no new model/RAG behavior; and
- no changes to Phase 4/5/6 frozen worktrees.

## 12. Security invariants

1. Capability permission and human approval remain separate predicates.
2. Browser input cannot create `TrustedApproverContext` or choose privileged
   authority.
3. Phase 6 `PreparedAction`, approval binding, claim, dispatch fence, outcome,
   and reconciliation remain authoritative.
4. One task request has one linked action/operation; retries require a new task.
5. `UNKNOWN_OUTCOME` is never relabeled failure or success.
6. No browser projection contains provider payloads, credentials, paths, SQL,
   tracebacks, raw events, or unbounded metadata.
7. Database uniqueness/CAS, not disabled buttons, prevents duplicate task or
   decision effects.
8. Approval endpoints cannot execute a provider call.
9. Acceptance performs zero real external writes.
10. Existing Phase 6B one-real-CREATE history remains immutable and no new real
    write is authorized.

## 13. Open questions and implementation gates

No architectural blocker remains for a later implementation plan. Before
implementation authorization, the plan must verify:

- the next Alembic revision ID is generated from the actual current head;
- local approver identity/context configuration is present in the deployment
  environment or mutation routes fail closed;
- the Next.js proxy forwards the configured mutation origin/host safely; and
- all acceptance uses injected fakes and a disposable database.

These are implementation gates, not permission to implement in this amendment.
