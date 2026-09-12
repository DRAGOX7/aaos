# Phase 6A — Durable Approval and Gated Execution Core

Status: **DESIGN ONLY — HUMAN-APPROVED REVISED PROPOSAL**

This document defines Phase 6A only. It is the contract for a later
implementation review; it does not authorize production code, migrations,
real external adapters, or an implementation plan. Phase 6B (Calendar), 6C
(optional GitHub read-only), Phase 7, and Phase 8 remain out of scope.

## 1. Objective

Phase 6A adds a durable, one-shot approval and execution core that can be
exercised entirely with fake external adapters. It makes an approved action
auditable, binds it to the exact payload and policy under which it was
approved, prevents duplicate or unauthorized dispatch, and represents
uncertain external outcomes honestly.

The intended flow is:

```text
trusted Phase 5 result / trusted application request
  -> immutable PreparedAction
  -> capability check and human approval
  -> atomic one-shot claim and consumption
  -> dispatch fence and remote-precondition revalidation
  -> least-authority ExecutableAction
  -> fake external adapter (6A)
  -> durable outcome or UNKNOWN_OUTCOME
  -> reconciliation, without automatic re-execution
```

The model, retrieved documents, tool observations, and arbitrary request
text are never approval authority. Capability permission and human approval
are separate predicates: both are required, and neither can be supplied by
the model.

## 2. Authoritative context and current-state findings

The frozen Phase 5 roadmap identifies Phase 6 as “Durable Approval Core and
Gated External Integrations.” Its first internal gate is a durable approval
and execution core tested with fake adapters; real Calendar and other
external writes are later gates. The roadmap requires an action record to
bind actor, target, action type, exact normalized arguments, tool/schema/
policy versions, expiry, remote precondition identity, approval identity,
atomic consumption, stable operation identity, and outcome. It names the
outcomes `CONFIRMED_SUCCESS`, `CONFIRMED_FAILURE`, and `UNKNOWN_OUTCOME`.

The Phase 5 architecture and implementation preserve the following
boundaries that Phase 6A consumes:

- one trusted primary orchestrator;
- model proposals are untrusted suggestions, not execution authority;
- static allowlisted tools and capability permission checks;
- `LocalRagService.ask(RagQuery) -> RenderedRagResponse` as the frozen Phase
  4 boundary;
- trusted task scope, runtime policy, validation, lifecycle, sanitization,
  lineage, and truthful event envelopes;
- no external-write tool, credential exposure, or approval engine in Phase 5.

The repository currently provides SQLAlchemy/Alembic persistence conventions,
UTC timestamp handling, and append-only in-memory Phase 5 events, but no
durable Phase 6 approval schema or external adapter. No dedicated Phase 6
security or acceptance document exists yet; the roadmap and the frozen
Phase 5 architecture/design are therefore the normative predecessors for
this specification.

Relevant references (all are in the frozen Phase 5 worktree unless noted):

- `docs/PHASES.md`, Phase 6 roadmap and acceptance boundaries;
- `docs/ARCHITECTURE.md`, trusted-agent and approval separation;
- `docs/PROJECT_SPEC.md`, typed boundaries and human approval before
  sensitive external writes;
- `docs/GOVERNANCE_LEDGER.md`, phase authorization history;
- `docs/superpowers/specs/2026-09-07-phase-5-controlled-agent-loop-design.md`,
  C1–C9 trust, lifecycle, event, and least-authority contracts;
- `src/aaos/persistence/database.py` and `academic_models.py`, persistence
  and migration conventions;
- `src/aaos/agent/contracts.py`, `registry.py`, `policy.py`, `events.py`,
  `phase4_adapter.py`, and `orchestrator.py`, the consumed Phase 5 typed
  interfaces.

## 3. Scope and non-goals

### In scope

- durable prepared-action, approval, claim, operation, lifecycle, and event
  records;
- exact canonical argument binding and version/hash binding;
- trusted approver context and human approval state transitions;
- capability revalidation at preparation, claim, and dispatch;
- database-enforced uniqueness and transactional one-shot consumption;
- least-authority execution projection and fake adapter protocol;
- explicit success, failure, and unknown-outcome semantics;
- reconciliation and crash/race behavior;
- append-only per-action audit events with atomic sequence allocation;
- deterministic acceptance tests and security invariants.

### Out of scope

Phase 6A does not implement Calendar, GitHub, Moodle, Gmail, MCP,
browser, HTTP, or any real external write; credentials; remote API clients;
the Phase 6 approval UI; a durable worker fleet; an approval delegation or
escalation engine; Phase 7 dashboards; Phase 8 REPL/VLM functionality; a
general distributed job framework; or any Phase 4/5 redesign. Fake adapters
must not be replaceable by network adapters through model-controlled data.

There is no automatic execution retry in 6A. Durable idempotency,
reconciliation, and approval consumption are included only to make a single
attempt safe and auditable; richer recovery workflows are later-phase work.

## 4. Trust boundaries and authority

### Untrusted inputs

Model proposals, model terminal text, retrieved Moodle/course text, RAG
evidence, tool observations, arbitrary request text, and serialized payloads
from an untrusted caller are data. They may request preparation but cannot
approve, select a privileged capability, broaden scope, alter policy, set
identity, or mark an action successful.

### Trusted owners

The trusted application owns actor and target identity, action type,
canonical permitted arguments, tool/schema/policy versions, remote
precondition data, action/operation IDs, approval transitions, capability
grants, state transitions, dispatch, adapter registration, and event
allocation. An authenticated approver context supplies approver identity.
Phase 6A may use a deterministic fake trusted approver in tests; it is not a
model or request field.

The database transaction is the authority for one-shot approval consumption.
Application checks are necessary but are not sufficient without the database
constraints and expected-state predicates specified below.

## 5. Record boundaries

The durable aggregate may be exposed internally as an `ActionRecord` read
model, but the adapter boundary never receives it. `ActionRecord` is a
trusted application/repository view composed from the records below; it is
not a model-writable command and must not be serialized to an adapter.

### 5.1 `PreparedAction` — immutable approval-bound data

`PreparedAction` is created once by trusted code and is immutable thereafter.
It contains:

- `action_id`: opaque trusted UUID/ULID-like identifier;
- `operation_id`: trusted stable identity for this one possible execution;
- `actor_identity`: typed authenticated initiator identity, without tokens;
- `target_identity`: typed provider/account/resource identity, without
  credentials or arbitrary paths;
- `action_type`: static allowlisted domain action;
- `canonical_arguments`: exact compact UTF-8 JSON representation of the
  permitted arguments;
- `arguments_sha256`: hash of those exact canonical bytes;
- `tool_name`, `tool_schema_version`, and `tool_definition_fingerprint`;
- `policy_version` and `policy_fingerprint` in force at preparation;
- `remote_precondition`: provider/resource/version/etag/hash or equivalent
  expected-state identity;
- `prepared_at`;
- `action_expires_at`: an immutable trusted deadline for the prepared
  action; and
- `prepared_action_sha256`: the digest of every approval-bound immutable
  field listed in section 6.

It contains no mutable approval status, lifecycle state, adapter receipt, or
execution result, and it has no catch-all metadata map. The model cannot
supply or change any of these fields. An action at or past
`action_expires_at` cannot be approved, claimed, or dispatched.

The repository exposes no update/delete operation for a prepared payload.
The SQLite schema uses foreign keys and DB triggers rejecting `UPDATE` or
`DELETE` of immutable columns/rows. Direct-SQL mutation tests are required.
A correction requires a new action with a new operation identity and
approval.

### 5.2 `ActionLifecycle` — mutable action state

One row keyed by `action_id` stores current action state, revision, approval
reference, operation reference, and lifecycle timestamps. It never copies
canonical arguments or version hashes. Every update uses an expected-state
and expected-revision compare-and-swap (CAS).

Action states are defined in section 7. Action-level `REJECTED` means a
trusted human approver explicitly rejected the approval. Trusted system
invalidation due to policy, schema, version, or stored-precondition binding
drift is always `INVALIDATED`, never `REJECTED`.

### 5.3 `ApprovalRecord` — human decision bound to immutable data

Immutable-at-issue fields:

- `approval_id`;
- `action_id`;
- `operation_id`;
- approved payload hash and tool/schema/policy fingerprints;
- `approval_binding_sha256`, which includes the prepared action's
  `prepared_action_sha256` plus the bound approver identity/context and
  `approval_expires_at`;
- approver identity from `TrustedApproverContext`;
- issued timestamp and `approval_expires_at`.

`approval_expires_at` must be no later than `action_expires_at` and is checked
against both deadlines at request, approval, claim, and dispatch.

Mutable fields are only status and status timestamps/reason codes. Approval
status transitions are exactly:

```text
PENDING  -> APPROVED | REJECTED | REVOKED | EXPIRED
APPROVED -> CONSUMED | REVOKED | EXPIRED
```

`CONSUMED`, `REJECTED`, `REVOKED`, and `EXPIRED` are terminal approval
states. The model, retrieval text, and arbitrary request content cannot
create an approver context or move this state.

If an action or its pending approval reaches a deadline before consumption,
the trusted expiry path updates `ApprovalRecord` to `EXPIRED` and the action
to `EXPIRED` in one transaction. An already-consumed approval is not rewritten
by a later action deadline; the claimed operation follows the post-consumption
expiry rule in section 7.3.

### 5.4 `ApprovalConsumption` — authoritative one-shot fact

This table is the authoritative fact that an approval was consumed. It has a
unique/primary `approval_id`, unique `operation_id`, `action_id`, trusted
claim identity, and `consumed_at`. In the same database transaction that
inserts this row, `ApprovalRecord.status` changes from `APPROVED` to
`CONSUMED`, the lifecycle changes to `CLAIMED`, and the operation row and
claim event are written. A duplicate insert fails atomically. No later code
may infer consumption solely from an application cache or a status read.

### 5.5 `OperationRecord` — one execution attempt

`OperationRecord` binds one `operation_id` to one action and records adapter
kind/version, lifecycle, dispatch/reconciliation timestamps, sanitized
receipt/outcome evidence, and a revision. Phase 6A deliberately has one
execution attempt per operation and therefore has no `attempt_number` field.
An operation that reaches unknown outcome is reconciled but never executed
again. A future execution requires a newly prepared action, approval,
operation ID, and consumption.

### 5.6 `ActionEvent` — append-only audit history

Events contain an event ID, action ID, operation ID when applicable, an
atomically allocated per-action sequence, event type, safe actor/source,
timestamp, and an event-kind-specific typed details value. The details value
is a closed tagged union (for example `ActionPreparedDetails`,
`ApprovalRequestedDetails`, `ApprovalDecisionDetails`, `ClaimedDetails`,
`DispatchStartedDetails`, `OutcomeRecordedDetails`,
`ReconciliationDetails`, `InvalidatedDetails`, and
`LateResultIgnoredDetails`) containing only bounded IDs, enums, hashes,
timestamps, and safe reason codes. It is never an arbitrary dictionary or
JSON passthrough. Raw prompts, document text, credentials, stack traces,
arbitrary exception objects, and full adapter payloads are excluded.

The repository/API exposes append and read only; no update or delete method
exists for prior events. The SQLite migration adds database triggers
rejecting direct `UPDATE` and `DELETE` of event rows, using the same
SQLite/Alembic conventions as the rest of the repository. Event persistence
is in the same transaction as the state transition it describes.

## 6. Canonical payloads, identity, and versions

Canonical arguments are strict JSON-compatible values. Objects use sorted
keys and compact UTF-8 encoding; arrays preserve order. NaN, infinity,
callables, file handles, exception objects, credentials, arbitrary Python
objects, and non-JSON values are rejected. Strings are not broadly
normalized or rewritten: only a tool schema may define a domain-specific
normalization, and the normalized result is then the exact approved value.
The bytes and SHA-256 hash are retained so approval and dispatch compare the
same payload, not a semantically similar reconstruction.

`ActorIdentity` and `TargetIdentity` are typed, bounded values generated or
resolved by trusted code. They cannot contain secrets, arbitrary filesystem
authority, or permission grants. `action_id` and `operation_id` are generated
by trusted code and have no capability meaning. Tool/schema/policy versions,
fingerprints, the approved payload hash, and both expiry values are checked
again at claim and dispatch; a mismatch invalidates the action and prevents
the write.

`prepared_action_sha256` is computed once over a canonical, versioned
serialization containing, in a fixed field order: `action_id`,
`operation_id`, actor identity, target identity, action type,
`canonical_arguments` bytes and `arguments_sha256`, tool name, tool schema
version and definition fingerprint, policy version and fingerprint, the
complete remote precondition identity, and `action_expires_at`. The
serialization is strict JSON/UTF-8 with sorted object keys, compact encoding,
and no omitted or extra fields. At approval issuance,
`approval_binding_sha256` is computed over a second fixed serialization of
that prepared-action digest, the bound approver identity/context, and
`approval_expires_at`; it is not recomputed from a subset of fields. Any
mismatch in either digest or in its constituent versioned fields fails
closed.

## 7. State machines

### 7.1 Action state machine

```text
PREPARED -> AWAITING_APPROVAL    (trusted request_approval)
PREPARED -> EXPIRED              (action deadline reached; no approval exists)

AWAITING_APPROVAL -> APPROVED       (human approval)
AWAITING_APPROVAL -> REJECTED       (human rejection only)
AWAITING_APPROVAL -> EXPIRED        (action/approval deadline; approval EXPIRED in the same transaction)
AWAITING_APPROVAL -> REVOKED        (trusted/human revocation)

APPROVED -> CLAIMED                 (atomic claim/consumption)
APPROVED -> INVALIDATED             (policy/schema/version/stored-binding drift)
APPROVED -> EXPIRED                 (approval EXPIRED in the same transaction)
APPROVED -> REVOKED                 (approval REVOKED in the same transaction)

CLAIMED -> EXECUTING                (dispatch fence passes)
CLAIMED -> INVALIDATED              (dispatch fence fails before dispatch start)
CLAIMED -> EXPIRED                  (an applicable deadline passes before dispatch start)

EXECUTING -> CONFIRMED_SUCCESS
EXECUTING -> CONFIRMED_FAILURE
EXECUTING -> UNKNOWN_OUTCOME
EXECUTING -> INVALIDATED           (execute-time precondition rejected before side effect)

UNKNOWN_OUTCOME -> RECONCILIATION_PENDING
RECONCILIATION_PENDING -> CONFIRMED_SUCCESS | CONFIRMED_FAILURE | UNKNOWN_OUTCOME
```

Terminal action states are `REJECTED`, `INVALIDATED`, `EXPIRED`, `REVOKED`,
`CONFIRMED_SUCCESS`, and `CONFIRMED_FAILURE`.
`UNKNOWN_OUTCOME` is terminal for the one execution attempt and non-retryable;
it may enter reconciliation. If reconciliation cannot prove an outcome,
the action remains `UNKNOWN_OUTCOME` and cannot execute again. There is no
transition from any terminal state, including a reconciled unknown, back to
execution.

### 7.2 Approval state machine

The approval transitions in section 5.3 are the complete set. In particular,
a dispatch fence must not require `ApprovalRecord.status = APPROVED` after a
successful claim: the authoritative consumption row and the same-transaction
`CONSUMED` status are expected at dispatch.

### 7.3 Operation lifecycle

```text
CLAIMED -> EXECUTING -> CONFIRMED_SUCCESS | CONFIRMED_FAILURE | UNKNOWN_OUTCOME
UNKNOWN_OUTCOME -> RECONCILIATION_PENDING
RECONCILIATION_PENDING -> CONFIRMED_SUCCESS | CONFIRMED_FAILURE | UNKNOWN_OUTCOME
CLAIMED -> NOT_DISPATCHED       (dispatch fence invalidation before start)
EXECUTING -> NOT_DISPATCHED      (execute entered only to reject stale precondition)
```

`NOT_DISPATCHED` is a terminal operation state with no external attempt and
no external side effect dispatched or committed and no fabricated
success/failure outcome. `execute()` may be entered solely to enforce the
atomic precondition and still result in `NOT_DISPATCHED`; this is paired with
action `INVALIDATED` when the remote precondition is stale or action
`EXPIRED` when an immutable action or approval deadline passes after
consumption but before dispatch. In the latter case the already-consumed
approval remains `CONSUMED`; it cannot be rewritten to `EXPIRED`.
`NOT_DISPATCHED` is not a
`CONFIRMED_FAILURE`, because no execution that passed the precondition fence
has been shown to fail.

Invalid state inputs, backwards transitions, and terminal mutations fail
with a stable typed error. The trusted repository/service owns all
transitions; the model can request preparation only.

## 8. Preparation, approval, claim, and dispatch protocol

1. **Prepare.** Trusted code validates the allowlisted action schema,
   canonicalizes the exact arguments, resolves actor/target identity, checks
   current capability permission, captures versions/fingerprints and the
   remote precondition, chooses an immutable `action_expires_at`, generates
   both IDs and `prepared_action_sha256`, and persists `PreparedAction` plus
   `ActionLifecycle(PREPARED)`. A failed capability check creates no
   executable action.
2. **Request approval.** Trusted code calls
   `request_approval(action_id, TrustedApproverContext, approval_expires_at)`.
   It verifies the action exists, is still `PREPARED`, has not reached
   `action_expires_at`, and that the requested approval deadline is positive
   and no later than the action deadline. In one transaction it creates an
   `ApprovalRecord(PENDING)` bound to the action, operation, full
   `approval_binding_sha256`, and `TrustedApproverContext.approver_identity`,
   then CASes `PREPARED -> AWAITING_APPROVAL`. No model or request field can
   create this context.
3. **Approve or reject.** A trusted authenticated approver context whose
   stable identity exactly matches the bound approver identity may approve or
   reject the pending record. Approval rechecks both deadlines and the full
   action binding. A mismatch is denied; it cannot be treated as delegation.
   Human rejection yields action `REJECTED`; trusted drift is never reported
   as rejection.
4. **Atomic claim.** The claim transaction is database-only: it performs no
   adapter I/O, network I/O, or live remote-state/precondition read. In one
   write transaction, CAS the lifecycle from
   `APPROVED` to `CLAIMED` and verify: action/approval/operation IDs match;
   approval is `APPROVED`, unexpired, and not revoked; `now` is before both
   deadlines; the full approved binding digest and all versions still match;
   current capability permission and policy still grant the action; the
   operation does not already exist or have a dispatch; and the stored
   `RemotePrecondition` is present, structurally valid, exactly equal to the
   immutable approval-bound `PreparedAction` value, and has not been mutated
   or replaced. Insert the unique
   `ApprovalConsumption`, change approval status to `CONSUMED` in that same
   transaction, create the operation, allocate/append the claim event, and
   commit. A failed predicate is handled coherently in that transaction:
   expiry sets approval `EXPIRED` and action `EXPIRED`; revocation sets
   approval `REVOKED` and action `REVOKED`; policy/schema/action-binding or
   stored-precondition binding drift sets action `INVALIDATED` and the
   still-unconsumed approval `REVOKED` with a fixed trusted `reason_code`.
   Live remote
   precondition validation is deferred to `execute()` at the side-effect
   boundary. It never leaves an
   `APPROVED` approval attached to a terminal invalidated action, consumes an
   approval on failure, or calls an adapter.
5. **Durable dispatch start and fence.** A worker runs the dispatch fence in
   a write transaction for a durable `CLAIMED` operation. It verifies that
   the consumed row belongs to the current action and operation, lifecycle
   and operation are exactly the expected `CLAIMED` state, current capability
   permission remains valid, the approved payload/binding/schema/policy
   versions and both action/approval deadlines remain valid, and no dispatch
   has previously started. The fence expects approval status `CONSUMED`, not
   `APPROVED`. The winning worker alone CASes action/operation
   `CLAIMED -> EXECUTING`, writes
   `dispatch_started_at` and a typed `DISPATCH_STARTED` event, and commits
   before invoking any adapter. A fence failure invalidates the action and
   records operation `NOT_DISPATCHED` without an adapter call; an expired
   action or approval is recorded as action `EXPIRED` rather than
   `INVALIDATED`, while consumed approval remains `CONSUMED`.
6. **Projection and execution.** After the durable dispatch-start commit,
   build an `ExecutableAction` containing only target identity, allowlisted
   action type, canonical permitted arguments, operation identity, and the
   required remote precondition. The adapter receives no `ActionRecord`,
   `PreparedAction`, approval, permission grant, policy object, model text,
   or credentials. Its `execute()` method enforces the required precondition
   at the actual side-effect boundary. Recovery of durable `CLAIMED` may run
   the fence; recovery of durable `EXECUTING` must never execute again and,
   when no result exists, conservatively records `UNKNOWN_OUTCOME` for
   reconciliation.
7. **Persist outcome.** Adapter evidence is mapped to the operation/action
   lifecycle using expected-state CAS. State and event commit together. A
   late result whose expected revision/state no longer matches updates zero
   rows and becomes a typed, sanitized `LATE_RESULT_IGNORED` event.
8. **Reconcile unknown.** `UNKNOWN_OUTCOME` may be queried through the
   adapter's reconciliation method. Reconciliation can prove success or
   failure, or leave unknown. It never invokes `execute()` and never creates
   a second attempt. A new execution requires a new action and approval.

## 9. Preconditions and least-authority execution

`RemotePrecondition` is required data on `ExecutableAction`; it is not an
optional argument supplied by a model. A standalone preliminary
`check_precondition()` is only an advisory optimization and is never a
correctness or security fence. The actual adapter `execute()` must perform a
provider-conditional or equivalent atomic check immediately before the
side-effect operation and must return a non-success result without writing
when the precondition is stale.

The fake adapter test suite includes this sequence:

```text
preliminary check passes
remote fake state changes
durable CLAIMED -> EXECUTING dispatch-start commits
execute(ExecutableAction) revalidates
zero fake side effects are recorded
action is INVALIDATED / operation is NOT_DISPATCHED
```

The projection is intentionally smaller than `ActionRecord`:

```text
ExecutableAction {
  target_identity
  action_type
  canonical_permitted_arguments
  operation_id
  required_remote_precondition
}
```

No target string becomes a filesystem path, no argument becomes a shell
command, and no adapter can infer additional authority from omitted fields.

## 10. Outcomes, crashes, and retry policy

- `CONFIRMED_SUCCESS`: authoritative adapter or reconciliation evidence
  proves the external side effect occurred for this operation identity.
- `CONFIRMED_FAILURE`: an execution that passed the precondition and dispatch
  fences has authoritative evidence that its invocation failed and cannot
  later succeed from that invocation. A stale precondition is not this
  outcome; it is action `INVALIDATED` plus operation `NOT_DISPATCHED`.
- `UNKNOWN_OUTCOME`: AAOS cannot prove whether the external side effect
  occurred. Transport failure, timeout, process crash, lost response, or a
  crash after the adapter accepted the operation but before AAOS persisted
  its outcome are unknown unless independent evidence proves otherwise.

`NOT_DISPATCHED` means that no external side effect was dispatched or
committed. It may be recorded even when `execute()` was entered solely to
perform the atomic remote-precondition check and it rejected the stale
condition before the side-effect operation. It is not a fabricated failure.

AAOS must not translate a timeout into confirmed failure, success, or
termination without evidence. Unknown is a safe durable stop condition.
There are no automatic execution retries in Phase 6A: one approval produces
one consumed claim, one operation identity, and one execution attempt.
Reconciliation is read/observe only. Any later execution is a new trusted
preparation and approval, never a blind retry of an unknown operation.

The adapter protocol is conceptually:

```python
execute(ExecutableAction) -> AdapterExecutionOutcome
reconcile(operation_id) -> AdapterReconciliationOutcome
```

Both outcomes are typed, bounded, and sanitized. A fake adapter may model
success, authoritative rejection, timeout, crash, accepted-but-unpersisted
operation, stale precondition, and late result; it must never perform a real
network or external write.

## 11. Database correctness and event sequencing

The 6A migration must use the repository's SQLAlchemy/Alembic conventions
and enable foreign-key enforcement. Correctness-critical constraints include:

- primary/unique `action_id` and `operation_id`;
- one-to-one action/operation foreign keys;
- primary/unique `approval_id`;
- unique `(approval_id)` and `(operation_id)` in `approval_consumptions`;
- immutable approval/action hash/version binding checks;
- unique event ID and `(action_id, sequence)`;
- non-negative monotonic revisions/sequences and valid status checks;
- foreign keys from lifecycle, approvals, consumption, operations, and
  events to their owning action.

The claim transaction is serialized by the database write transaction and
uses predicates on state and revision. Two workers/connections racing to
claim can produce exactly one consumption row and one dispatchable operation;
the loser receives a typed already-claimed/invalid-state result.

Per-action event sequences are never allocated with non-transactional
`MAX(sequence)+1`. A dedicated `action_event_counters` row keyed by
`action_id` is created on the first event. In the same write transaction as
the state change, the repository performs an insert-if-absent followed by an
atomic increment-and-return of the prior sequence (for example, SQLite
`UPDATE ... SET next_sequence = next_sequence + 1 ... RETURNING next_sequence - 1`).
The returned value is inserted into `ActionEvent` under the unique
`(action_id, sequence)` constraint before commit. Concurrent writers are
serialized by the database; a failed event/state transaction rolls back both
the counter increment and state change.

## 12. Reconciliation and race/crash semantics

The repository records expected state and revision for every mutable update.
Reconciliation wins only through CAS. A late adapter response can never
overwrite `CONFIRMED_SUCCESS`, `CONFIRMED_FAILURE`, `INVALIDATED`, or a
reconciled `UNKNOWN_OUTCOME`; its zero-row CAS is retained as a sanitized
audit event. If reconciliation and a late result race, exactly one valid
transition commits and the other is ignored.

Required crash cases:

- crash before claim commit: no consumption and no dispatch;
- crash after consumption/claim commit but before dispatch: recovery sees a
  claimed operation; the database-only dispatch fence and stored-binding
  checks decide whether it is invalidated or dispatched once. Live remote
  precondition validation remains exclusively inside `execute()`;
- crash after the durable `CLAIMED -> EXECUTING` dispatch-start commit but
  before the adapter call: recovery must not execute again; with no persisted
  result it records `UNKNOWN_OUTCOME` and schedules reconciliation;
- adapter accepts an operation, then AAOS crashes before outcome persistence:
  operation is `UNKNOWN_OUTCOME`, not failure; reconciliation is required;
- crash after a confirmed outcome commit: replay reads the terminal state and
  cannot execute again;
- late success/failure after reconciliation: CAS rejects mutation and emits
  `LATE_RESULT_IGNORED`.

Resource cleanup and transport timeout are separate facts. Phase 6A records
the adapter/lifecycle owner’s cleanup evidence; returning from a waiter is
not proof that a remote operation terminated. No unsafe thread termination,
process killing, hidden retry, or detached unowned work is introduced.

## 13. Approver context

`TrustedApproverContext` is a narrow boundary supplied by authenticated
application code. It contains a bounded approver identity and authentication
context identifier suitable for audit, but no credential material. It is
constructed outside model/retrieval/request data. Human approve, reject, and
revoke commands require a trusted context matching the bound approver. The
deterministic fake implementation used by 6A tests returns a fixed trusted
identity and cannot be instantiated from a model proposal. An approval API
that accepts `approver_id` from an untrusted JSON body is invalid. The bound
approver is the exact pair of stable `approver_identity` and authenticated
`context_id`; later human decisions must present a trusted context matching
that pair. Phase 6A does not support model-selected delegation or approver
substitution.

Trusted-system invalidation is different: when policy, schema, binding,
precondition, or expiry rules invalidate an unconsumed approval, the system
may move that approval to `REVOKED` or `EXPIRED` in the same transaction with
the action transition, using a fixed trusted `reason_code`. This internal
transition does not require, invent, or impersonate a human approver and is
never represented as a human decision event.

## 14. Failure/error contract

All public failures are safe typed codes with fixed safe context fields
(bounded IDs, enums, and reason codes). At minimum:

`INVALID_PREPARED_ACTION`, `CAPABILITY_DENIED`, `APPROVAL_NOT_FOUND`,
`APPROVAL_EXPIRED`, `APPROVAL_REVOKED`, `APPROVAL_ALREADY_CONSUMED`,
`HUMAN_REJECTED`, `ACTION_INVALIDATED`, `INVALID_STATE_TRANSITION`,
`CLAIM_CONFLICT`, `VERSION_MISMATCH`, `PRECONDITION_STALE`,
`DISPATCH_ALREADY_RECORDED`, `UNKNOWN_OUTCOME`, `RECONCILIATION_REQUIRED`,
`ADAPTER_FAILURE`, and `PERSISTENCE_CONFLICT`.

Raw SQL, paths, credentials, stack traces, exception objects, provider
headers, and internal module names are not exposed to users or model-visible
observations. A handler/provider failure is not automatically a confirmed
failure unless its contract supplies authoritative no-side-effect evidence.

## 15. Planned module boundaries (implementation not included here)

These are proposed boundaries for the later implementation plan; this
specification creates none of them:

```text
src/aaos/approval/contracts.py       # typed identities, records, states, outcomes
src/aaos/approval/state.py           # transition/CAS domain rules
src/aaos/approval/repository.py      # transactional persistence and event counter
src/aaos/approval/adapters.py        # least-authority adapter protocol/fakes
src/aaos/approval/service.py         # prepare, approve, claim, dispatch, reconcile
src/aaos/approval/events.py          # safe event types/projections
src/aaos/persistence/approval_models.py
migrations/versions/0004_phase6a_approval_core.py
tests/test_phase6a_*.py
```

The future implementation must reuse existing persistence conventions rather
than create a parallel database or bypass the Phase 5 registry/policy and
Phase 4 typed boundary. No module above is created by this design task.

## 16. Acceptance invariants and tests

The Phase 6A implementation is not acceptable until deterministic tests
prove all of the following:

### Approval, authority, and payload

1. No action is executable without current capability permission and a
   trusted human approval.
2. Model, retrieval, request, and observation text cannot approve, revoke,
   broaden scope, change policy, or supply approver identity.
3. Prepared payload, actor, target, action type, operation ID, versions,
   precondition, action expiry, and hashes are immutable; mutation attempts
   fail. An expired prepared action cannot be approved, claimed, or
   dispatched.
4. Human rejection yields action `REJECTED`; trusted drift yields
   `INVALIDATED`, never a mislabeled rejection.
5. Approval status transitions exactly match the closed graph, and terminal
   approvals cannot transition again.
6. `request_approval` creates a `PENDING` approval and atomically transitions
   `PREPARED -> AWAITING_APPROVAL`; its trusted approver identity is required
   to match the identity later used to decide the approval.
7. Capability revoked after approval but before claim invalidates/denies the
   claim and invokes no adapter; the approval is revoked in the same
   transaction.
8. Approval revoked after approval but before claim cannot be consumed or
   dispatched; approval and action become `REVOKED` coherently.

### Claim, dispatch, and concurrency

9. The database consumption row and `ApprovalRecord=CONSUMED` commit in the
   same transaction.
10. Two workers/connections racing to claim yield one successful claim,
   one operation identity, one consumption row, and no duplicate dispatch.
11. Two workers racing to dispatch a claimed operation produce one durable
    `CLAIMED -> EXECUTING` dispatch-start event and one adapter invocation;
    only the CAS winner may call `execute()`.
12. The dispatch fence accepts consumed approval only when action/operation,
    lifecycle, capability, hashes/versions, deadlines, and dispatch status
    all match.
13. A stale remote precondition after approval (and a remote state change
    between preliminary check and `execute`) produces zero side effect.
14. The fake adapter receives only the `ExecutableAction` projection and
    cannot access `ActionRecord`, approval, permissions, policy, or secrets.
15. Per-action event sequences remain unique and ordered under concurrent
    transitions; no `MAX(sequence)+1` race exists.

### Outcomes, crash, and reconciliation

16. Adapter success persists `CONFIRMED_SUCCESS` exactly once.
17. An execution that passed both precondition and dispatch fences and has
    authoritative invocation-failure evidence persists
    `CONFIRMED_FAILURE`; stale precondition persists action `INVALIDATED`
    and operation `NOT_DISPATCHED` instead.
18. Timeout, transport failure, crash, or accepted-but-unpersisted operation
    persists `UNKNOWN_OUTCOME`, not confirmed failure.
19. A reconciliation race with a late execution result has one CAS winner;
    the stale result becomes a sanitized event and cannot overwrite state.
20. Durable `EXECUTING` recovery never calls `execute()` again; with no result
    it enters `UNKNOWN_OUTCOME`/reconciliation.
21. `UNKNOWN_OUTCOME` causes zero automatic execution retries and no second
    model proposal; reconciliation never invokes `execute()`.
22. A terminal action cannot be mutated by late success/failure, and a
    `NOT_DISPATCHED` invalidation cannot be dispatched later.
23. Resource cleanup is recorded only when the actual lifecycle owner proves
    it; timeout is not reported as termination or cleanup completion.

### Isolation and scope

24. Fake adapters are the only adapters in 6A; no adapter, model, tool
    proposal, or `ExecutableAction` exposes arbitrary SQL or filesystem
    authority, shell, credentials, network, Calendar, GitHub, Moodle, or MCP
    writes. Trusted internal SQLite/SQLAlchemy/Alembic persistence for the
    Phase 6A ledger is permitted and remains application-owned.
25. Importing Phase 6A modules performs no model load, network access,
    subprocess, database mutation, or filesystem mutation.
26. Existing Phase 4/5 tests, C1–C9 authority boundaries, lineage,
    sanitization, lifecycle, runtime policy, and events remain green.

## 17. Evaluation ownership and evidence retention

Phase 6A evaluation is a deterministic trusted-core evaluation. It makes
zero model calls and zero RAG calls, and it has no model-routing accuracy
metric. English and Arabic strings may be retained only as adversarial
untrusted-data fixtures proving that language text, retrieved text, or
observations cannot grant authority; they are not language-model quality
cases. Calibration and heldout acceptance measure approval, capability,
schema, claim/consumption, lifecycle, reconciliation, event, and security
correctness gates owned by trusted code.

The implementation plan must freeze a separate Phase 6A calibration set and
untouched final heldout set before evaluation. Cases must cover approval and
capability separation, stale approval/policy/version/precondition, duplicate
claims, adapter projection, success/failure/unknown, crash windows,
reconciliation races, late results, event sequencing, injection attempts,
English and Arabic adversarial strings, and import/side-effect safety.
Heldout membership and labels are never changed in response to calibration
results.

The first heldout execution must retain two local, fingerprinted artifacts:

1. a sanitized report suitable for human review; and
2. a private frozen evaluation bundle containing only the exact evidence
   needed for predeclared post-hoc metrics, with no credentials or raw
   secrets, never committed and never exposed to the model.

The acceptance report must separate:

- capability/approval quality;
- trusted policy and schema enforcement;
- one-shot claim and duplicate prevention;
- lifecycle/outcome/reconciliation correctness;
- security hard gates (zero unauthorized or external writes);
- runtime/import reliability; and
- supplementary latency/diagnostic metrics.

No blended score may average away a security or correctness violation. The
report must include `MODEL_CALL_COUNT=0`, `RAG_EXECUTION_COUNT=0`, and no
model-routing accuracy field for the Phase 6A trusted-core domain.

## 18. Phase boundaries and governance

Phase 6A does not grant a real external capability. A later adapter must be
approved as a separate phase gate and must consume only the
`ExecutableAction` projection. Phase 6B may add a Calendar adapter and
provider-specific idempotency/reconciliation details; it must not weaken the
6A approval, capability, precondition, or unknown-outcome contracts. Phase 7
may consume safe events for a dashboard. Phase 8 may add restricted code or
VLM functionality. None of those changes are implied by this document.

The narrowest implementation sequence is: implement the records and closed
state rules; add migration/constraints and transactional repository; add
fake adapter and projection; add service/CAS/reconciliation behavior; then
run the predeclared acceptance suite. A separate implementation plan and
human approval are required before any code or migration is written.

## 19. Design decisions fixed by final review

The following corrections are binding:

1. `check_precondition()` is never the side-effect security fence;
   `ExecutableAction` carries the required precondition and `execute()`
   revalidates it at the side-effect boundary.
2. Action `REJECTED` is reserved for human rejection; trusted drift uses
   `INVALIDATED`.
3. The post-claim dispatch fence expects consumed approval and validates all
   action/operation/lifecycle/capability/version/dispatch predicates.
4. The consumption row is authoritative and is committed with approval
   status `CONSUMED`.
5. Approver identity is from trusted authenticated context only.
6. Event sequence allocation uses a transactional counter row, never
   non-transactional `MAX(sequence)+1`.
7. `OperationRecord` has one execution attempt and no `attempt_number`.
8. Unknown outcome is uncertainty about whether a side effect occurred; it
   is not a disguised failure and is never automatically retried.
9. `action_expires_at` is immutable and part of the full approval binding;
   approval expiry cannot exceed it, and expiry is enforced at request,
   claim, and dispatch.
10. `request_approval` is the explicit trusted issuance step, creating
    `PENDING` and atomically moving `PREPARED -> AWAITING_APPROVAL`; only a
    matching trusted approver identity may decide it.
11. Dispatch start is durable: the winning CAS commits `CLAIMED -> EXECUTING`
    and its event before `execute()`; durable `EXECUTING` recovery never
    executes again and conservatively reconciles unknown state.
12. `prepared_action_sha256`/`approval_binding_sha256` cover the complete
    immutable action binding, not merely argument bytes.
13. Stale preconditions produce action `INVALIDATED` and operation
    `NOT_DISPATCHED`; `CONFIRMED_FAILURE` is reserved for a post-fence
    invocation with authoritative non-success evidence.
14. Phase 6A has no action `CANCELLED` state and no arbitrary event-details
    map; event details are typed, event-kind-specific bounded structures.
15. Pre-consumption expiry, revocation, and trusted drift update approval and
    action states coherently in one transaction, never leaving an
    `APPROVED` approval attached to an invalidated terminal action.
16. Phase 6A evaluation has zero model/RAG calls and no model-routing metric;
    language strings are adversarial untrusted-data fixtures only.

No implementation, migration, real adapter, benchmark, heldout run, or
Phase 6B functionality is authorized by this specification alone.
