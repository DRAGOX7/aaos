# Phase 7 — Dashboard and Operational Visibility

Status: **HUMAN_APPROVED — PRODUCTION IMPLEMENTATION AUTHORIZED SLICE-BY-SLICE**

Design sections 1–7 were human-approved in chat on 2026-09-16. This exact
written specification was explicitly human-approved at commit `095979f` on
2026-09-16. Production implementation may proceed only through reviewed
slice-specific implementation plans and their acceptance gates.

Baseline: `phase-06b-passed` at
`40ff9d3ef6e0e3010982dabfb37cb597aceeb6ed`.

Phase 6C is optional and is not pursued by this Phase 7 design. Existing
Phase 5 unresolved governance/acceptance qualifications are not rewritten or
silently converted into passed evidence; Phase 7 consumers must preserve the
established trust, sanitization, event, and approval boundaries.

## 1. Objective

Phase 7 turns the existing AAOS backend into a usable local-first web product
without moving business authority, policy, credentials, or external-write
capability into the browser.

The product is a local-first, single-user application in Phase 7, designed so
a future hosted/authenticated deployment can be added without redesigning the
browser contracts. The frontend is desktop-first and responsive.

The fundamental authority rule is:

```text
Frontend = presentation + user intent
FastAPI = trusted browser boundary
Existing AAOS subsystems = domain authority
```

The browser may request and display actions, but it may never manufacture
authoritative AAOS state.

## 2. Authoritative context

Normative predecessors include:

- `docs/PHASES.md`, Phase 7 purpose and acceptance boundaries;
- `docs/GOVERNANCE_LEDGER.md`, authoritative phase/decision evidence;
- `docs/ARCHITECTURE.md` and `docs/PROJECT_SPEC.md`, trusted-boundary rules;
- Phase 4 RAG contracts and accepted citation-grounded answer behavior;
- Phase 5 agent contracts, safe events, task lifecycle, and least-authority
  orchestration boundaries;
- Phase 6A durable approval/execution architecture;
- Phase 6B frozen Google Calendar integration and real acceptance evidence;
- `src/aaos/main.py`, current FastAPI foundation;
- `src/aaos/persistence/academic_models.py`, authoritative academic records;
- `src/aaos/agent/events.py`, safe backend agent-event contract;
- `src/aaos/approval/events.py`, durable approval-event contract;
- `src/aaos/inference/telemetry.py`, safe local inference diagnostics.

The current FastAPI application exposes process health but no general
browser-facing product API. The frontend does not yet exist. Phase 7 therefore
adds a new browser/API boundary and a new frontend without redesigning the
underlying academic, RAG, agent, approval, or Calendar systems.

## 3. Scope and non-goals

### 3.1 In scope

- a versioned browser-facing FastAPI API under `/api/v1`;
- explicit browser-safe Pydantic projection contracts;
- stable browser-safe error contracts and request correlation identifiers;
- OpenAPI as the source for generated TypeScript types;
- a Next.js + TypeScript frontend;
- Tailwind CSS and shadcn/ui primitives with custom AAOS components;
- TanStack Query for backend-owned browser state;
- native `EventSource` for SSE;
- desktop-first responsive layout;
- Overview, Courses, Assignments, Documents, Ask AAOS, Tasks, Approvals,
  Activity, System Health, and Settings navigation areas;
- course, assignment, and document read views;
- citation-grounded RAG UI with validated final answers only;
- task viewing and later task initiation from user intent;
- approval/rejection UI delegating to the Phase 6 authority path;
- a durable browser-safe `ActivityRecord` projection;
- SSE live updates, durable replay, and reconnect/refetch recovery;
- side-effect-free operational system health;
- production build, security, accessibility, responsive, and recovery gates.

### 3.2 Out of scope

Phase 7 does not require:

- cloud deployment;
- multi-user authentication;
- a mobile native application;
- WebSockets;
- Phase 6C GitHub integration;
- GitHub writes;
- MCP as a prerequisite for the dashboard;
- full dark-mode delivery;
- study-time analytics or other unimplemented analytics;
- push notifications;
- a new distributed job framework;
- a redesign of Phase 4 RAG, Phase 5 authority, or Phase 6 approval logic;
- direct browser access to Moodle, Google Calendar, SQLite, model runtimes,
  credentials, local storage paths, or provider APIs.

The approved UI mockup is a visual reference only. It may guide layout,
spacing, icon treatment, pastel surfaces, and information hierarchy, but it
must not add features AAOS does not actually implement.

## 4. Deployment and trust model

### 4.1 Local-first, future-deployable

Phase 7 is single-user and local-first. Next.js and FastAPI run locally and
AAOS continues to use its existing local persistence/runtime systems.

The design must avoid assumptions that prevent a future hosted deployment:

- no business rule may depend on "localhost means trusted";
- future authentication must be insertable at the FastAPI boundary;
- API contracts are versioned and transport-neutral;
- browser-safe contracts never depend on direct database or filesystem access;
- credentials never become `NEXT_PUBLIC_*` values or browser responses.

### 4.2 Next.js proxy

The browser talks to Next.js, and Next.js proxies AAOS API/SSE traffic to
FastAPI.

```text
Browser -> Next.js -> FastAPI -> AAOS internals
```

Next.js has no execution or policy authority. The proxy exists for a clean
same-origin product surface, local development, and future deployment
flexibility.

FastAPI should not require permissive wildcard CORS for the normal product
path. Local backend binding and deployment configuration remain explicit and
non-secret.

### 4.3 External writes

The frontend never calls Google Calendar or another external provider.

```text
Approve click
  -> HTTP decision request
  -> FastAPI
  -> existing Phase 6 approval authority
  -> validated transition / claim / execution / reconciliation
  -> provider
```

React disabled states are convenience only; backend transition, binding,
expiry, capability, one-shot, idempotency, and reconciliation rules remain
authoritative.

## 5. Frontend product areas

The navigation is fixed to the real AAOS capabilities:

```text
Overview
Courses
Assignments
Documents
Ask AAOS
Tasks
Approvals
Activity
System Health
Settings
```

### 5.1 Overview

Overview answers: "What needs my attention right now?"

It may compose compact views of:

- active courses;
- upcoming assignments;
- pending approvals;
- active tasks;
- a compact Ask AAOS entry point;
- recent safe activity;
- document/RAG readiness;
- operational system health.

Overview is not a second implementation of each workflow. Detailed actions
navigate to the authoritative feature page. There is no dedicated Overview
backend aggregate required initially; the frontend may compose existing query
results unless measured performance later justifies a dedicated projection.

### 5.2 Courses

Courses expose safe academic information and are the first vertical
implementation proof.

A course detail may link to its assignments, documents, and an Ask AAOS query
scope. Moodle/internal provenance identifiers remain backend-only unless a
later reviewed feature demonstrates a real need.

### 5.3 Assignments

Assignments are authoritative synchronized academic records. UI grouping
such as "Due soon", "This week", "Later", and "No due date" is presentation
logic only.

A Moodle assignment is not automatically an AAOS task. Phase 7 must not invent
completion state that does not exist in an authoritative backend model.

### 5.4 Documents

Documents expose browser-safe document metadata, ingestion/extraction status,
and RAG index/readiness state. They never expose local blob paths, storage
keys, database paths, or other filesystem internals.

Citation navigation should resolve from a validated Ask AAOS citation to the
corresponding document/page or source location supported by the existing RAG
provenance.

### 5.5 Ask AAOS

Ask AAOS is the browser frontend for the existing validated RAG pipeline.

The user may choose global academic scope or a specific course scope where the
existing Phase 4 contracts support it.

The browser may receive safe progress states such as:

```text
RETRIEVING
CHECKING_EVIDENCE
GENERATING
VALIDATING
COMPLETED
```

Unvalidated generated text is never browser-visible. Only the final validated
grounded response may be rendered. Insufficient evidence is a valid RAG
result, not an HTTP/server failure.

### 5.6 Tasks

Tasks expose browser-safe Phase 5 task state, lifecycle, timestamps, safe
result category, and associated safe activity.

Later in Phase 7, the browser may initiate a task using user intent. The
request may not select tool identity, privileged arguments, capability
permission, runtime policy, approval bypass, or provider authority.

### 5.7 Approvals

Approvals expose a purpose-built `ApprovalView`, not raw Phase 6 persistence
records or event payloads. The UI must show enough safe information for a
meaningful human decision: proposed action kind, integration, safe target
summary, current status, expiry, and operation status/outcome where relevant.

Approve/reject requests delegate to the existing Phase 6 authority path.
The frontend never writes approval state directly and does not optimistically
claim a critical decision succeeded before backend confirmation.

### 5.8 Activity

Activity is a browser-safe operational history, not an audit ledger. It is a
projection over trusted domain events/state and is subordinate to the
underlying authoritative records.

### 5.9 System Health

System Health reports side-effect-free operational status of AAOS subsystems.
It does not perform syncs, model loads, index rebuilds, Calendar writes, or
other state-changing probes.

### 5.10 Settings

Settings remains intentionally narrow. It may expose safe UI/local-runtime
preferences but never raw credentials, OAuth tokens, Moodle cookies, database
paths, authorization headers, or provider secrets.

## 6. API versioning and browser-safe contracts

### 6.1 Namespace

All product APIs live under `/api/v1`.

Canonical Phase 7 paths are:

```text
GET  /health

GET  /api/v1/courses
GET  /api/v1/courses/{course_id}
GET  /api/v1/assignments
GET  /api/v1/documents

POST /api/v1/rag/queries

GET  /api/v1/tasks
POST /api/v1/tasks

GET  /api/v1/approvals
POST /api/v1/approvals/{approval_id}/approve
POST /api/v1/approvals/{approval_id}/reject

GET  /api/v1/activity
GET  /api/v1/events
GET  /api/v1/system-health
```

`/health` remains the process-liveness endpoint. It is not the same contract as
`/api/v1/system-health`.

### 6.2 Projection rule

Internal AAOS models are not browser API models. ORM rows, internal events,
provider records, and domain aggregates must not be returned by automatic
serialization.

Every browser response is an explicit Pydantic projection containing only the
minimum fields required by the UI.

For the first course list contract:

```text
CourseSummary
- id
- title
- code
- description
- updated_at
```

The initial browser contract deliberately excludes `source_system` and
`source_id`.

### 6.3 Contract source of truth

Pydantic/FastAPI OpenAPI is the contract source of truth.

```text
Pydantic -> OpenAPI -> generated TypeScript types
```

Frontend API functions and TanStack Query hooks are hand-written AAOS code.
Generated types must be checked for drift in the verification gate.

### 6.4 Time

All API timestamps are timezone-aware UTC ISO-8601 values. The frontend may
localize them for display but never changes their authoritative meaning.

### 6.5 Deterministic list ordering

Every list endpoint has explicit deterministic ordering.

Initial rules:

- courses: active only; deterministic code/title ordering with a stable ID
  tie-breaker;
- assignments: due time ascending, null due times last, stable ID tie-breaker;
- documents: deterministic title/ID or contract-defined recency ordering;
- activity: newest-first for history pages with monotonic ID as the cursor.

Courses and assignments do not require pagination initially. Activity is
bounded/paginated. Documents may add pagination only when needed.

### 6.6 Stable error contract

Browser-safe errors use one stable shape:

```json
{
  "error": {
    "code": "COURSE_NOT_FOUND",
    "message": "The requested course could not be found.",
    "request_id": "opaque-safe-id"
  }
}
```

The browser never receives raw exception text, SQL, filesystem paths,
tracebacks, credentials, tokens, authorization headers, or provider error
payloads.

Stable codes are domain-specific and closed. Representative codes include:

```text
INVALID_REQUEST
COURSE_NOT_FOUND
DOCUMENT_NOT_FOUND
RAG_UNAVAILABLE
TASK_NOT_FOUND
APPROVAL_NOT_FOUND
APPROVAL_EXPIRED
APPROVAL_ALREADY_DECIDED
SYSTEM_UNAVAILABLE
INTERNAL_ERROR
```

`RAG_INSUFFICIENT_EVIDENCE` is represented by the RAG result contract rather
than by an HTTP 5xx failure.

## 7. First vertical slice: Courses

The first production slice proves the complete browser architecture with a
read-only, low-risk resource.

```text
CourseORM
  -> AcademicReadService
  -> CourseSummary
  -> GET /api/v1/courses
  -> OpenAPI-generated TS type
  -> hand-written API function
  -> TanStack Query
  -> real AAOS Course UI
```

The route layer does not query SQLAlchemy directly. A focused read/projection
service owns academic read queries and conversion to browser-safe contracts.

The initial contract must prove:

- active courses only;
- inactive courses excluded;
- deterministic ordering;
- nullable code/description support;
- UTC timestamp serialization;
- exact allowed response field set;
- no `source_system`;
- no `source_id`;
- no database writes;
- existing `/health` remains unchanged.

## 8. RAG contract

A browser RAG request describes a question and approved query scope, for
example:

```json
{
  "question": "Explain self-attention.",
  "scope": {
    "type": "course",
    "course_id": 12
  }
}
```

The Phase 7 adapter translates this request into the existing trusted Phase 4
RAG contracts. The browser cannot supply internal retrieval knobs, model
prompts, validation bypasses, or provider identity.

The browser result distinguishes:

- validated answered result with citations;
- insufficient-evidence result;
- operational/API failure.

Unvalidated generation is never included in an HTTP response, ActivityRecord,
SSE progress payload, frontend cache, or browser log.

## 9. Task and approval mutation semantics

### 9.1 Task creation

`POST /api/v1/tasks` describes user intent. It does not expose tool selection,
permission, runtime policy, approval bypass, or trusted provider arguments.

The trusted agent/orchestrator owns those decisions.

### 9.2 Approval decisions

Approve/reject endpoints delegate into the Phase 6 approval service. Browser
payloads may identify the approval and human decision but do not set arbitrary
state or directly update persistence.

Mutations must remain correct under duplicate clicks, request retries,
connection loss, and browser reconnect. UI button disabling is not a safety
control.

An uncertain HTTP/network result is not automatically "failure". The client
refetches authoritative state to determine whether the decision was accepted,
remained pending, expired, was invalidated, or reached another trusted state.

Automatic mutation retries are disabled unless the underlying contract is
explicitly proven idempotent and the retry semantics are part of that route's
reviewed design.

## 10. Activity projection

### 10.1 Purpose

`ActivityRecord` is a durable user-facing operational projection. It is not a
replacement for Phase 5 events, Phase 6 audit evidence, task state, approval
state, or provider outcome records.

If Activity disagrees with an authoritative domain record, the domain record
wins.

### 10.2 Record shape

Conceptually the record contains:

```text
id                  monotonic database identifier
schema_version      phase7-activity-v1
occurred_at         timezone-aware UTC
category            closed enum
event_code          closed enum
status              closed enum / optional by event code
resource_type       closed enum / optional by event code
resource_id         bounded opaque identifier / optional
context             closed event-code-specific typed union
```

Categories are limited to:

```text
ACADEMIC
DOCUMENT
RAG
TASK
APPROVAL
INTEGRATION
SYSTEM
```

`context` is never a free-form dictionary. Each event code has an exact typed
context or no context. Arbitrary metadata is rejected.

Activity must never persist raw prompts, model output, tool-argument payloads,
raw exception text, credentials, authorization data, filesystem paths,
approval binding hashes, or arbitrary provider payloads.

### 10.3 Retention and pagination

Phase 7 initially retains browser-safe activity records rather than inventing
a retention policy before real volume is known.

The API is bounded. Initial history access uses a default limit of 50, a hard
maximum of 200, and an ID cursor such as `before_id` for older records.

## 11. SSE delivery and recovery

### 11.1 Endpoint

`GET /api/v1/events` uses `text/event-stream` and is reachable through the
Next.js proxy without buffering or caching.

### 11.2 Durable change events

Durable user-facing change notices correspond to stored `ActivityRecord`
entries. Their SSE `id:` is the monotonic ActivityRecord ID.

A durable event should normally carry a minimal invalidation message rather
than a full authoritative resource object, for example:

```text
id: 1042
event: activity
data: {"schema_version":"phase7-browser-event-v1","activity_id":1042}
```

The frontend invalidates/refetches the relevant TanStack Query resource.

### 11.3 Ephemeral progress

Short-lived progress such as RAG retrieval/generation/validation may be sent as
`progress` SSE events. Ephemeral events are not durable ActivityRecords and do
not receive replay IDs. They contain only bounded safe progress state and a
safe correlation identifier; they never contain generated answer text.

Because ephemeral events do not set an SSE ID, they do not advance the
client's durable replay cursor.

### 11.4 Reconnect

The browser loads authoritative HTTP state and recent activity before opening
the stream.

On disconnect, native EventSource reconnects and sends the last durable event
ID. The backend replays durable ActivityRecords after that ID, then resumes
live delivery.

If the server cannot safely replay the requested range, it sends/returns a
recovery condition that causes the client to perform a full authoritative
refetch before resuming live mode. Silent event loss is not acceptable.

After a long-closed dashboard reopens, current domain state is reconstructed
from HTTP resources; SSE is not treated as the database.

## 12. Operational system health

### 12.1 Status model

Dashboard health states are:

```text
HEALTHY
DEGRADED
UNAVAILABLE
UNKNOWN
```

The initial component set is:

```text
DATABASE
ACADEMIC_DATA
ACADEMIC_SYNC
DOCUMENT_PIPELINE
RAG_INDEX
INFERENCE
GOOGLE_CALENDAR
```

The response may expose only browser-safe fields such as component name,
status, checked time, safe detail code, and last-success time where available.
It never includes raw exceptions or credentials.

### 12.2 Side-effect-free rule

Health probes must not trigger Moodle sync, document ingestion, RAG rebuild,
expensive model load solely for the check, Calendar/provider writes, database
mutation, or other state-changing work.

### 12.3 Overall aggregation

`DATABASE` is the critical persisted-state component.

- overall `UNAVAILABLE` if `DATABASE` is `UNAVAILABLE`;
- overall `DEGRADED` if `DATABASE` is `DEGRADED`, or any enabled non-critical
  component is `DEGRADED`/`UNAVAILABLE`, or a required component is `UNKNOWN`;
- overall `HEALTHY` only when the database and all enabled required components
  are `HEALTHY`;
- overall `UNKNOWN` only when the health aggregator cannot safely establish
  critical state at all.

The backend computes the overall status. React only renders it.

## 13. Frontend architecture

### 13.1 Stack

- Next.js;
- TypeScript;
- Tailwind CSS;
- shadcn/ui primitives;
- Lucide icon family;
- TanStack Query;
- native EventSource.

shadcn/ui is implementation plumbing, not the AAOS visual identity. Its source
components may be customized. AAOS-specific layout, cards, feature panels,
status components, and visual language remain custom.

### 13.2 State ownership

Backend-owned state uses TanStack Query:

```text
courses
assignments
documents
RAG results/tasks
agent tasks
approvals
activity history
system health
```

SSE invalidates or updates the relevant query after trusted backend changes.

Local React state is limited to presentation concerns such as sidebar state,
open dialogs, selected tab, filters, and transient form state.

### 13.3 Component layers

Low-level primitives:

```text
Button
Dialog
Sheet
DropdownMenu
Tooltip
Tabs
Select
Input
Textarea
Badge
Skeleton
Table primitives
```

Custom AAOS design components include:

```text
AppSidebar
PageHeader
MetricCard
StatusBadge
EmptyState
ErrorState
LoadingPanel
SectionCard
ActivityItem
HealthStatusCard
ApprovalCard
CitationCard
CourseCard
AssignmentRow
```

Feature components include:

```text
CourseOverview
AssignmentList
DocumentBrowser
RagAnswerPanel
TaskTimeline
ApprovalDecisionPanel
ActivityFeed
SystemHealthGrid
```

### 13.4 Responsive behavior

Desktop is the primary experience. Representative acceptance widths are
approximately 1440, 1280, 768, and 390 CSS pixels.

- desktop: full sidebar, multi-column dashboard, dense data views;
- tablet: collapsible sidebar, reduced padding, fewer columns;
- mobile: Sheet/drawer navigation, stacked cards, compact/list table
  alternatives, reachable critical actions, no unintended horizontal overflow.

### 13.5 Visual language

Use the approved AAOS mockup style only as a visual target:

- soft light background;
- rounded surfaces;
- subtle depth/shadows;
- clean academic SaaS hierarchy;
- restrained pastel accents;
- consistent spacing/typography;
- one coherent icon family;
- calm, trustworthy motion.

Do not copy fake analytics, schedules, or other unsupported widgets from a
reference image.

### 13.6 Theme

Light theme is the Phase 7 acceptance target. Tokens should avoid making a
future dark theme unnecessarily difficult, but dark mode is not a Phase 7
blocking requirement.

## 14. UI state behavior

Every major data region explicitly supports:

```text
loading
empty
success
error
reconnecting where relevant
```

Loading uses structural skeletons where appropriate. Empty state is not the
same as backend failure. Errors should remain local to the affected panel when
other dashboard data is still valid.

Status presentation is deterministic and never color-only. Backend enums map
explicitly to AAOS visual states; the frontend does not parse status strings
heuristically.

Accessibility requirements include keyboard navigation, visible focus,
semantic controls, labels, dialog focus management, adequate contrast,
status meaning beyond color, and usable zoom/responsive behavior.

## 15. Retry and uncertain-outcome behavior

Safe GET requests may use limited automatic retry.

Mutations such as task creation and approval/rejection do not blindly retry
on transport uncertainty. The client refetches authoritative state first.

This preserves the Phase 6 distinction between a failed request and an
unknown external outcome. The frontend must never claim failure or success
solely from a dropped HTTP response when the backend state can be reconciled.

RAG requests are read/compute operations rather than external writes. If a RAG
request is interrupted before a validated result reaches the browser, no
partial model text is shown and the user may retry. Phase 7 does not introduce
a durable distributed RAG job system unless implementation evidence later
shows it is necessary and separately reviewed.

## 16. Security invariants

The following are hard Phase 7 boundaries:

- browser responses contain zero credentials, Moodle cookies, OAuth tokens,
  provider secrets, authorization headers, private environment values,
  database paths, or local filesystem secrets;
- no files under `data/auth/` or `data/credentials/` are exposed or bundled;
- no raw ORM objects are serialized to the browser;
- no raw Phase 5 AgentEvent is streamed as the Phase 7 public contract;
- no raw Phase 6 approval event/persistence payload is streamed as the Phase 7
  public contract;
- no raw exception text reaches the browser;
- no unvalidated RAG generation reaches the browser;
- no frontend code owns approval validity, capability permission, provider
  execution, or reconciliation;
- no health endpoint performs an external write or mutating probe;
- no automatic retry may duplicate an external write;
- Next.js proxying does not grant authority.

## 17. Testing and acceptance

### 17.1 Backend contract tests

Every route tests exact public fields and negative leakage. Course acceptance
specifically proves active-only reads, deterministic ordering, nullable fields,
UTC timestamps, exact field set, absence of `source_system`/`source_id`, and
zero writes.

All browser errors prove stable code/message/request ID and absence of raw
exception/SQL/path/secret content.

### 17.2 Authority and mutation tests

Tests prove browser routes delegate to trusted domain services and do not
create alternate direct database/provider write paths. Duplicate click,
duplicate request, network uncertainty, and reconnect cases must not create
duplicate agent tasks, approval consumption, provider attempts, or Calendar
events.

### 17.3 RAG tests

Tests prove:

- validated answer renders;
- citations are preserved;
- course/global scope is honored;
- insufficient evidence is represented intentionally;
- rejected/unsupported generation is never returned;
- progress events contain no generated answer text.

### 17.4 SSE and Activity tests

Tests prove:

- `text/event-stream`;
- durable IDs are monotonic;
- Activity context is a closed typed union;
- arbitrary metadata/prompt/tool payload/exception/credential/path fields are
  rejected;
- disconnect/replay using last durable event ID works;
- ephemeral progress is not falsely replayed;
- replay failure forces authoritative recovery rather than silent loss;
- the Next.js proxy does not buffer/cache SSE.

### 17.5 System-health tests

Tests prove each probe is side-effect free and aggregation follows the frozen
criticality rules.

### 17.6 Frontend tests

Frontend testing covers behavior rather than excessive snapshots, including:

- course and assignment rendering;
- approval decision states;
- expired approval behavior;
- RAG citations and insufficient-evidence state;
- no answer before validation;
- health/status mappings;
- loading/empty/error/reconnect states;
- key end-to-end journeys and failure journeys.

### 17.7 Contract-generation gate

OpenAPI-derived TypeScript types are regenerated during verification. Any
uncommitted generated-type drift fails the gate.

### 17.8 Quality and acceptance gates

Phase 7 cannot pass until all required gates are satisfied:

```text
BACKEND_CONTRACT_TESTS         = PASS
FRONTEND_UNIT_TESTS            = PASS
FRONTEND_INTEGRATION_TESTS     = PASS
PRODUCTION_FRONTEND_BUILD      = PASS
TYPESCRIPT_STRICT              = PASS
BACKEND_PYTEST                 = PASS
RUFF                           = PASS
MYPY                           = PASS
LOCK_CHECK                     = PASS
DIFF_CHECK                     = PASS

FRONTEND_MATCHES_BACKEND       = PASS
OPENAPI_TYPES_IN_SYNC          = PASS

SSE_LIVE_DELIVERY              = PASS
SSE_RECONNECT_RECOVERY         = PASS
ACTIVITY_DURABILITY            = PASS

APPROVAL_UI_BACKEND_AUTHORITY  = PASS
DUPLICATE_WRITE_PROTECTION     = PASS
UNKNOWN_OUTCOME_RECOVERY       = PASS

RAG_VALIDATION_PRESERVED       = PASS
UNVALIDATED_MODEL_TEXT_LEAKS   = ZERO

BROWSER_SECRET_EXPOSURE        = ZERO
RAW_INTERNAL_EVENT_EXPOSURE    = ZERO
RAW_EXCEPTION_EXPOSURE         = ZERO
SYSTEM_HEALTH_SIDE_EFFECTS     = ZERO

RESPONSIVE_ACCEPTANCE          = PASS
ACCESSIBILITY_ACCEPTANCE       = PASS
```

A development server appearing to work is not acceptance. The production
frontend build is a blocking gate.

## 18. Implementation slices

### 7A — Browser/API Contract Foundation

- create the Phase 7 browser namespace and explicit Pydantic contracts;
- add browser-safe error handling and request correlation;
- add read-only academic projection service;
- implement `GET /api/v1/courses` as the first vertical proof;
- establish OpenAPI -> generated TypeScript type synchronization;
- preserve `/health` unchanged.

### 7B — Frontend Foundation and AAOS Design System

- scaffold Next.js + TypeScript frontend;
- configure Tailwind, shadcn/ui primitives, Lucide, TanStack Query;
- implement the Next.js -> FastAPI proxy;
- create responsive shell, sidebar, page header, AAOS design tokens, and common
  loading/empty/error/status components;
- prove a real backend query through the proxy.

### 7C — Academic Read-Only Experience

- course list/detail;
- assignments;
- documents and index/readiness state;
- Overview academic summary composition;
- citation-source navigation prerequisites.

### 7D — Ask AAOS / Validated RAG Interface

- query request contract;
- course/global scope;
- safe progress states;
- validated final answer only;
- citation rendering and source navigation;
- insufficient-evidence UX.

### 7E — Tasks and Approval-Controlled Actions

- browser-safe task projections;
- task status/detail UI;
- task initiation from user intent;
- browser-safe approval projections;
- approve/reject delegation through Phase 6;
- uncertain network-result reconciliation/refetch behavior.

### 7F — Activity, SSE, and System Health

- ActivityRecord persistence/projectors;
- history API/pagination;
- SSE durable invalidation and ephemeral progress events;
- reconnect/replay/recovery;
- system-health endpoint and UI;
- side-effect-free health verification.

### 7G — Integration, Security, Recovery, and Final Acceptance

- full workflows across academic data, RAG, tasks, approvals, activity, and
  health;
- disconnect and uncertain-outcome scenarios;
- browser secret-exposure review;
- raw-event/exception leak review;
- responsive and accessibility acceptance;
- production frontend build;
- full backend/frontend quality gates;
- human acceptance before `phase-07-passed` is created.

## 19. Phase boundaries and governance

Phase 7 starts from the frozen `phase-06b-passed` baseline. Phase 6C remains
optional and is not a prerequisite for this dashboard scope.

The repository governance documents currently contain stale pre-Phase-6
status language that conflicts with frozen Phase 6B evidence. A docs-only
governance correction must record the actual Phase 6B frozen baseline and the
human authorization status of Phase 7 without manufacturing Phase 5 acceptance
or removing unresolved Phase 5 limitations.

This exact written specification was explicitly human-approved at commit
`095979f` on 2026-09-16. Production implementation is therefore authorized
slice-by-slice through reviewed implementation plans under
`docs/superpowers/plans/`, beginning with 7A and using TDD.

## 20. Final design invariants

The design is accepted only with all of the following preserved throughout
implementation:

```text
Phase 7 reveals AAOS; it does not duplicate AAOS.

Browser state is not authoritative state.

Internal AAOS model is not browser API model.

User intent is not provider/tool authority.

Approval UI is not approval authority.

SSE is notification/recovery transport, not the database.

Activity is user-facing history, not the audit ledger.

Model-generated text is not user-visible RAG output until validated.

Health observation is side-effect free.

Visual reference may shape design, never product scope.
```
