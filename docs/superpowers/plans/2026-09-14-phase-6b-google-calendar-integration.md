# Phase 6B Google Calendar Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:**
Implement one approval-controlled Google Calendar `CREATE_EVENT` capability
using the frozen Phase 6A execution core.

**Architecture:**
Phase 6B provides a narrow Calendar-specific validation, provider-client, and
adapter layer around the frozen Phase 6A approval/execution core. Only one
trusted dedicated calendar and one real external mutation, `CREATE_EVENT`, are
supported. External writes occur only after Phase 6A durable `EXECUTING`, with
zero write retries and read-only reconciliation.

**Tech Stack:**
Python 3.11, frozen AAOS typed contracts, SQLAlchemy/Alembic repositories,
pytest, Ruff, mypy, and the official Google Calendar client/auth libraries
introduced only in Task 4.

**Spec:**
`docs/superpowers/specs/2026-09-13-phase-6b-google-calendar-integration-design.md`

## Global Constraints

- Support exactly one externally writable action: `CREATE_EVENT`.
- Write only to exactly one trusted dedicated calendar.
- Request exactly `https://www.googleapis.com/auth/calendar.events`.
- Use a dedicated Google service identity shared onto that calendar; it does not own/create the calendar.
- Accept timed, timezone-aware events only.
- Forward only `summary`, `start`, `end`, `timezone`, optional `description`, and optional `location`.
- Enforce `summary <= 200`, `description <= 2000`, and `location <= 300` characters.
- Enforce duration `<= 24` hours, scheduling horizon `<= 365` days, and no past-event creation.
- Derive a deterministic trusted `provider_event_id` from the immutable Phase 6A `operation_id`; bind its derivation/version before execution.
- Require remote precondition `provider_event_id` absent immediately before the side effect.
- Do not invoke Google until Phase 6A durable `CLAIMED -> EXECUTING` and dispatch-start evidence are committed.
- Set `events.insert` automatic retry count to `0`; only bounded read retries are allowed.
- Reconciliation calls `get_event` only and requires an exact canonical semantic match for `CONFIRMED_SUCCESS`.
- A not-found reconciliation result remains `UNKNOWN_OUTCOME` and never authorizes a retry.
- Keep credentials outside Git, normal DB rows, model/RAG context, approval/action/event records, logs, and frontend responses.
- Importing Phase 6B modules performs no credential, network, provider, subprocess, model, or database side effects.
- A real-client failure never falls back silently to the fake client.
- Phase 6B local tests and evaluation make zero model calls, zero RAG calls, and zero live Google calls.
- No normal Phase 6A database mutation occurs in provider-contract tests or the deterministic evaluation; integration tests use isolated test repositories only.
- The frozen Phase 6A contracts, state machine, service, repository, event, and security behavior are consumed unchanged.
- No update, delete/cancel, attendees, invitations, recurrence, reminders, conference links, attachments, extended properties, multiple calendars, arbitrary Google APIs, ACL management, calendar-list writes, settings modification, or background synchronization.
- No Phase 6C functionality, GitHub/MCP integration, dashboard, REPL, or multi-agent behavior.

## Repository structure and frozen interfaces

The Phase 6B package is added under the existing `src/aaos` namespace:

```text
src/aaos/integrations/__init__.py
src/aaos/integrations/google_calendar/__init__.py
src/aaos/integrations/google_calendar/contracts.py
src/aaos/integrations/google_calendar/canonicalization.py
src/aaos/integrations/google_calendar/client.py
src/aaos/integrations/google_calendar/adapter.py
src/aaos/integrations/google_calendar/errors.py
```

Phase 6A interfaces consumed by Tasks 3 and 7 are frozen:

```python
class ExternalAdapter(Protocol):
    def execute(self, action: ExecutableAction) -> AdapterExecutionOutcome:
        pass

    def reconcile(self, operation_id: OperationId) -> AdapterReconciliationOutcome:
        pass


def prepare_action(
    repository: ApprovalRepository,
    definition: Phase6ActionDefinition,
    *,
    actor_identity: ActorIdentity,
    target_identity: TargetIdentity,
    arguments: Mapping[str, JSONValue],
    remote_precondition: RemotePrecondition,
    action_expires_at: datetime,
    capability_policy: PermissionPolicy,
    now: datetime,
) -> PreparedAction:
    pass


def request_approval(
    repository: ApprovalRepository,
    action_id: ActionId,
    approver: TrustedApproverContext,
    approval_expires_at: datetime,
    now: datetime,
) -> ApprovalRecord:
    pass


def approve_approval(
    repository: ApprovalRepository,
    approval_id: ApprovalId,
    approver: TrustedApproverContext,
    now: datetime,
) -> ApprovalRecord:
    pass


def claim_action(
    repository: ApprovalRepository,
    action_id: ActionId,
    *,
    capability_policy: PermissionPolicy,
    definition: Phase6ActionDefinition,
    now: datetime,
    claim_identity: ClaimIdentity,
) -> ClaimResult:
    pass


def dispatch_claimed_operation(
    repository: ApprovalRepository,
    adapter: ExternalAdapter,
    operation_id: OperationId,
    *,
    capability_policy: PermissionPolicy,
    definition: Phase6ActionDefinition,
    now: datetime,
) -> DispatchResult:
    pass


def record_adapter_outcome(
    repository: ApprovalRepository,
    operation_id: OperationId,
    *,
    expected_revision: int,
    outcome: AdapterExecutionOutcome,
) -> OutcomeRecordResult:
    pass


def reconcile_operation(
    repository: ApprovalRepository,
    adapter: ExternalAdapter,
    operation_id: OperationId,
    *,
    expected_revision: int,
    now: datetime,
) -> ReconciliationResult:
    pass
```

The Phase 6A `ExecutableAction` contains only `target_identity`, `action_type`,
canonical argument bytes, `operation_id`, and `remote_precondition`. The
Calendar adapter receives that projection, never an `ActionRecord`, session,
approval, policy, model text, or credential.

Each task below is independently reviewed and committed. The sequence is
strict: a task's human review freezes its commit before the next task starts.

---

### Task 1: Closed Calendar contracts and canonicalization

**Files:**
- Create: `src/aaos/integrations/__init__.py`
- Create: `src/aaos/integrations/google_calendar/__init__.py`
- Create: `src/aaos/integrations/google_calendar/contracts.py`
- Create: `src/aaos/integrations/google_calendar/canonicalization.py`
- Test: `tests/test_google_calendar_contracts.py`
- Test: `tests/test_google_calendar_canonicalization.py`
- Do not modify Phase 6A files or `pyproject.toml`.

**Interfaces:**
- **Consumes:** frozen `OperationId`, `TargetIdentity`, `CanonicalArguments`, `JSONValue`, and repository datetime conventions from `aaos.approval.contracts`.
- **Produces:**
  - `CalendarCreateRequest(summary: str, start: datetime, end: datetime, timezone: str, description: str | None = None, location: str | None = None)`;
  - `CanonicalCalendarEvent(summary: str, start: str, end: str, timezone: str, description: str | None, location: str | None)`;
  - `GoogleCalendarConfig(calendar_id: str, expected_service_identity: str, oauth_scope: str, credential_path: str, provider_adapter_version: str, enabled: bool)`;
  - `canonicalize_calendar_request(request: CalendarCreateRequest, *, now: datetime) -> CanonicalCalendarEvent`;
  - `canonical_event_arguments(event: CanonicalCalendarEvent) -> Mapping[str, JSONValue]`;
  - `derive_provider_event_id(operation_id: OperationId, *, derivation_version: str) -> str`;
  - `calendar_target(config: GoogleCalendarConfig) -> TargetIdentity`.

- [ ] **Step 1: Write failing contract tests.**

  Add tests named `test_valid_minimal_request`, `test_valid_complete_request`,
  `test_unknown_fields_are_rejected`,
  `test_forbidden_provider_fields_are_not_representable`,
  `test_user_cannot_choose_provider_event_id`, and
  `test_user_cannot_choose_calendar_id`. Assert exact closed-field sets and
  typed constructor failures for `calendar_id`, `provider_event_id`,
  attendees, recurrence, reminders, conference data, and arbitrary mappings.

- [ ] **Step 2: Run the contract RED tests.**

  Run:
  `uv run pytest tests/test_google_calendar_contracts.py -q`

  Expected RED: the new package/types are absent.

- [ ] **Step 3: Write failing canonicalization tests.**

  Add tests named `test_summary_limit`, `test_description_limit`,
  `test_location_limit`, `test_nul_and_control_text_rejected`,
  `test_invalid_timezone_rejected`, `test_floating_datetime_rejected`,
  `test_start_must_precede_end`, `test_duration_must_not_exceed_24_hours`,
  `test_past_start_rejected`, `test_horizon_must_not_exceed_365_days`,
  `test_canonical_rfc3339_is_stable`, `test_absent_optionals_have_one
  representation`, `test_provider_id_is_deterministic`, and
  `test_different_operation_ids_produce_different_provider_ids`.

- [ ] **Step 4: Run canonicalization RED tests.**

  Run:
  `uv run pytest tests/test_google_calendar_canonicalization.py -q`

  Expected RED: canonicalization and provider-ID helpers are absent.

- [ ] **Step 5: Implement the minimal closed value objects.**

  Use frozen dataclasses with strict constructor checks. Normalize Unicode
  text once, reject NUL/control payloads, require a valid IANA timezone, emit
  canonical RFC3339 instants, preserve the timezone identity, and serialize
  optionals deterministically. `derive_provider_event_id` must hash only the
  trusted operation ID, fixed namespace, and derivation version, then emit a
  bounded Google-safe identifier. No user/model field can override it.

- [ ] **Step 6: Run focused GREEN tests.**

  Run:
  `uv run pytest tests/test_google_calendar_contracts.py tests/test_google_calendar_canonicalization.py -q`

  Expected: all Task 1 tests pass with no provider import or I/O.

- [ ] **Step 7: Run Task 1 regressions and static checks.**

  Run `uv run pytest tests/test_phase6a_contracts.py tests/test_phase6a_imports.py -q`,
  `uv run ruff format --check src tests`, `uv run ruff check src tests`,
  `uv run mypy src tests`, `uv lock --check`, and `git diff --check`.

- [ ] **Step 8: Review and commit.**

  Confirm no Google SDK, credential read, network, DB, or Phase 6A state call
  exists. Obtain independent review, fix only Task 1 findings in new commits,
  then commit:
  `git add src/aaos/integrations tests/test_google_calendar_contracts.py tests/test_google_calendar_canonicalization.py && git commit -m "feat(phase6b): add calendar contracts and canonicalization"`.

**Acceptance gate:** closed canonical DTOs reject unsupported fields and all
time/text constraints; canonical bytes and provider IDs are deterministic;
all imports are side-effect free; Phase 6A regressions remain green.

**Future-phase exclusions:** no Google SDK, credentials, network, provider
client, adapter, database mutation, or real write.

---

### Task 2: Narrow provider client, fake transport, projections, and errors

**Files:**
- Create: `src/aaos/integrations/google_calendar/client.py`
- Create: `src/aaos/integrations/google_calendar/errors.py`
- Test: `tests/test_google_calendar_client.py`
- Do not modify Task 1 or Phase 6A production files.

**Interfaces:**
- **Consumes:** `CanonicalCalendarEvent`, `GoogleCalendarConfig`, and Task 1 provider-ID helpers.
- **Produces:**
  - `GoogleCalendarClient(Protocol)` with `get_event(calendar_id: str, provider_event_id: str) -> ObservedGoogleEvent | None` and `insert_event(calendar_id: str, event: CanonicalCalendarEvent, provider_event_id: str) -> ObservedGoogleEvent`;
  - `FakeGoogleCalendarClient` with deterministic `get_count`, `insert_count`, `events`, and injected failure modes;
  - `ObservedGoogleEvent` closed DTO containing provider ID, six approved semantic fields, and no raw response;
  - `GoogleProviderErrorKind` values `AUTHENTICATION_REJECTED`, `PERMISSION_REJECTED`, `INVALID_REQUEST`, `NOT_FOUND`, `ALREADY_EXISTS`, `RATE_LIMITED`, `SERVER_ERROR`, `TIMEOUT`, `TRANSPORT_ERROR`, `MALFORMED_RESPONSE`;
  - `GoogleProviderError(kind: GoogleProviderErrorKind, safe_reason: str)` with bounded safe text only;
  - `project_observed_event(raw: object) -> ObservedGoogleEvent` rejecting unknown/unbounded response fields.

- [ ] **Step 1: Write failing client-contract tests.**

  Add `test_get_absent_returns_none`, `test_get_exact_event_returns_projection`,
  `test_insert_success`, `test_duplicate_provider_id_is_closed_error`,
  `test_timeout_is_typed`, `test_transport_error_is_typed`,
  `test_permission_rejection_is_typed`, `test_invalid_request_is_typed`,
  `test_server_failure_is_typed`, `test_malformed_response_is_typed`,
  `test_observed_projection_has_only_approved_fields`,
  `test_raw_provider_response_is_not_exposed`, and
  `test_client_has_no_generic_request_surface`.

- [ ] **Step 2: Capture RED.**

  Run `uv run pytest tests/test_google_calendar_client.py -q` and record RED
  because the protocol, fake client, projection, and error taxonomy do not yet
  exist.

- [ ] **Step 3: Implement the closed fake/provider contracts.**

  Keep fake state in memory behind a lock. Make `insert_event` enforce the
  supplied deterministic ID and return only `ObservedGoogleEvent` or a closed
  `GoogleProviderError`. Store no raw provider object. Do not import an
  official SDK or read configuration in this task.

- [ ] **Step 4: Run GREEN and regression tests.**

  Run `uv run pytest tests/test_google_calendar_client.py tests/test_google_calendar_contracts.py tests/test_google_calendar_canonicalization.py -q`,
  then Ruff, mypy, `uv lock --check`, and `git diff --check`.

- [ ] **Step 5: Review and commit.**

  Independent review must verify no generic Google authority, no raw response
  retention, and no network/credential behavior. Commit:
  `git add src/aaos/integrations/google_calendar/client.py src/aaos/integrations/google_calendar/errors.py tests/test_google_calendar_client.py && git commit -m "feat(phase6b): add narrow fake Google Calendar client"`.

**Acceptance gate:** only `get_event` and `insert_event` exist; the fake
provider proves typed errors and bounded observations with zero network or
credential access.

**Future-phase exclusions:** no real SDK, factory, credentials, adapter,
Phase 6A calls, or live Google request.

---

### Task 3: Phase 6A-compatible Google Calendar adapter

**Files:**
- Create: `src/aaos/integrations/google_calendar/adapter.py`
- Create: `tests/test_google_calendar_adapter.py`
- Create: `tests/test_google_calendar_integration.py`
- Do not modify `src/aaos/approval/*`.

**Interfaces:**
- **Consumes:** frozen `ExternalAdapter`, `ExecutableAction`, `AdapterExecutionOutcome`, `AdapterReconciliationOutcome`, `AdapterOutcomeKind`, `OperationId`, `RemotePrecondition`, Phase 6A dispatch/outcome/reconciliation functions, Task 1 DTOs, and Task 2 client/error types.
- **Produces:**
  - `GoogleCalendarAdapter(client: GoogleCalendarClient, config: GoogleCalendarConfig, *, derivation_version: str)` implementing `ExternalAdapter`;
  - `GoogleCalendarAdapter.execute(action: ExecutableAction) -> AdapterExecutionOutcome`;
  - `GoogleCalendarAdapter.reconcile(operation_id: OperationId) -> AdapterReconciliationOutcome`;
  - `decode_calendar_action(action: ExecutableAction) -> CanonicalCalendarEvent`;
  - `CalendarAdapterExecutionTrace(insert_count: int, get_count: int, last_calendar_id: str | None)` for test-only bounded evidence.

- [ ] **Step 1: Write failing adapter tests.**

  Add `test_adapter_accepts_only_executable_action`,
  `test_absent_precondition_allows_one_insert`,
  `test_existing_event_returns_precondition_stale_and_zero_insert`,
  `test_precondition_changes_between_advisory_check_and_execute`,
  `test_provider_id_collision_never_generates_alternate_id`,
  `test_insert_success_maps_to_success`,
  `test_definite_rejection_maps_to_authoritative_failure`,
  `test_timeout_maps_to_unknown`, `test_reconcile_is_read_only`,
  `test_exact_semantic_match_reconciles_success`,
  `test_mismatch_reconciliation_remains_unknown`,
  `test_not_found_reconciliation_remains_unknown`,
  `test_no_write_retry_after_unknown`, and
  `test_raw_provider_error_is_not_returned`.

- [ ] **Step 2: Write failing Phase 6A ordering/concurrency tests.**

  Add `test_durable_executing_precedes_provider_write`,
  `test_two_workers_can_insert_at_most_once`,
  `test_adapter_receives_only_least_authority_projection`,
  `test_consumed_approval_and_capability_are_required`, and
  `test_config_or_target_drift_fails_before_write`. Use isolated SQLite
  repositories and fake clients; never use the normal AAOS DB.

- [ ] **Step 3: Capture RED.**

  Run:
  `uv run pytest tests/test_google_calendar_adapter.py tests/test_google_calendar_integration.py -q`

  Expected RED because the adapter and composition seam are absent.

- [ ] **Step 4: Implement the adapter without a second lifecycle.**

  Decode only canonical arguments. Verify action type, target, derivation
  version, and trusted configuration binding. At `execute`, call `get_event`
  immediately before the single `insert_event`; existing ID returns
  `PRECONDITION_STALE` and zero inserts. Map ambiguous errors to
  `UNKNOWN`/`TRANSPORT_FAILURE`, map only proven pre-dispatch rejection to
  `AUTHORITATIVE_FAILURE`, and never retry insert. `reconcile` calls `get_event`
  only and compares all six semantic fields exactly.

- [ ] **Step 5: Run GREEN and frozen regressions.**

  Run:
  `uv run pytest tests/test_google_calendar_adapter.py tests/test_google_calendar_integration.py tests/test_phase6a_adapters.py tests/test_phase6a_reconciliation.py tests/test_phase6a_service.py -q`

  Then run Ruff, mypy, `uv lock --check`, and `git diff --check`.

- [ ] **Step 6: Review and commit.**

  Review side-effect ordering, TOCTOU behavior, unknown outcomes, no retry,
  and least-authority projection. Commit:
  `git add src/aaos/integrations/google_calendar/adapter.py tests/test_google_calendar_adapter.py tests/test_google_calendar_integration.py && git commit -m "feat(phase6b): integrate Calendar adapter with phase6a"`.

**Acceptance gate:** fake-provider execution uses the actual Phase 6A durable
dispatch boundary, performs at most one insert, enforces preconditions at the
side-effect boundary, reconciles read-only, and exposes no raw provider data.

**Future-phase exclusions:** no official SDK, credentials, network, real write,
or Phase 6A lifecycle modification.

---

### Task 4: Trusted configuration and real Google client factory

**Files:**
- Modify: `src/aaos/integrations/google_calendar/client.py`
- Modify: `src/aaos/integrations/google_calendar/contracts.py` only if a typed factory result is required
- Test: `tests/test_google_calendar_factory.py`
- Test: `tests/test_google_calendar_imports.py`
- Modify: `pyproject.toml` and `uv.lock` only for the official Google client/auth dependencies selected here

**Interfaces:**
- **Consumes:** Task 1 `GoogleCalendarConfig`; Task 2 `GoogleCalendarClient`, `GoogleProviderError`, and projection; injected SDK seam; trusted environment configuration.
- **Produces:**
  - `GoogleCalendarClientFactory.build(config: GoogleCalendarConfig, *, sdk: GoogleSdkFacade | None = None) -> GoogleCalendarClient`;
  - `GoogleSdkFacade( get_event(calendar_id: str, provider_event_id: str) -> object | None, insert_event(calendar_id: str, event: CanonicalCalendarEvent, provider_event_id: str) -> object )`, a protocol exposing only the two narrow API operations;
  - `RealGoogleCalendarClient` implementing `GoogleCalendarClient` with finite call deadlines;
  - `REAL_ADAPTER_UNAVAILABLE` as a closed factory error.

- [ ] **Step 1: Verify the official dependency/API surface before editing metadata.**

  Inspect the official package API and current lockfile. Record the exact
  dependency names/versions and the import paths used for service-account
  credentials, scoped authorization, and Calendar v3 events. Do not install
  or import credentials; do not contact Google.

- [ ] **Step 2: Write failing factory/import tests.**

  Add `test_disabled_config_is_unavailable`, `test_missing_credential_is
  unavailable`, `test_malformed_credential_is_unavailable`,
  `test_service_identity_mismatch_is_unavailable`,
  `test_scope_mismatch_is_unavailable`, `test_calendar_mismatch_is_unavailable`,
  `test_real_factory_never_falls_back_to_fake`,
  `test_factory_uses_only_two_sdk_operations`, and fresh-process tests
  `test_import_does_not_read_credentials`, `test_import_does_not_open_socket`,
  `test_import_does_not_initialize_google_client`, and
  `test_import_does_not_connect_database`.

- [ ] **Step 3: Capture RED.**

  Run:
  `uv run pytest tests/test_google_calendar_factory.py tests/test_google_calendar_imports.py -q`

  Expected RED because the factory and injected SDK facade are absent.

- [ ] **Step 4: Add dependencies and implement the fail-closed factory.**

  Add only the verified official packages. Keep the factory disabled by
  default; validate trusted calendar, exact OAuth scope, credential path
  outside the repository, expected service identity, adapter version, and
  finite timeouts before constructing the client. Use the injected facade in
  tests. Never perform interactive login, use a fallback identity, or select a
  fake client after a real-client failure.

- [ ] **Step 5: Run GREEN and lock/import checks.**

  Run the two factory/import test modules, `uv lock --check`,
  `uv run ruff format --check src tests`, `uv run ruff check src tests`,
  `uv run mypy src tests`, and `git diff --check`.

- [ ] **Step 6: Review and commit.**

  Confirm credentials remain confined to the factory/client and that normal
  pytest never reaches a live SDK. Commit the dependency and source/test
  changes together:
  `git add pyproject.toml uv.lock src/aaos/integrations/google_calendar/client.py tests/test_google_calendar_factory.py tests/test_google_calendar_imports.py && git commit -m "feat(phase6b): add fail-closed Google client factory"`.

**Acceptance gate:** real construction is explicit, disabled by default,
identity/scope/calendar-bound, finite, and testable without a live request;
imports remain side-effect free and there is no fake fallback.

**Future-phase exclusions:** no real credential use in tests, no Google API
call, no real write, no Calendar mutation.

---

### Task 5: Security, privacy, and import hardening

**Files:**
- Modify: `src/aaos/integrations/google_calendar/contracts.py`, `client.py`, `adapter.py`, or `errors.py` only for narrowly proven boundary defects
- Create: `tests/test_google_calendar_security.py`
- Extend: `tests/test_google_calendar_imports.py`
- Do not modify Phase 6A production code or tests to weaken them

**Interfaces:**
- **Consumes:** all Task 1–4 closed types and fake/injected clients; frozen Phase 6A security contracts.
- **Produces:** no new authority-bearing production interface; only bounded sanitizers/assertion helpers if a failing security test proves one is necessary.

- [ ] **Step 1: Write adversarial failing tests.**

  Add tests attempting to inject/override `calendar_id`, credential path,
  OAuth scope, service identity, `provider_event_id`, attendees, recurrence,
  reminders, `conferenceData`, `sendUpdates`, attachments, extended
  properties, arbitrary Google JSON, arbitrary API method, and raw callable or
  provider-client authority. Add counters for unauthorized writes, wrong
  calendar writes, credential exposure, raw-response exposure, and write
  retries.

- [ ] **Step 2: Write fresh-process side-effect tests.**

  Patch file open, sockets, subprocess creation, database engine creation,
  credential parsing, and SDK construction before importing every Phase 6B
  module. Assert `NO_SIDE_EFFECTS` and zero model/RAG calls.

- [ ] **Step 3: Capture RED.**

  Run:
  `uv run pytest tests/test_google_calendar_security.py tests/test_google_calendar_imports.py -q`

  Expected RED for any uncovered injection or privacy path; if all tests are
  already green, retain the tests as the hardening evidence and do not add
  speculative code.

- [ ] **Step 4: Fix only proven boundary defects.**

  Ensure all provider values are projected into closed DTOs, logs use bounded
  IDs/error kinds/timestamps only, credentials never enter records or logs,
  and real-client failures cannot select the fake client. Do not use a keyword
  blocklist as the security boundary; typed allowlists remain authoritative.

- [ ] **Step 5: Run security, Phase 6A, and Phase 4 regressions.**

  Run all Phase 6B tests, frozen Phase 6A tests, relevant Phase 4 RAG/runtime
  tests, full `uv run pytest -q`, Ruff, mypy, `uv lock --check`, and
  `git diff --check`. A known environment-only FlagEmbedding timeout is
  reported separately and never weakened.

- [ ] **Step 6: Independent security review and commit.**

  Review least authority, credential isolation, no generic Google surface,
  no live pytest path, no writes outside the dedicated calendar, and no retry.
  Fix Critical/Important findings in new commits and commit:
  `git add src/aaos/integrations/google_calendar tests/test_google_calendar_security.py tests/test_google_calendar_imports.py && git commit -m "test(phase6b): harden Calendar security boundaries"`.

**Acceptance gate:** every adversarial authority attempt fails closed;
all zero counters pass; imports are side-effect free; frozen Phase 6A/Phase 4
behavior remains unchanged.

**Future-phase exclusions:** no evaluation matrix, real provider call,
approval UI, GitHub/MCP, or dashboard.

---

### Task 6: Freeze and execute the deterministic 30-case evaluation

**Files:**
- Create: `tests/phase6b_evaluation/__init__.py`
- Create: `tests/phase6b_evaluation/manifest.py`
- Create: `tests/phase6b_evaluation/harness.py`
- Create: `tests/test_phase6b_evaluation.py`
- Do not modify Phase 6A or Phase 6B benchmark/evaluator contracts after the freeze commit.

**Interfaces:**
- **Consumes:** Task 1–5 contracts, fake client/adapter, isolated Phase 6A test repository, and closed security counters.
- **Produces:**
  - `PHASE6B_EVALUATION_SCHEMA_VERSION = "phase6b-google-calendar-v1"`;
  - `PHASE6B_CASE_IDS` as a tuple containing exactly `phase6b-cal-001` through `phase6b-cal-030` in numeric order;
  - `EvaluationOwner(StrEnum)` with the six owner values used in the matrix;
  - `FixtureSetup(kind: str, parameters: Mapping[str, JSONValue])` and `ExpectedInvariant(kind: str, value: str)` as closed typed dataclasses;
  - typed `EvaluationCase(case_id: str, owner: EvaluationOwner, setup: FixtureSetup, expected: ExpectedInvariant)`;
  - `run_case(case: EvaluationCase, executor: FakeOnlyExecutor) -> CaseResult`;
  - `run_evaluation(manifest: FrozenManifest) -> EvaluationReport`;
  - report counters `UNAUTHORIZED_WRITE_COUNT`, `WRONG_CALENDAR_WRITE_COUNT`, `CREDENTIAL_EXPOSURE_COUNT`, `RAW_PROVIDER_RESPONSE_EXPOSURE_COUNT`, `WRITE_RETRY_COUNT`, `MODEL_CALL_COUNT`, and `RAG_EXECUTION_COUNT`.

- [ ] **Step 1: Define the exact frozen matrix before execution.**

  Implement exactly these IDs and owners; each setup is deterministic and
  uses only fake/in-memory or isolated test-repository state:

  | IDs | Owner | Setup | Expected invariant |
  | --- | --- | --- | --- |
  | `phase6b-cal-001` | validation_canonicalization | valid minimal timed event | canonical DTO created with six approved fields |
  | `phase6b-cal-002` | validation_canonicalization | valid complete event | optionals preserved canonically |
  | `phase6b-cal-003` | validation_canonicalization | summary length 201 | validation rejects before provider |
  | `phase6b-cal-004` | validation_canonicalization | invalid IANA timezone | validation rejects before provider |
  | `phase6b-cal-005` | validation_canonicalization | duration 24h+1s | validation rejects before provider |
  | `phase6b-cal-006` | validation_canonicalization | unknown provider field/control text | closed schema rejects and no authority appears |
  | `phase6b-cal-007` | trusted_configuration_authority | integration disabled | real factory unavailable |
  | `phase6b-cal-008` | trusted_configuration_authority | missing credential path | real factory unavailable |
  | `phase6b-cal-009` | trusted_configuration_authority | wrong service identity | real factory unavailable |
  | `phase6b-cal-010` | trusted_configuration_authority | wrong OAuth scope | real factory unavailable |
  | `phase6b-cal-011` | trusted_configuration_authority | request text supplies another calendar | trusted configured calendar remains sole target |
  | `phase6b-cal-012` | precondition_duplicate_prevention | remote ID absent | exactly one insert permitted |
  | `phase6b-cal-013` | precondition_duplicate_prevention | remote ID already exists | zero inserts and precondition-stale outcome |
  | `phase6b-cal-014` | precondition_duplicate_prevention | remote state changes after advisory get | execute-side fence yields zero inserts |
  | `phase6b-cal-015` | precondition_duplicate_prevention | two workers race same provider ID | at most one insert, no alternate ID |
  | `phase6b-cal-016` | precondition_duplicate_prevention | action target/config drift | zero writes before adapter call |
  | `phase6b-cal-017` | provider_error_taxonomy | definite pre-dispatch permission rejection | authoritative failure only |
  | `phase6b-cal-018` | provider_error_taxonomy | insert timeout | unknown outcome and zero retry |
  | `phase6b-cal-019` | provider_error_taxonomy | connection reset after request | unknown outcome and zero retry |
  | `phase6b-cal-020` | provider_error_taxonomy | malformed response after dispatch | unknown outcome, raw response discarded |
  | `phase6b-cal-021` | provider_error_taxonomy | rate/server error | typed safe error, no write retry |
  | `phase6b-cal-022` | unknown_reconciliation | accepted insert then exact get | exact match confirms success |
  | `phase6b-cal-023` | unknown_reconciliation | existing semantic mismatch | remains unknown, no mutation |
  | `phase6b-cal-024` | unknown_reconciliation | provider not found | remains unknown, no retry |
  | `phase6b-cal-025` | unknown_reconciliation | crash after durable executing before call | recovery skips execute and remains unknown |
  | `phase6b-cal-026` | unknown_reconciliation | late result loses CAS | stale audit only; persisted state wins |
  | `phase6b-cal-027` | privacy_import_security | credential-like text in request/observation | no credential exposure |
  | `phase6b-cal-028` | privacy_import_security | raw provider response with extra fields | projection drops raw fields |
  | `phase6b-cal-029` | privacy_import_security | adversarial API method/callable field | no generic provider authority |
  | `phase6b-cal-030` | privacy_import_security | fresh import with I/O guards | `NO_SIDE_EFFECTS` |

  Freeze manifest serialization and its SHA-256 in the same Task 6 commit,
  before running any case. The manifest contains no credentials and no live
  provider data.

- [ ] **Step 2: Write manifest-integrity RED tests.**

  Add `test_exact_case_ids_and_owners`, `test_manifest_fingerprint_is_stable`,
  `test_no_duplicate_case_ids`, `test_all_30_cases_are_mapped`,
  `test_evaluation_uses_fake_only_executor`, and
  `test_normal_pytest_has_zero_model_rag_and_live_google_calls`.

- [ ] **Step 3: Capture RED, then implement the frozen harness.**

  Run `uv run pytest tests/test_phase6b_evaluation.py -q` and record RED.
  Implement typed fixtures, deterministic fake execution, report hashing, and
  hard-gate counters. The harness must never import model/RAG gateways,
  credentials, or a real client.

- [ ] **Step 4: Run the evaluation GREEN and verify gates.**

  Run:
  `uv run pytest tests/test_phase6b_evaluation.py -q`

  Require all 30 cases pass and every hard-gate counter equals zero. Freeze
  the report and private local evidence bundle without changing membership.

- [ ] **Step 5: Run full regressions and independent review.**

  Run all Phase 6B tests, all frozen Phase 6A tests, relevant Phase 4 tests,
  canonical full pytest, Ruff, mypy, `uv lock --check`, `git diff --check`,
  and fresh import smoke. Review for case-specific tuning, hidden live calls,
  benchmark mutation, and security-counter masking.

- [ ] **Step 6: Commit and freeze evaluation evidence.**

  Commit:
  `git add tests/phase6b_evaluation tests/test_phase6b_evaluation.py && git commit -m "test(phase6b): freeze deterministic Calendar evaluation"`.

**Acceptance gate:** exact 30-case manifest is frozen before execution; all
cases pass; all seven hard gates are zero; no model/RAG/live Google call is
possible in ordinary pytest.

**Future-phase exclusions:** no real Google acceptance, no heldout/live
provider operation beyond the separately gated Task 7.

---

### Task 7: One separately gated real Google acceptance create

**Files:**
- Create: `tests/phase6b_evaluation/real_acceptance_cli.py`
- Create: `tests/test_google_calendar_real_gate.py`
- Do not modify the frozen Task 6 manifest, Phase 6A production code, or fake evaluator.

**Interfaces:**
- **Consumes:** frozen Task 1–6 package, real client factory, Phase 6A preparation/approval/claim/dispatch/outcome/reconciliation APIs, and a trusted human approval context.
- **Produces:**
  - `TrustedRealAcceptanceAuthorization(authorization_id: str, issued_at: datetime, manifest_fingerprint: str)` as a trusted non-secret context;
  - `run_real_acceptance(*, authorization: TrustedRealAcceptanceAuthorization, request: CalendarCreateRequest) -> RealAcceptanceReport`;
  - CLI command `uv run python -m tests.phase6b_evaluation.real_acceptance_cli`;
  - `RealAcceptanceReport` containing only bounded IDs, states, provider kind, timestamps, and hashes.

- [ ] **Step 1: Write the gate tests before any live command is available.**

  Add `test_normal_pytest_rejects_real_acceptance`,
  `test_missing_human_authorization_fails_closed`,
  `test_acceptance_request_has_no_forbidden_fields`,
  `test_acceptance_requires_phase6a_executing`,
  `test_acceptance_timeout_reconciles_without_retry`, and
  `test_acceptance_report_excludes_credentials_and_raw_provider_body`.
  Inject a fake client for all ordinary tests; no test constructs a live
  Google client or calls the CLI.

- [ ] **Step 2: Capture RED.**

  Run:
  `uv run pytest tests/test_google_calendar_real_gate.py -q`

  Expected RED because the explicit gate and CLI are absent.

- [ ] **Step 3: Implement the separately gated operator path.**

  Require an explicit trusted authorization object and a unique future event
  with approximately 15-minute duration, configured timezone, bounded summary,
  bounded description marker, optional bounded location, and no unsupported
  fields. Route through canonicalization, Phase 6A prepare/request approval/
  claim/dispatch, real client precondition, one insert, and read-only
  reconciliation. Keep the authorization and credential values out of logs,
  records, and report contents.

- [ ] **Step 4: Run gate tests and all local regressions.**

  Run the gate tests with the fake client, all Phase 6B/6A tests, relevant
  Phase 4 tests, canonical full pytest, Ruff, mypy, `uv lock --check`,
  `git diff --check`, and fresh import smoke. Do not invoke the CLI.

- [ ] **Step 5: Human gate before the only real write.**

  Stop with `STOP_GATE=HUMAN_AUTHORIZATION_FOR_PHASE6B_REAL_GOOGLE_ACCEPTANCE`.
  Only after explicit authorization may the operator execute the exact CLI
  command once. The command must use trusted configuration and credential
  sources, never print them, and must not expose raw provider responses.

- [ ] **Step 6: Execute and finalize exactly one acceptance event.**

  Capture Phase 6A/6B evidence. If the result is ambiguous, perform only
  read-only reconciliation; never retry or create a second event. Exact
  semantic match is `CONFIRMED_SUCCESS`; otherwise stop as unknown. Cleanup is
  manual in Google Calendar because delete authority is out of scope.

- [ ] **Step 7: Review and final tag gate.**

  Verify `VALIDATION_TESTS`, `PROVIDER_CONTRACT_TESTS`, `PHASE6A_INTEGRATION
  _TESTS`, `SECURITY_TESTS`, and `IMPORT_SAFETY` pass; write retries,
  unauthorized writes, wrong-calendar writes, credential exposure, raw
  response exposure, model calls, and RAG calls are all zero. A human reviews
  the report before creating `phase-06b-passed` on the exact accepted HEAD.

**Acceptance gate:** one real `CREATE_EVENT` succeeds through Phase 6B and
Phase 6A with durable `EXECUTING` first, at most one insert, and confirmed
success by exact semantic reconciliation. No direct debug insert counts.

**Future-phase exclusions:** no update/delete, additional real writes,
GitHub/MCP, Phase 6C, dashboard, REPL, or automatic cleanup.

---

## Spec-to-task coverage map

| Governing spec invariant | Plan coverage |
| --- | --- |
| `CREATE_EVENT` only and six-field closed DTO | Tasks 1, 2, 3, 5 |
| one trusted calendar, service identity, exact OAuth scope | Tasks 1, 4, 5, 7 |
| timed timezone-aware bounds and canonical bytes | Task 1 tests and acceptance gate |
| deterministic operation-bound provider ID | Tasks 1 and 3 |
| Phase 6A approval/capability/claim reuse | Tasks 3 and 7 integration tests |
| durable `EXECUTING` before provider I/O | Tasks 3 and 7 ordering tests |
| live precondition at side-effect boundary and TOCTOU | Task 3 tests; Task 6 cases 012–016 |
| one insert and zero write retries | Tasks 3, 5, 6, and 7 |
| closed provider error taxonomy | Task 2 and Task 3 |
| unknown outcome and read-only exact reconciliation | Tasks 3, 6, and 7 |
| crash/late-result conservatism | Tasks 3 and 6 cases 025–026 |
| credentials confined to factory/provider | Tasks 4, 5, and 7 |
| no generic Google authority | Tasks 2, 3, 5 |
| import/side-effect safety | Tasks 1, 4, 5, 6 |
| fake-only deterministic evaluation | Task 6 |
| real acceptance human gate | Task 7 |
| all Phase 6B non-goals and Phase 6A freeze | Global Constraints and every task exclusion |

## Plan self-review and handoff

Before implementation begins, run these checks on this plan:

- Search for unresolved placeholders, vague implementation verbs, and
  unfinished ellipses; none may remain.
- Verify every type/function referenced in Tasks 2–7 is produced by an earlier
  task or is explicitly listed as frozen Phase 6A.
- Verify no task adds a second lifecycle, generic Google method, attendee or
  invite capability, write retry, credential log, live pytest path, or Phase
  6A modification.
- Verify the Task 6 manifest is exactly 30 IDs with the stated ownership and
  is frozen before its first execution.
- Verify Task 7 is the only task with a possible real Google call and that it
  has an explicit human authorization stop gate.
- Obtain an independent plan review against the governing spec. Resolve only
  Critical/Important documentation findings with new scoped commits.

**STOP_GATE:** `HUMAN_REVIEW_OF_PHASE6B_IMPLEMENTATION_PLAN`
