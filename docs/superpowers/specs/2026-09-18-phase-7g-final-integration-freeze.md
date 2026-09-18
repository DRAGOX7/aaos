# Phase 7G Final Integration, Security, and Freeze

Status: discovery-complete design for human review. Product implementation is
not authorized by this document.

Baseline: phase-07-dashboard at
c1009c5a558e36b3aa8f76f8085febacabf33f33. Phase 7A through 7F are
human-accepted at their recorded implementation boundaries. Phase 7 remains
IN_PROGRESS, Phase 7G is NOT_STARTED, and no phase-07-passed tag exists.

## 1. Purpose

Phase 7G is the final integration, security, recovery, and freeze gate for the
already accepted dashboard. It resolves the two explicit global carryovers,
audits every accepted surface together, and produces the evidence required for
human acceptance and a later phase-07-passed tag.

Phase 7G is not a new feature phase. It must not add a new product domain,
provider, RAG architecture, event ledger, migration, credential UI, or
external-write authority.

## 2. Authoritative boundaries inherited

The following accepted contracts remain authoritative:

- FastAPI is the trusted browser boundary. The browser owns presentation and
  user intent only.
- Next.js is a same-origin proxy and has no AAOS policy or execution authority.
- Pydantic/FastAPI OpenAPI is the browser contract source of truth; generated
  TypeScript must remain synchronized.
- Phase 4 returns only validated grounded results or explicit insufficient
  evidence. Raw model output and private citations never reach the browser.
- Phase 5 least-authority proposal, scope, event, lifecycle, and sanitization
  rules are unchanged.
- Phase 6A owns capability, approval, claim, dispatch, reconciliation, and
  UNKNOWN_OUTCOME semantics. Phase 6B remains frozen at
  40ff9d3ef6e0e3010982dabfb37cb597aceeb6ed and exactly one historical real
  Google Calendar CREATE remains the only authorized real external write.
- Phase 7E derives TrustedApproverContext server-side. The browser cannot
  choose approver identity, capability, policy, target, provider arguments, or
  execution authority.
- Phase 7F Activity is a composite read projection, not a duplicate ledger;
  SSE is notification/replay transport; System Health is passive and
  side-effect-free.
- Importing or constructing AAOS is inert: no normal database, migration,
  model/RAG runtime, provider, poller, or SSE loop is started until an
  explicit request needs it.

## 3. Discovery findings

### 3.1 Settings carryover

The accepted Phase 7 design names Settings as a navigation area and permits
only narrow safe UI/local-runtime preferences. It defines no Settings API,
data contract, preference model, or required user workflow. The current shell
contains a visible Settings link to /settings, but the route tree has no
matching page and no backend Settings endpoint exists. This is a genuine
pre-existing dead navigation item, not a missing decorative page.

Decision: use design choice B from the authorization. Remove or disable the
Settings navigation item and update the navigation truth contract. Do not
invent a Settings page, configuration editor, credential view, authority
control, or placeholder route. The accepted Phase 7 scope after this decision
contains only routes backed by an implemented contract.

### 3.2 Favicon carryover

No favicon/icon file exists under frontend/app or frontend/public and the
layout metadata has no icon declaration. The smallest repository-native
solution is a deterministic, hand-authored SVG at frontend/app/icon.svg with
an explicit metadata icon reference. The SVG contains only the AAOS mark,
uses no remote resources, embedded metadata, secrets, or generated binary
content, and is served by the Next production runtime through the supported
App Router metadata convention.

### 3.3 Persistence and runtime

The repository migration chain ends at
0006_phase7e_task_records. Phase 7G adds no migration and uses only a
disposable database migrated through the existing Alembic chain for production
acceptance. The normal data/database/aaos.db path must be absent at the start
of import and browser checks and the normal C:\Users\aljaf\aaos\data location
must never be touched.

## 4. Final navigation truth contract

The visible desktop and mobile navigation is exactly:

| Label | Frontend route | Route kind | Browser authority |
| --- | --- | --- | --- |
| Overview | / | server composition | read-only accepted queries |
| Courses | /courses | list | GET /api/v1/courses |
| Assignments | /assignments and /assignments?course_id=<positive integer> | list/filter | GET /api/v1/assignments |
| Documents | /documents and /documents?course_id=<positive integer> | list/filter | GET /api/v1/documents |
| Ask AAOS | /ask | validated RAG read/compute | POST /api/v1/rag/queries with the closed request contract |
| Tasks | /tasks and /tasks/<task id> | browser-safe task read | GET /api/v1/tasks and GET /api/v1/tasks/<id> |
| Approvals | /approvals and /approvals/<approval id> | read plus approved decisions | GET reads; POST approve/reject only through Phase 7E routes |
| Activity | /activity | history and live read | GET /api/v1/activity and GET /api/v1/activity/stream |
| System Health | /system-health | passive read | GET /api/v1/system-health |

Course and document detail routes remain /courses/<positive integer> and
/documents/<positive integer>. No Settings route is presented. Any route
outside this table is not a Phase 7G deliverable.

## 5. Browser/API boundary audit

Every browser fetch helper calls only relative same-origin /api/v1 paths.
AAOS_BACKEND_ORIGIN remains server-only in next.config.ts and the SSE proxy.
No browser asset may contain the backend origin, provider URL, credential,
authorization header, filesystem path, SQL, or Phase 6 private identity.

The final browser endpoint inventory is:

- read-only GET: courses, course detail, assignments, documents, document
  detail, tasks, task detail, approvals, approval detail, activity history,
  Activity SSE, and system health;
- validated RAG POST: /api/v1/rag/queries;
- approved Phase 7E mutation POST: /api/v1/tasks and
  /api/v1/approvals/<id>/approve or /reject;
- process liveness: GET /health.

No generic execute endpoint, arbitrary tool route, provider-direct route,
cursor mutation, Activity/System Health write, or browser-supplied
TrustedApproverContext is permitted.

## 6. Surface-specific audit requirements

### RAG

Recheck that Ask AAOS renders only validated answers with browser-safe
citations, keeps insufficient evidence distinct, redacts operational
failures, and exposes no model/provider credential or action authority. The
existing Phase 4/7D design is not changed. If a representative production
RAG run is performed, it must use the already available local acceptance stack
without downloading models or contacting an external provider.

### Activity and SSE

Recheck deterministic composite ordering, strict-before history, strict-after
stream replay, opaque bounded cursors, reconnect/dedup, exactly-once live
rendering, inert prelude, heartbeat/disconnect cleanup, and the absence of raw
ActionEvent, approval, operation, task, path, SQL, credential, exception, or
provider fields. Activity remains GET-only and never mutates the ledger.

### System Health

Recheck that /health remains liveness-only and /api/v1/system-health performs
bounded local reads only. It must not migrate, repair, sync, ingest, index,
load models, refresh credentials, contact providers, or claim fake uptime or
latency. UNKNOWN and NOT_CONFIGURED must remain truthful.

### Startup and persistence

Import-inert tests must prove that importing aaos.main and constructing the
default app do not create/open the normal database, run Alembic, initialize
RAG/models, contact providers, or start loops. Browser acceptance must use a
disposable Alembic-migrated database only and delete it after evidence is
persisted.

## 7. Security and privacy audit

The final artifact scan covers browser-delivered JavaScript, CSS, HTML,
generated API artifacts, and persisted screenshots/evidence for:

DATABASE_URL, AAOS_BACKEND_ORIGIN, Authorization, Bearer, data/auth,
data/credentials, storage_key, TrustedApproverContext, provider payload,
Google tokens, OAuth, cookies, local filesystem paths, traceback, SQL, API
keys, raw PreparedAction arguments, raw ActionEvent payload, request
fingerprints, and idempotency keys.

Ambiguous matches are inspected manually and classified as server-only
configuration, accepted test fixture text, or a defect. No secret or
credential-bearing artifact is committed. No Activity/Health/RAG/read page
can trigger an external write. The historical Phase 6B Calendar write is not
repeated.

## 8. Responsive, accessibility, and design audit

Production next start acceptance covers 1440, 1280, 768, and 390 CSS-pixel
widths. Representative evidence covers Overview, Courses, Assignments,
Documents, Ask AAOS, Tasks, Approvals, Activity, and System Health. It checks
no horizontal overflow, clipped controls, unreadable text, framework error
pages, or Next development badge.

Keyboard and accessibility checks cover visible focus, sidebar/drawer
open-close and Escape behavior, semantic headings, labels, status meaning
beyond color, button/link distinction, dialog focus where present, and usable
touch targets. This is an acceptance audit, not a claim of formal WCAG
certification.

All pages must continue using the accepted PageHeader, AppShell, SectionCard,
status, spacing, typography, and token language. 7G may correct an obvious
integration defect revealed by a focused RED test, but may not redesign the
product or add unsupported dashboard widgets.

## 9. Error, fake-data, and resource audit

The final walk verifies bounded loading, empty, unavailable, invalid-parameter,
RAG-insufficient, approval-unavailable, SSE-reconnecting, and health-unavailable
states without raw exceptions or stack traces. Source inspection rejects
hard-coded user-facing courses, assignments, documents, tasks, approvals,
Activity rows, health metrics, schedules, notifications, or profile data.

No constant background database polling, runaway EventSource, provider probe,
model preload, unbounded result accumulation, or repeated health work may be
introduced.

## 10. Acceptance environment and evidence

Acceptance starts from a clean tracked tree with no normal database and no
listeners on ports 3000, 8000, or 9222. A deterministic disposable fixture
may be created through Alembic only, frozen and hashed before browser
inspection, then used by FastAPI and production next start:

~~~text
FastAPI: 127.0.0.1:8000
Next.js: npm run start -- --hostname 127.0.0.1 --port 3000
~~~

No next dev runtime is accepted. The walk records direct backend and
same-origin proxy responses, browser console/network evidence, route status,
SSE behavior, screenshots, artifact-scan results, and cleanup hashes. Servers
are stopped, ports are rechecked, and only the disposable worktree database is
deleted.

Phase 7G acceptance performs zero real external writes. Phase 7E deterministic
FakeExternalAdapter evidence remains the only execution proof. No model or
RAG call is required for read-only integration unless the already accepted
local 7D stack is available and a representative final-HEAD RAG check is
needed; unavailable local model/index evidence is reported, never hidden by a
remote fallback.

## 11. Implementation boundaries

The reviewed plan uses six bounded tasks:

1. remove the dead Settings navigation item and freeze the route truth table;
2. add the repository-native favicon/icon contract;
3. add whole-navigation, route/prefetch, and generated-contract audit tests;
4. add final authority, artifact, credential, and import-inert audit tests;
5. run responsive/accessibility/design/error/resource integration checks and
   make only focused fixes proven by RED evidence;
6. run verification-only production acceptance, independent review, and freeze
   recommendation.

Any need for a migration, provider integration, new RAG architecture,
credential UI, event persistence, direct browser backend access, or major
redesign is a hard stop for a new human architecture decision.

## 12. Freeze and governance sequence

Phase 7G must not create phase-07-passed during implementation or planning.
The later freeze sequence is:

1. implementation commits are complete and reviewed;
2. all backend/frontend/static/import/security gates are fresh and green;
3. production browser acceptance is green with zero unexpected errors and zero
   external writes;
4. independent review has no Critical or Important finding;
5. human explicitly accepts Phase 7G;
6. governance records PHASE_7G=HUMAN_ACCEPTED;
7. human explicitly accepts whole Phase 7;
8. governance records PHASE_7=PASSED;
9. create an annotated phase-07-passed tag, matching the existing annotated
   phase-06b-passed convention, on the exact accepted final HEAD.

The tag is never created before both human decisions and the final clean-tree
check.

## 13. Explicit non-goals

Phase 7G does not implement Phase 8, Phase 6C, Settings preferences, new
academic/RAG/task/approval features, provider integrations, external writes,
credentials, migrations, a duplicate Activity ledger, a new SSE protocol,
model downloads, remote fallback, or any functionality from a future phase.
