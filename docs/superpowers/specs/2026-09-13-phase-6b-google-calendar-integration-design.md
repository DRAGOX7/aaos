# Phase 6B — Gated Google Calendar CREATE_EVENT Integration

**Status:** architecture approved; specification review required before an
implementation plan is written.

**Frozen base:** `phase-06a-passed` at
`949cd1d4a53072b32e29cfb4d178718af09f1f6f`.

This specification defines the first real external-provider integration for
AAOS. It is intentionally limited to one Google Calendar write operation and
uses the frozen Phase 6A durable approval and execution core. It does not
authorize implementation, credentials, network access, or a live write.

## 1. Objective

Prove that AAOS can create one bounded, human-approved event on one trusted
dedicated Google Calendar while preserving the Phase 6A guarantees:

`PREPARE -> REQUEST_APPROVAL -> APPROVE -> CLAIM -> CONSUME -> durable EXECUTING
-> adapter outcome -> persistence/reconciliation`.

The provider integration must be least-authority, deterministic, auditable,
and conservative when Google's response is ambiguous. The human approves the
exact canonical event effect; neither the model nor provider response grants
authority.

## 2. Scope and non-goals

### In scope

- one action type: `CREATE_EVENT`;
- one trusted dedicated AAOS calendar;
- a closed canonical event DTO containing only `summary`, `start`, `end`,
  `timezone`, optional `description`, and optional `location`;
- a narrow client with only `get_event` and `insert_event` operations;
- fake-provider execution and reconciliation for deterministic acceptance;
- an explicitly disabled-by-default real Google client factory;
- one separately gated real create after all local gates pass.

### Out of scope

Phase 6B does not implement update, delete/cancel, attendees or invitation
responses, recurrence, reminders, conference links, attachments, extended
properties, arbitrary Calendar JSON or methods, multiple calendars, ACL or
calendar-list writes, settings changes, background synchronization, or any
GitHub/MCP/network capability beyond the single approved Google client.
There is no model or RAG execution in the Phase 6B evaluator. Phase 6B does
not create a new approval service, operation state machine, retry ledger,
dashboard, REPL, or multi-agent behavior.

## 3. Authority and trust model

Three independent fences must all pass:

1. **AAOS provider policy:** the action is exactly `CREATE_EVENT`, targets the
   configured dedicated calendar, uses the approved canonical fields and
   current trusted configuration fingerprint.
2. **Phase 6A core:** capability permission, immutable prepared action,
   human approval, atomic one-shot claim/consumption, durable `EXECUTING`,
   outcome persistence, and reconciliation all succeed.
3. **Google boundary:** the credential belongs to the expected dedicated
   service identity and is authorized only for the dedicated calendar and the
   exact OAuth scope.

Failure of any **pre-write** fence means zero external writes. Once the
provider call has begun, an ambiguous provider response is handled as
`UNKNOWN_OUTCOME` rather than retrospectively claiming that no write occurred.
Model text, user text,
retrieved documents, observations, provider responses, and error messages are
untrusted data. They cannot select a calendar, set credentials or scopes,
grant capability/approval, change an action, or add provider fields.

The trusted composition root supplies `GoogleCalendarConfig`, the canonical
DTO, the Phase 6A `Phase6ActionDefinition`, and the adapter. The adapter never
receives `ActionRecord`, `PreparedAction`, `ApprovalRecord`, a database
session, policy objects, model text, or credentials outside its explicitly
constructed provider client.

## 4. Phase ownership

### Phase 6A owns

`PreparedAction`, `ApprovalRecord`, `ApprovalConsumption`, `OperationRecord`,
approval identity and expiry, capability checks, atomic claim and consumption,
`CLAIMED -> EXECUTING` durable dispatch-start CAS, one operation/one execution
attempt, action and operation outcomes, unknown-outcome handling, read-only
reconciliation lifecycle, append-only events, and no automatic write retry.

The Phase 6A public integration surface is the existing typed preparation,
approval, claim, dispatch, outcome, and reconciliation API. Phase 6B must use
it instead of duplicating any lifecycle or approval record.

### Phase 6B owns

Calendar request validation/canonicalization, provider event identity,
trusted calendar configuration, provider client construction, Google request
projection, provider error mapping, live create precondition enforcement,
provider response projection, and exact semantic reconciliation. These are
provider-specific contracts and never replace Phase 6A authority.

## 5. Canonical contracts

The following types are closed, immutable value contracts. Unknown fields,
NUL characters, control payloads, non-finite values, and arbitrary mappings
are rejected before Phase 6A preparation.

### `CalendarCreateRequest`

Trusted application input with exactly:

- `summary: str`, required, Unicode-normalized, 1–200 characters;
- `start: datetime`, required, timezone-aware;
- `end: datetime`, required, timezone-aware;
- `timezone: str`, required IANA timezone identifier;
- `description: str | None`, optional, at most 2,000 characters;
- `location: str | None`, optional, at most 300 characters.

The request does not contain `calendar_id`, `provider_event_id`, credentials,
OAuth scopes, attendees, or arbitrary provider fields.

### `CanonicalCalendarEvent`

Frozen, trusted representation with the same six semantic fields after:

- Unicode normalization;
- deterministic omission representation for absent optionals;
- canonical RFC3339 UTC instants for `start` and `end`;
- preserved explicit IANA timezone identity;
- `start < end`, duration at most 24 hours;
- event start not in the past and no more than 365 days in the future;
- no floating/all-day/mixed-timezone values.

The canonical payload is byte-stable and is the exact payload approved,
executed, and used for reconciliation. Adapter code does not rewrite it.

### `GoogleCalendarConfig`

Trusted configuration, never request/model data, with exactly:

- `calendar_id`;
- `expected_service_identity`;
- `oauth_scope`, which must equal
  `https://www.googleapis.com/auth/calendar.events`;
- `credential_path` (a trusted local configuration reference, never persisted
  in action/event records);
- `provider_adapter_version`;
- `enabled: bool`.

The configured calendar is the only writable target. Configuration is
fingerprinted/versioned over all authority-bearing values except secret
contents. Drift from the approved fingerprint fails closed before a write.

### `ObservedGoogleEvent`

Closed provider-result projection with only `provider_event_id`, `summary`,
canonical `start`, canonical `end`, `timezone`, optional `description`, and
optional `location`. Provider metadata (`etag`, links, timestamps, creator,
organizer, kind, sequence, and unknown fields) is discarded for durable
evidence and logs.

### Provider outcomes

The adapter returns a closed `AdapterExecutionOutcome`/reconciliation outcome
compatible with Phase 6A. Internal provider kinds are:

`AUTHENTICATION_REJECTED`, `PERMISSION_REJECTED`, `INVALID_REQUEST`,
`NOT_FOUND`, `ALREADY_EXISTS`, `RATE_LIMITED`, `SERVER_ERROR`, `TIMEOUT`,
`TRANSPORT_ERROR`, and `MALFORMED_RESPONSE`.

Only evidence proving that the request was rejected before acceptance and
cannot later create the event may map to Phase 6A `CONFIRMED_FAILURE`.
Timeouts, connection resets, ambiguous 5xx responses, lost responses, and
malformed responses after dispatch map to `UNKNOWN_OUTCOME`. A stale
precondition maps to Phase 6A action `INVALIDATED` and operation
`NOT_DISPATCHED`; it is not an execution success or failure.

## 6. Canonicalization and provider identity

Canonicalization runs before `prepare_action`. The canonical event payload is
serialized with sorted keys, UTF-8, no insignificant whitespace, explicit
null/absence semantics, and the repository's stable hashing convention.
The canonical payload hash and all provider-policy/configuration fingerprints
are included in the Phase 6A approval binding.

`provider_event_id` is generated by trusted AAOS code from a fixed AAOS
namespace and a cryptographic digest of the immutable Phase 6A
`operation_id`, encoded in a Google-safe bounded identifier. The operation ID
is persisted by Phase 6A preparation; the provider ID is a pure, deterministic
projection of that immutable identity, and the approved binding includes the
fixed derivation/version so the projection cannot be replaced. It is known
before execution, is not supplied by a model or user, and is never regenerated.
Reconciliation uses the same projection from the same operation ID.

Changing output filenames, provider response metadata, or request wording
cannot change this identity. A new external create requires a newly prepared
action, operation ID, provider event ID, and human approval.

## 7. Narrow Google client boundary

The provider client protocol exposes only:

```text
get_event(calendar_id: str, provider_event_id: str) -> ObservedGoogleEvent | NOT_FOUND
insert_event(calendar_id: str, event: CanonicalCalendarEvent,
             provider_event_id: str) -> ObservedGoogleEvent | ProviderError
```

The actual Python signature may use closed request/result objects, but it must
retain this authority surface. There is no `request`, `execute_api`, raw
service object, arbitrary method/path/body, or Google-service back-reference.
The real client wrapper maps only these two operations to the official
Calendar API. Provider calls have finite deadlines; the implementation plan
must bind the SDK calls to a finite per-call timeout (5 seconds for `get_event`
and 10 seconds for `insert_event`) and a read-only retry budget of at most two
total `get_event` attempts with bounded backoff. No write retry exists.

The fake client implements the same protocol in memory, exposes deterministic
inspection counters, and never performs network or credential operations.

## 8. Execution flow and side-effect ordering

The only valid flow is:

1. Validate and canonicalize `CalendarCreateRequest`.
2. Prepare a Phase 6A action binding the canonical payload, provider target,
   configuration fingerprint, schema/policy versions, provider-ID derivation
   version, and remote precondition (`provider_event_id` absent). Phase 6A
   allocates and persists the trusted `operation_id`; Phase 6B then derives the
   provider ID as the immutable projection of that identity before dispatch.
4. Request approval and obtain human approval through the Phase 6A context.
5. Claim and consume approval atomically.
6. Let Phase 6A commit durable `CLAIMED -> EXECUTING` and its dispatch-start
   evidence before the adapter is invoked.
7. Outside the database transaction, construct the least-authority executable
   projection and call `GoogleCalendarAdapter.execute()`.
8. At the adapter side-effect boundary, revalidate current trusted config and
   the remote precondition, then perform at most one `events.insert`.
9. Project the typed provider result and let Phase 6A persist the outcome with
   expected-state CAS.
10. If the outcome is unknown, invoke only Phase 6A's read-only reconciliation;
    never insert again.

No Google write can occur before durable Phase 6A `EXECUTING` is committed.
No model step is inserted after a successful RAG or provider operation.

## 9. Live precondition and duplicate protection

Immediately before the side effect, the adapter performs:

```text
events.get(trusted_calendar_id, provider_event_id)
```

The approved precondition is **ABSENT**. If the event is definitively absent,
one insert may proceed. If it exists, the adapter makes zero inserts and
returns `PRECONDITION_STALE` (Phase 6A `INVALIDATED`/`NOT_DISPATCHED`).
The preliminary read is not a sufficient fence: the adapter's execute path
must recheck the precondition in the same provider-side critical operation
used to decide whether to issue the single insert.

The deterministic provider ID is the duplicate fence. If another creator wins
the same-ID race, AAOS does not search by title, choose a different ID, or
retry. Any provider response that cannot establish whether the insert was
accepted is unknown and requires reconciliation.

## 10. Error and unknown-outcome semantics

`CONFIRMED_FAILURE` requires authoritative provider evidence that the
invocation failed before acceptance and cannot later succeed. It is not a
synonym for timeout or transport error.

The following are always conservative: timeout, connection reset, ambiguous
server error, lost response, malformed response after dispatch, and process
crash after dispatch. They produce `UNKNOWN_OUTCOME`, preserve the one-shot
operation identity, and permit only read-only reconciliation. A missing event
does not authorize a retry; it remains unknown unless the provider supplies a
separate authoritative non-acceptance proof.

Reconciliation calls only `get_event` and never mutates Google. It compares
the exact approved semantics: calendar target, provider ID, summary, start
instant, end instant, timezone, description, and location. Exact match yields
`CONFIRMED_SUCCESS`; an existing event with any semantic mismatch remains
unknown as a reconciliation conflict; not-found remains unknown. Google-
generated metadata is ignored.

## 11. Crash and recovery behavior

- Crash before the adapter call after durable `EXECUTING`: recover as
  `UNKNOWN_OUTCOME`; do not call `execute` again.
- Crash after Google accepts the insert but before outcome persistence:
  `UNKNOWN_OUTCOME`, followed by read-only reconciliation by the same
  provider ID.
- Crash after a definite provider rejection but before persistence: process
  memory is not durable proof; use unknown/reconciliation unless the provider
  rejection is itself durably recorded.
- A late adapter result that loses Phase 6A CAS is a sanitized audit event,
  never a replacement for the persisted state.

There is no automatic execution retry, no background sync, and no second
approval lifecycle in Phase 6B.

## 12. Credentials, configuration, and privacy

The real client factory is explicit and disabled by default. It returns
`REAL_ADAPTER_UNAVAILABLE` unless integration is enabled, the trusted
credential path exists and parses, the service identity matches, the exact
OAuth scope matches, and the dedicated calendar is configured. There is no
interactive login and no fake fallback after real-client failure.

The dedicated AAOS service identity is shared onto a normally owned dedicated
calendar; it does not own or create that calendar. Its provider authority is
limited to that calendar. The only requested OAuth scope is
`https://www.googleapis.com/auth/calendar.events`.

Credentials are read only by the trusted client factory and passed only to the
narrow provider client. They never enter `PreparedAction`, `ActionRecord`,
`ExecutableAction`, approval/event records, normal SQLite rows, frontend
responses, model/RAG context, Git, or logs.

Operational logs may contain bounded operation/provider IDs, action type,
configuration fingerprint, closed provider error kind, state, and timestamps.
They must not contain credential files, private keys, OAuth tokens or headers,
raw Google bodies/responses, exception tracebacks, full descriptions or
locations, or arbitrary event JSON.

Importing any Phase 6B module performs zero credential reads, provider-client
construction, OAuth negotiation, sockets, database mutation, subprocess
starts, model loads, or filesystem mutation. Real construction is available
only through explicit trusted composition.

## 13. Testing architecture

### Layer A — pure contract tests

No database, SDK, credentials, or network. Test closed fields, Unicode and
control rejection, bounds, timezone/DST behavior, canonical bytes, RFC3339
normalization, horizon/duration/past checks, deterministic provider ID, and
unknown-field rejection.

### Layer B — provider contract tests

Run `GoogleCalendarAdapter` against `FakeGoogleCalendarClient`. Prove:

- absent event permits exactly one insert;
- existing event permits zero inserts;
- remote state changing after an advisory check is caught at execute;
- accepted-then-timeout is unknown;
- exact reconciliation is success;
- semantic mismatch and not-found remain unknown;
- only authoritative pre-dispatch rejection is confirmed failure;
- no write retry occurs for any ambiguous outcome.

### Layer C — Phase 6A integration tests

Drive `CalendarCreateRequest -> Phase 6B canonicalization -> Phase 6A
prepare/approval/claim/dispatch -> fake provider`. Prove no approval,
capability denial, consumed approval, configuration drift, wrong target,
stale precondition, duplicate provider ID, and unknown outcome all yield zero
unsafe writes. Prove durable `EXECUTING` precedes the first provider call and
the adapter sees only the least-authority projection.

### Layer D — security, privacy, and import safety

Adversarial text must not override calendar, identity, scope, credentials,
provider ID, attendees, conference data, sendUpdates, recurrence, reminders,
arbitrary JSON, or provider methods. Tests assert no credential/raw-response/
traceback leakage, no fake fallback, no generic Google authority, and no
Phase 6A regression.

All layers preserve the frozen Phase 6A tests and relevant Phase 4/5 tests.

## 14. Deterministic Phase 6B evaluation

Before implementation execution, freeze one local, deterministic, approximately
30-case matrix. No model, RAG, live Google service, or credential is used.
The exact IDs and membership are frozen before any evaluation run and are not
edited in response to failures.

| Domain | Cases | Required coverage |
| --- | ---: | --- |
| Validation/canonicalization | 6 | bounds, timezone, normalization, invalid fields |
| Trusted configuration/authority | 5 | disabled/missing/malformed config, identity/scope/calendar drift |
| Precondition/duplicate prevention | 5 | absent, present, race, stale binding, zero-write behavior |
| Provider error taxonomy | 5 | definite rejection, timeout, transport, malformed, rate/server |
| Unknown/reconciliation | 5 | exact match, mismatch, not-found, crash/late result, no retry |
| Privacy/import/security | 4 | credential/raw response isolation, import safety, authority injection |
| **Total** | **30** | **all deterministic fake-provider cases** |

Acceptance gates are layer-specific: all tests pass; write retries equal zero;
unauthorized writes, wrong-calendar writes, credential exposure, and raw
provider-response exposure equal zero; model calls and RAG executions equal
zero. These gates are independent of any historical Phase 5 calibration
metric.

## 15. Separately gated real acceptance

Only after Tasks 1–6 are implemented, reviewed, and frozen may a separate
human gate authorize one real `CREATE_EVENT`. The acceptance request is a
unique future event of approximately 15 minutes in the configured timezone,
with bounded summary, description marker, and optional bounded location; it
has no attendees, recurrence, reminders, conference link, or attachment.

The event must travel through the full Phase 6B and Phase 6A path. A direct
debug `events.insert` is not acceptance. If the write is ambiguous, perform
the read-only reconciliation once: exact semantic match is confirmed success;
otherwise remain unknown and stop. AAOS has no delete authority for cleanup;
the event may be removed manually later in Google Calendar.

## 16. Seven-task decomposition

This decomposition is the implementation boundary after this specification
is approved. Each task gets its own tests-first/red, implementation, green,
review, and commit gate; no task may add a later task's authority.

1. **Closed contracts, validation, canonicalization, and provider ID.** Add
   `src/aaos/integrations/google_calendar/contracts.py` and
   `canonicalization.py` plus pure tests. No SDK, credentials, network, or
   database.
2. **Narrow client, fake transport, projections, and errors.** Add
   `client.py` and `errors.py` with `FakeGoogleCalendarClient`, closed
   `ObservedGoogleEvent`, and provider taxonomy. No real credentials.
3. **Phase 6A adapter integration.** Add `adapter.py` and a minimal trusted
   composition seam. Use existing `ExternalAdapter`, `ExecutableAction`,
   `AdapterExecutionOutcome`, dispatch, outcome, and reconciliation APIs.
   Fake provider only; prove precondition-at-side-effect and no retry.
4. **Trusted real-client factory.** Add explicit configuration validation and
   real client construction behind enablement, exact scope, identity and
   dedicated-calendar checks. No real write.
5. **Security/privacy/import hardening.** Add adversarial and side-effect
   tests, credential/raw-response isolation tests, fake-fallback prohibition,
   and Phase 6A regression coverage.
6. **Frozen deterministic Phase 6B evaluation.** Freeze the 30-case fake-only
   matrix and acceptance harness before execution. No model, RAG, live Google,
   or heldout-style adaptive tuning.
7. **One real Google acceptance create.** A separate explicit gate authorizes
   one end-to-end event, followed by read-only reconciliation and final
   evidence. No delete, retry, or additional provider method.

## 17. Acceptance definition

Phase 6B passes only when evidence proves all of the following:

1. only `CREATE_EVENT` is writable;
2. exactly one trusted dedicated calendar is writable;
3. only the six canonical fields reach Google;
4. provider event ID is predetermined, immutable, and operation-bound;
5. both capability permission and human approval are required;
6. durable Phase 6A `EXECUTING` precedes provider write;
7. `events.insert` occurs at most once per operation;
8. the precondition and deterministic ID prevent duplicates;
9. ambiguous writes become `UNKNOWN_OUTCOME`, never an automatic retry;
10. reconciliation is read-only and exact semantic match is required;
11. credentials remain inside the provider boundary;
12. imports are side-effect free;
13. all deterministic local gates and frozen Phase 6A regressions pass;
14. one real Google create succeeds through the full AAOS path.

The final tag `phase-06b-passed` may be created only after a human-reviewed
acceptance report proves every gate. This specification alone does not create
that tag or authorize any real provider call.

## 18. Explicit security invariants

- `UNAUTHORIZED_WRITE_COUNT = 0`.
- `WRONG_CALENDAR_WRITE_COUNT = 0`.
- `WRITE_RETRY_COUNT = 0`.
- `CREDENTIAL_EXPOSURE_COUNT = 0`.
- `RAW_PROVIDER_RESPONSE_EXPOSURE_COUNT = 0`.
- `MODEL_CALL_COUNT = 0` and `RAG_EXECUTION_COUNT = 0` in Phase 6B local
  evaluation.
- no model, observation, retrieved text, or provider response can grant
  capability, approval, target, scope, identity, or provider method.
- no adapter receives `ActionRecord` or any database/policy authority.
- no Phase 6B import performs I/O or initialization.
- Phase 6A remains the sole approval, claim, dispatch, outcome, and
  reconciliation authority.

## 19. Review checklist and stop gate

Before an implementation plan is written, review this spec for placeholders,
contradictions, scope expansion beyond `CREATE_EVENT`, duplicate Phase 6A
behavior, hidden write retry, generic Google authority, credential leakage,
reconciliation mutation, evaluation ambiguity, and task decomposition gaps.

An independent architecture review must confirm least authority, durable
dispatch ordering, duplicate prevention, conservative unknown outcomes,
read-only reconciliation, credential isolation, import safety, testability,
and the seven-task boundary. Any Critical or Important documentation finding
must be fixed in a new scoped documentation commit and reviewed again.

**STOP_GATE:** `HUMAN_REVIEW_OF_PHASE6B_DESIGN_SPEC`
