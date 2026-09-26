# Phase 7E Tasks and Approval-Controlled Actions Implementation Plan

> **For agentic workers:** execute this plan task-by-task. Each implementation
> task uses RED -> minimal GREEN -> focused verification -> review -> commit.
> Do not skip ahead, combine task commits, or begin Phase 7F.

**Goal:** expose one closed, browser-safe task type and approval workflow while
preserving the frozen Phase 6A/6B authority, one-operation semantics, and
`UNKNOWN_OUTCOME` handling.

**Architecture:** add a small additive `task_records` ledger linked by one
nullable unique foreign key to `prepared_actions`; derive post-preparation task
status from existing Phase 6 lifecycle/approval/operation rows; derive all
approver identity from a trusted server-side local principal provider; compose
existing Phase 6 services without changing them; use only injected fake
adapters in acceptance.

**Spec:** `docs/superpowers/specs/2026-09-18-phase-7e-prerequisite-architecture-amendment.md`.

**Starting checkpoint:** `2de4a133257f6b5f613422dd90be5c70a81828ce` on
`phase-07-dashboard`, clean. Phase 7D is human-accepted at
`ebae43800f6600ef61c07c355c9ad085b1f5f483`; Phase 7E implementation requires
separate human authorization after this plan.

## Global constraints

- Work only in `C:\Users\aljaf\.codex\worktrees\aaos-phase-07`.
- Read the approved amendment before every implementation task.
- Preserve frozen Phase 4/5/6 contracts and the Phase 6B tag
  `phase-06b-passed`.
- Do not modify Phase 6 service/state/adapter semantics. Use its public
  services and `ExecutableAction` boundary only.
- The only browser-initiable task kind is the closed
  `CALENDAR_CREATE_EVENT` request based on existing
  `CalendarCreateRequest`/canonicalization. No generic provider payload exists.
- Browser task creation prepares and requests approval; it never claims,
  dispatches, reconciles, or retries an operation.
- `approve` and `reject` routes only call existing Phase 6 decision functions.
- No automatic retry. A new execution requires a new task/action/approval.
- No model calls, RAG calls, real Google/Moodle/provider writes, credentials,
  Phase 7F work, or normal production-database writes in the acceptance
  harness.
- Use a disposable SQLite database migrated through Alembic. Never use
  `create_all()` or mutate `C:\Users\aljaf\aaos\data`.
- Existing tests are not deleted, skipped, weakened, or xfailed.
- Stage only the files listed by the current task. If an unexpected file or
  migration head appears, stop and report it before editing.

## Frozen cross-task interfaces

The following names and signatures are the contract between tasks. Later tasks
must consume these interfaces rather than create parallel substitutes.

### Trusted task domain (`src/aaos/tasks/contracts.py`)

```python
class TaskKind(StrEnum):
    CALENDAR_CREATE_EVENT = "CALENDAR_CREATE_EVENT"


class TaskCreationState(StrEnum):
    CREATED = "CREATED"
    ACTION_BOUND = "ACTION_BOUND"
    FAILED = "FAILED"


class TaskFailureCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    CAPABILITY_DENIED = "CAPABILITY_DENIED"
    PREPARATION_FAILED = "PREPARATION_FAILED"


class BrowserTaskState(StrEnum):
    PREPARING = "PREPARING"
    PREPARATION_FAILED = "PREPARATION_FAILED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    CLAIMED = "CLAIMED"
    EXECUTING = "EXECUTING"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"
    CONFIRMED_SUCCESS = "CONFIRMED_SUCCESS"
    CONFIRMED_FAILURE = "CONFIRMED_FAILURE"
    NOT_DISPATCHED = "NOT_DISPATCHED"
    REJECTED = "REJECTED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


@dataclass(frozen=True, slots=True)
class TaskRecord:
    task_id: str
    task_kind: TaskKind
    actor_identity: ActorIdentity
    idempotency_key: str
    request_fingerprint: str
    creation_state: TaskCreationState
    failure_code: TaskFailureCode | None
    action_id: ActionId | None
    created_at: datetime
    updated_at: datetime


def project_task_state(
    creation_state: TaskCreationState,
    action_state: ActionState | None,
    approval_state: ApprovalState | None,
    operation_state: OperationState | None,
) -> BrowserTaskState: ...
```

### Trusted local principal (`src/aaos/web/principal.py`)

```python
class LocalApproverContextProvider(Protocol):
    def current(self) -> TrustedApproverContext: ...


class LocalPrincipalProvider(Protocol):
    def current_actor(self) -> ActorIdentity: ...
    def current_approver(self) -> TrustedApproverContext: ...
```

The default provider reads only trusted server configuration. Tests inject a
deterministic provider. Browser JSON never supplies either value.

### Task persistence (`src/aaos/tasks/repository.py`)

```python
class TaskRepository:
    def create_or_get(self, record: TaskRecord) -> tuple[TaskRecord, bool]: ...
    def get(self, task_id: str) -> TaskRecord | None: ...
    def get_by_idempotency(
        self, actor_identity: ActorIdentity, idempotency_key: str
    ) -> TaskRecord | None: ...
    def cas_creation_state(
        self,
        task_id: str,
        *,
        expected: TaskCreationState,
        target: TaskCreationState,
        action_id: ActionId | None,
        failure_code: str | None,
        now: datetime,
    ) -> TaskRecord: ...
    def list(self, *, limit: int) -> tuple[TaskRecord, ...]: ...
```

### Browser read/command services (`src/aaos/tasks/service.py`)

```python
class TaskApplicationService:
    def create_calendar_task(
        self,
        request: CalendarCreateRequest,
        *,
        idempotency_key: str,
        now: datetime,
    ) -> TaskDetail: ...

    def get_task(self, task_id: str) -> TaskDetail: ...
    def list_tasks(self, *, limit: int) -> tuple[TaskSummary, ...]: ...
    def list_approvals(self, *, limit: int) -> tuple[ApprovalSummary, ...]: ...
    def get_approval(self, approval_id: ApprovalId) -> ApprovalDetail: ...
    def approve(self, approval_id: ApprovalId, *, now: datetime) -> ApprovalDetail: ...
    def reject(self, approval_id: ApprovalId, *, now: datetime) -> ApprovalDetail: ...

    def execute_approved_task(self, task_id: str, *, now: datetime) -> TaskDetail: ...
```

`execute_approved_task` is a trusted internal composition point with an
injected `ExternalAdapter`; it is not an HTTP route. It calls existing Phase 6
claim/dispatch/outcome/reconciliation functions and never retries.

## Task 1: Define closed task and browser projection contracts

**Files**

- Create `src/aaos/tasks/__init__.py`.
- Create `src/aaos/tasks/contracts.py`.
- Modify `src/aaos/web/contracts.py` only to add strict frozen
  `TaskSummary`, `TaskDetail`, `ApprovalSummary`, `ApprovalDetail`,
  `CalendarCreateTaskRequest`, and `SafeActionSummary` models.
- Create `tests/test_phase7e_task_contracts.py`.
- Create `tests/test_phase7e_browser_contracts.py`.

**RED**

Add tests before production code proving exact field allowlists, closed enums,
strict integer/date/string handling, frozen models, rejection of authority
fields, and deterministic projection of every Phase 6 state including
`UNKNOWN_OUTCOME`, `NOT_DISPATCHED`, and the recovery case of a linked
`PREPARED` action with no approval row remaining `PREPARING`. Run:

```text
uv run pytest tests/test_phase7e_task_contracts.py tests/test_phase7e_browser_contracts.py -q
```

Expected RED is missing task contracts/projections.

**GREEN**

Implement only the closed types above. `SafeActionSummary` contains action kind,
integration label, and bounded calendar event display fields; it never exposes
canonical argument JSON, target hashes, provider IDs, policies, credentials,
paths, SQL, tracebacks, or raw events. `CalendarCreateTaskRequest` mirrors the
existing calendar request fields plus no provider/runtime fields.

**Verification and commit**

Run the focused tests, `uv run ruff check src/aaos/tasks src/aaos/web/contracts.py tests/test_phase7e_*`,
`uv run mypy src/aaos`, and `git diff --check`. Commit only listed files:

```text
feat(phase7e): define task and approval browser contracts
```

**Stop condition:** any need to expose raw Phase 6 payload or add a generic
task/provider field stops this plan.

## Task 2: Add additive task persistence and repository CAS

**Files**

- Create `src/aaos/persistence/task_models.py`.
- Create `migrations/versions/0006_phase7e_task_records.py` after verifying
  `alembic heads` and current `0005_phase6a_approval_core` ordering. If the
  repository generator produces a different revision ID, stop and revise the
  task file list before implementation.
- Modify `migrations/env.py` to import `task_models`.
- Create `src/aaos/tasks/repository.py`.
- Create `tests/test_phase7e_task_migration.py`.
- Create `tests/test_phase7e_task_repository.py`.

**RED**

Write tests for migration upgrade/downgrade, foreign-key enforcement, unique
actor/idempotency identity, unique nullable action link, closed checks,
`ON DELETE RESTRICT`, repository CAS, duplicate creation, conflicting
idempotency fingerprint, and crash-safe `CREATED` recovery. Run the two focused
files before adding the model/migration.

**GREEN**

Define only `task_records` with the fields and constraints in the amendment.
Use the existing `Base`, `UTCDateTime`, `create_database_engine`, and Alembic
patterns. `TaskRepository` owns transactions and compare-and-swap; it exposes
no delete/update-all method and never stores raw request/provider payloads.

Verify a temporary database reaches the actual current head and downgrade
removes only `task_records`; Phase 6 tables remain intact. Run focused tests,
`uv run pytest tests/test_phase6a_migration.py tests/test_phase7e_task_migration.py tests/test_phase7e_task_repository.py -q`,
Ruff, mypy, and diff-check. Commit:

```text
feat(phase7e): persist task records with guarded linkage
```

**Stop condition:** any destructive Phase 6 migration, missing uniqueness/CAS,
or task table containing provider/credential payloads.

## Task 3: Implement trusted task creation and Phase 6 binding

**Files**

- Create `src/aaos/tasks/service.py`.
- Create `tests/test_phase7e_task_service.py`.
- Create `tests/test_phase7e_task_idempotency.py`.

**RED**

Test valid calendar task creation, malformed/expired calendar values,
capability denial, actor derivation, action/approval binding, idempotent replay,
conflicting replay, preparation crash leaving safe `PREPARING`, crash after
action linkage before approval issuance, recovery that observes an existing
approval without duplicating it, and no adapter call. Use a disposable
repository, a fake capability policy, the existing calendar canonicalizer, and
injected clock/configuration.

**GREEN**

`TaskApplicationService.create_calendar_task` validates the closed request,
creates or returns the durable task, derives actor/target/action/policy and
remote precondition from trusted configuration, calls existing
`prepare_action`, CAS-links `action_id`, and only then calls existing
`request_approval` with the server-derived approver. Recovery first reads the
approval by action identity and creates it only when absent, so a crash between
linkage and approval issuance cannot duplicate approvals. It never calls
`execute`, `claim_action`, or an external adapter.

If preparation fails, persist only a bounded closed failure code. A repeated
idempotency key returns the existing task; a different fingerprint returns a
stable conflict. No implicit retry is performed.

Run focused tests, Phase 6A approval tests, OpenAPI generation where applicable,
Ruff, mypy, and diff-check. Commit:

```text
feat(phase7e): bind task creation to phase6 approval
```

**Stop condition:** any browser-selected actor, tool, capability, policy,
provider, raw argument, or automatic execution path.

## Task 4: Add server-derived local principal and mutation request fence

**Files**

- Create `src/aaos/web/principal.py`.
- Create `src/aaos/web/mutation_security.py`.
- Modify `src/aaos/config.py` only to add validated non-secret local principal
  settings with fail-closed absence.
- Create `tests/test_phase7e_principal.py`.
- Create `tests/test_phase7e_mutation_security.py`.

**RED**

Prove browser JSON cannot select actor/approver/context/role/capability,
missing configuration fails closed, invalid identity/context is rejected,
same-origin loopback requests pass, foreign Origin/Host/content type fails,
and tests can inject a deterministic provider without environment mutation.

**GREEN**

Implement `LocalPrincipalProvider` and `LocalApproverContextProvider` using
trusted server settings such as `AAOS_LOCAL_APPROVER_IDENTITY` and
`AAOS_LOCAL_APPROVER_CONTEXT_ID`. Validate the request fence at FastAPI before
decision service calls. Do not add wildcard CORS or claim multi-user
authentication.

Run focused tests, existing web route tests, Ruff, mypy, and diff-check. Commit:

```text
feat(phase7e): derive local approver context at the server boundary
```

**Stop condition:** any identity accepted from request JSON, secret exposed to
the browser, or remote deployment implied without authentication.

## Task 5: Add browser-safe task and approval read services/routes

**Files**

- Create `src/aaos/tasks/read_service.py`.
- Modify `src/aaos/web/routes.py` with `GET /api/v1/tasks`,
  `GET /api/v1/tasks/{task_id}`, `GET /api/v1/approvals`, and
  `GET /api/v1/approvals/{approval_id}`.
- Modify `src/aaos/main.py` for lazy/injected task service wiring.
- Create `tests/test_phase7e_task_reads.py`.
- Create `tests/test_phase7e_approval_reads.py`.
- Modify `tests/test_phase7_openapi_contract.py` only for the reviewed new paths.

**RED**

Add route tests for deterministic ordering, empty state, missing IDs, every
derived status, safe action summary, expiry, no raw ORM/event/provider fields,
and no database/provider/model work at import time.

**GREEN**

Implement read-only projections over `TaskRepository` and existing Phase 6
rows. The service joins by the immutable action link and operation ID but never
returns ORM objects or raw event payloads. Errors use existing bounded Phase 7
error envelopes.

Run focused API tests, full web API tests, OpenAPI export/drift, Ruff, mypy, and
diff-check. Commit:

```text
feat(phase7e): expose safe task and approval reads
```

**Stop condition:** any write in a GET route, raw Phase 6 serialization, or
read-time provider/model access.

## Task 6: Add approve/reject command routes

**Files**

- Modify `src/aaos/web/routes.py` with the two POST decision routes.
- Modify `src/aaos/main.py` to inject the principal provider and task service.
- Create `tests/test_phase7e_approval_commands.py`.
- Create `tests/test_phase7e_approval_concurrency.py`.

**RED**

Test empty-body approve/reject, rejection of extra/authority fields,
server-derived approver binding, missing/expired/terminal decisions, duplicate
click, simultaneous approve/reject, stale tab, dropped-response refetch
contract, and proof that no claim/dispatch/adapter call occurs.

**GREEN**

Routes enforce the mutation fence, obtain `TrustedApproverContext` from the
provider, and call existing `approve_approval`/`reject_approval` with fixed
trusted reasons. Map Phase 6 conflicts to bounded current-state responses.
Do not optimistically mutate browser cache and do not retry automatically.

Run focused tests, all Phase 6A service/concurrency tests, OpenAPI drift, Ruff,
mypy, and diff-check. Commit:

```text
feat(phase7e): delegate browser approval decisions to phase6
```

**Stop condition:** approval route claims, dispatches, reconciles, retries, or
accepts browser-supplied trusted context.

## Task 7: Compose trusted execution and deterministic no-write acceptance

**Files**

- Create `src/aaos/tasks/execution.py`.
- Create `tests/test_phase7e_task_execution.py`.
- Create `tests/test_phase7e_no_real_write_acceptance.py`.

**RED**

Test the internal coordinator with the existing `FakeExternalAdapter` for
success, authoritative failure, stale precondition/`NOT_DISPATCHED`, transport
unknown, durable `CLAIMED -> EXECUTING` before adapter entry, exactly one
adapter attempt, reconciliation without execute, and no automatic retry.
Test that all acceptance calls use a temporary database and zero model/RAG or
real provider calls.

**GREEN**

Implement only a trusted composition around existing Phase 6 functions:
`claim_action`, `dispatch_claimed_operation`, `record_adapter_outcome`, and
`reconcile_operation`. The browser never calls this function directly. Adapter
input remains the existing least-authority `ExecutableAction`.

Run focused tests, Phase 6A/6B fake-adapter regressions, and the no-network
assertion. Commit:

```text
feat(phase7e): compose task execution without new provider authority
```

**Stop condition:** any new retry, executor, provider payload, browser dispatch
route, real external write, model call, or RAG call.

## Task 8: Implement Tasks and Approvals frontend surfaces

**Files**

- Create `frontend/lib/api/tasks.ts`.
- Create `frontend/lib/query/tasks.ts`.
- Modify `frontend/lib/query/keys.ts`.
- Create `frontend/components/aaos/task-list-view.tsx`.
- Create `frontend/components/aaos/approval-list-view.tsx`.
- Create `frontend/app/tasks/page.tsx`.
- Create `frontend/app/approvals/page.tsx`.
- Create `frontend/tests/task-api.test.ts`.
- Create `frontend/tests/task-query.test.ts`.
- Create `frontend/tests/task-views.test.tsx`.
- Create `frontend/tests/approval-views.test.tsx`.

**RED**

Test safe API paths, typed response parsing, idempotency-key generation,
bounded request payloads, query-key separation, loading/empty/error states,
approval status rendering, `UNKNOWN_OUTCOME` distinction, no optimistic
success, approve/reject refetch, uncertain response handling, keyboard/focus,
and absence of raw provider/authority fields.

**GREEN**

Build only the browser-safe projections and closed calendar task form. Use
TanStack Query with no automatic mutation retry. Next.js remains a same-origin
proxy/presentation layer and never constructs approver context or provider
arguments.

Run focused frontend tests, all frontend tests, typecheck, lint, production
build, generated API drift, and diff-check. Commit:

```text
feat(phase7e): add task and approval dashboard views
```

**Stop condition:** direct FastAPI/database/provider access from the browser,
fake task/approval data, optimistic authority, automatic mutation retry, or
7F activity/SSE/system-health code.

## Task 9: Final deterministic Phase 7E acceptance and independent review

**Files**

- Create `tests/phase7e_evaluation/manifest.py` with a deterministic synthetic
  manifest; no real heldout cases.
- Create `tests/phase7e_evaluation/harness.py` using a disposable database and
  injected `FakeExternalAdapter`.
- Create `tests/test_phase7e_acceptance.py`.

**RED -> GREEN**

The harness first proves its manifest is synthetic, contains no real provider
credentials, makes zero model/RAG calls, and cannot import a real provider
client. It then proves task creation, approval, decision concurrency,
`EXECUTING` dispatch ordering, success/failure/unknown outcomes, stale
precondition invalidation, idempotency, safe projections, no duplicate
operations, and refetch-required uncertain HTTP behavior.

Run the complete deterministic harness in a temporary SQLite database. Capture
bounded evidence only. Do not run Phase 6B real acceptance, do not contact
Google/Moodle/LM Studio, do not inspect/consume heldout data, and do not alter
the frozen benchmark.

**Verification and review**

Run all Phase 7E tests, all Phase 5/6A/6B regressions, relevant Phase 4 tests,
frontend tests, OpenAPI drift, Ruff, mypy, `uv lock --check`, `git diff --check`,
and import smoke. Perform an independent review against the amendment for
authority separation, migration safety, concurrency, unknown outcomes,
projection privacy, and no-real-write behavior. No source commit is created by
this verification-only task (`COMMIT: none`); if a defect is found, stop and
create a separate human-approved fix task.

## Cross-task acceptance matrix

| Invariant | Proving task/tests |
| --- | --- |
| AgentTask is not persisted blindly | Task 1 contracts; Task 2 schema review |
| One task/action/operation identity | Tasks 2–3 idempotency/FK tests |
| Capability remains separate from approval | Tasks 3, 6, 7 Phase 6 regressions |
| Browser cannot supply trusted identity | Task 4 and Task 6 mutation tests |
| No raw Phase 6/provider payloads | Tasks 1 and 5 projection tests; Task 8 leak tests |
| No Phase 6 semantic changes | Tasks 2, 6, 7 regression suites |
| `UNKNOWN_OUTCOME` remains distinct | Tasks 1, 5, 7, 9 |
| Precondition stale means `NOT_DISPATCHED` | Tasks 1, 7, 9 |
| Durable `EXECUTING` before adapter | Tasks 7 and 9 |
| No automatic retry | Tasks 3, 6, 7, 8, 9 |
| Duplicate/concurrent decisions are safe | Task 6 and Task 9 |
| Additive migration only | Task 2 direct-schema tests |
| No real provider writes | Tasks 7 and 9 |
| Local-first security is explicit | Task 4 and Task 8 |
| Future multi-user boundary is documented | Amendment and Task 4 |
| No 7F leakage | Global diff review and Task 8 scope audit |

## Final implementation stop gate

After Task 9, stop at:

`HUMAN_REVIEW_OF_PHASE7E_IMPLEMENTATION`

Do not record Phase 7E human acceptance, create `phase-07-passed`, begin
Phase 7F, or authorize any additional real external write from this plan.
