# Phase 7G Final Integration, Security, and Freeze Implementation Plan

> For agentic workers: use superpowers:subagent-driven-development or
> superpowers:executing-plans to implement this plan task-by-task. Every
> implementation task is tracked with checkbox steps and ends at its own
> reviewable commit.

**Goal:** Close the accepted Phase 7 Settings/favicon carryovers and prove the
whole dashboard remains secure, same-origin, import-inert, responsive, and
production-ready without adding a feature phase.

**Architecture:** Keep FastAPI as the trusted browser boundary, Next.js as a
server-only same-origin proxy, Phase 4/5/6/7E authority unchanged, and Phase
7F Activity/System Health read-only. Remove the unsupported Settings link,
add one repository-authored Next icon, then add deterministic audits and a
verification-only production freeze gate.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy/SQLite, Alembic, Next.js 16,
React 19, TypeScript, TanStack Query, Vitest, Testing Library, pytest, Ruff,
mypy, uv, and the existing production browser harness.

**Spec:** docs/superpowers/specs/2026-09-18-phase-7g-final-integration-freeze.md

## Global Constraints

- Work only in C:\Users\aljaf\.codex\worktrees\aaos-phase-07 on
  phase-07-dashboard from clean checkpoint
  c1009c5a558e36b3aa8f76f8085febacabf33f33.
- Read the Phase 7G spec and accepted Phase 7A–7F documents before each task.
- Do not modify frozen Phase 4/5/6 implementation or Phase 7A–7F semantics.
- Do not add a migration, table, broker, WebSocket, worker fleet, provider,
  credential UI, RAG architecture, or external-write capability.
- The only browser mutations remain the closed Phase 7E task and approval
  routes; Activity, Health, academic reads, and navigation never mutate.
- Do not use create_all for production acceptance. Use only a disposable
  Alembic-migrated database and delete it after evidence is persisted.
- Do not run next dev for acceptance. Use FastAPI on 127.0.0.1:8000 and
  production next start on 127.0.0.1:3000.
- No real Google Calendar, Moodle, or other external write is permitted.
- No model download, remote fallback, credential access, or provider network
  call is permitted. A representative Ask AAOS run may use the already
  available local Phase 7D stack only; absence is reported, not bypassed.
- No existing test may be deleted, weakened, skipped, or xfailed.
- Tasks 1 and 2 start with focused RED tests, reach the smallest GREEN change,
  run focused regressions, and create one reviewable commit. Tasks 3, 4, and
  5 are characterization/audit-test additions: their first run may pass
  immediately because they freeze already accepted behavior, and they still
  create one reviewable test commit. Task 6 is verification-only and creates
  no code commit.

## Task 1: Remove unsupported Settings navigation

**Files:**

- Modify: frontend/components/aaos/app-sidebar.tsx
- Modify: frontend/tests/navigation.test.tsx

**Interfaces:**

- Consumes the existing readonly AAOS_NAVIGATION array and AppSidebar props.
- Produces an exact nine-entry navigation array with no Settings item:
  Overview, Courses, Assignments, Documents, Ask AAOS, Tasks, Approvals,
  Activity, and System Health.
- No /settings route, Settings API, preference contract, or placeholder page is
  produced.

- [ ] **Step 1: Write the failing navigation contract test**

Replace the expected label list in the focused test with the accepted
post-carryover route list and add exact href assertions:

~~~typescript
expect(links.map((link) => link.textContent?.trim())).toEqual([
  "Overview", "Courses", "Assignments", "Documents", "Ask AAOS",
  "Tasks", "Approvals", "Activity", "System Health",
]);
expect(links.map((link) => link.getAttribute("href"))).toEqual([
  "/", "/courses", "/assignments", "/documents", "/ask", "/tasks",
  "/approvals", "/activity", "/system-health",
]);
expect(links).toHaveLength(9);
~~~

- [ ] **Step 2: Run the focused test and capture RED**

Run from frontend:

~~~text
npm test -- --run tests/navigation.test.tsx
~~~

Expected RED: the current ten-entry shell contains Settings and the expected
label/href/count assertions fail.

- [ ] **Step 3: Remove only the dead navigation item**

Delete the Settings icon import and the single Settings object from
AAOS_NAVIGATION. Do not change AppShell layout, active-link behavior, drawer
behavior, labels of the remaining entries, or styles.

- [ ] **Step 4: Run focused GREEN and shell regressions**

~~~text
npm test -- --run tests/navigation.test.tsx tests/app-shell.test.tsx tests/course-views.test.tsx
~~~

Expected: all focused navigation/shell tests pass and no route or API file is
changed.

- [ ] **Step 5: Commit**

~~~text
git add frontend/components/aaos/app-sidebar.tsx frontend/tests/navigation.test.tsx
git commit -m "fix(phase7g): remove dead settings navigation"
~~~

Stop if the repository requires a legitimate Settings data contract to keep
the link. Do not create a decorative page.

## Task 2: Add the repository-native application icon

**Files:**

- Create: frontend/app/icon.svg
- Modify: frontend/app/layout.tsx
- Create: frontend/tests/favicon.test.ts

**Interfaces:**

- Consumes Next App Router metadata and the existing AAOS mark language.
- Produces one deterministic browser icon at /icon.svg and an explicit
  metadata link to that icon.
- The SVG is a small repository-authored vector with no external href,
  embedded data, user data, environment values, or binary provenance.

- [ ] **Step 1: Write the failing icon tests**

Create a static test that reads frontend/app/icon.svg and layout.tsx and
asserts the exact safety/metadata contract:

~~~typescript
expect(icon).toMatch(/^<svg[\s>]/);
expect(icon).toContain('viewBox="0 0 64 64"');
expect(icon).not.toMatch(/https?:|data:|DATABASE_URL|token|secret|path/i);
expect(layout).toContain('icons:');
expect(layout).toContain('"/icon.svg"');
~~~

- [ ] **Step 2: Run the focused test and capture RED**

~~~text
npm test -- --run tests/favicon.test.ts
~~~

Expected RED: neither icon.svg nor metadata icon configuration exists.

- [ ] **Step 3: Implement the smallest icon contract**

Add a 64 by 64 SVG containing only the existing AAOS dashboard mark: a solid
primary rounded-square field and a simple contrasting dashboard glyph. Add
icons: { icon: "/icon.svg", shortcut: "/icon.svg" } to the existing Metadata
object without changing title, description, layout, fonts, or theme tokens.

- [ ] **Step 4: Run GREEN and build metadata checks**

~~~text
npm test -- --run tests/favicon.test.ts tests/design-tokens.test.ts
npm run typecheck
npm run build
~~~

Expected: the file contract, TypeScript, and production build pass. The
production browser check in Task 6 must receive HTTP 200 for /icon.svg and
must not request a missing favicon path.

- [ ] **Step 5: Commit**

~~~text
git add frontend/app/icon.svg frontend/app/layout.tsx frontend/tests/favicon.test.ts
git commit -m "fix(phase7g): add application favicon"
~~~

Stop if Next metadata emits a different icon route or requires a binary asset;
record the exact framework constraint for human review instead of adding an
unproven asset.

## Task 3: Freeze navigation, route, and generated-contract audits

**Files:**

- Create: tests/test_phase7g_route_inventory.py
- Create: frontend/tests/phase7g-navigation.test.ts
- Create: frontend/tests/phase7g-api-boundary.test.ts

**Interfaces:**

- Consumes create_app().openapi(), AAOS_NAVIGATION, the committed App Router
  route tree, next.config.ts, and the generated OpenAPI/TypeScript artifacts.
- Produces deterministic tests for the exact accepted route/API inventory and
  same-origin browser boundary. Tests do not start a server or mutate a
  database.

- [ ] **Step 1: Write focused inventory tests**

The backend test must assert the OpenAPI path set contains exactly these
Phase 7 operations, with no Settings path:

~~~python
EXPECTED = {
    "/api/v1/courses",
    "/api/v1/courses/{course_id}",
    "/api/v1/assignments",
    "/api/v1/documents",
    "/api/v1/documents/{document_id}",
    "/api/v1/rag/queries",
    "/api/v1/tasks",
    "/api/v1/tasks/{task_id}",
    "/api/v1/approvals",
    "/api/v1/approvals/{approval_id}",
    "/api/v1/approvals/{approval_id}/approve",
    "/api/v1/approvals/{approval_id}/reject",
    "/api/v1/activity",
    "/api/v1/activity/stream",
    "/api/v1/system-health",
}
~~~

The frontend tests must assert every AAOS_NAVIGATION href is in the route
allowlist, no href is /settings, every academic fetch helper uses a relative
/api/v1 URL, and next.config.ts is the only source of AAOS_BACKEND_ORIGIN.

- [ ] **Step 2: Run the characterization test set**

~~~text
uv run pytest tests/test_phase7g_route_inventory.py -q
cd frontend
npm test -- --run tests/phase7g-navigation.test.ts tests/phase7g-api-boundary.test.ts
cd ..
~~~

The new test modules are the RED/GREEN boundary for this audit-only task:
their first run must either fail on a concrete route/proxy discrepancy or
pass as a characterization of the already corrected Task 1 and Task 2
contracts. A clean PASS is acceptable only when the exact expected inventory
is proven; do not manufacture a failing assertion.

- [ ] **Step 3: Add deterministic audit tests only**

Implement the tests using static source reads and create_app().openapi().
Do not add runtime requests, SQL, new environment variables, or production
fallbacks. Assert that the only browser mutation methods are the closed
Phase 7E POST routes plus RAG POST, and that Activity/Health/academic route
files contain no mutation method.

- [ ] **Step 4: Run GREEN and contract drift gates**

~~~text
uv run pytest tests/test_phase7g_route_inventory.py tests/test_phase7_openapi_contract.py -q
cd frontend
npm test -- --run tests/phase7g-navigation.test.ts tests/phase7g-api-boundary.test.ts tests/proxy-config.test.ts tests/academic-api.test.ts
npm run check:api-types
cd ..
~~~

- [ ] **Step 5: Commit**

~~~text
git add tests/test_phase7g_route_inventory.py frontend/tests/phase7g-navigation.test.ts frontend/tests/phase7g-api-boundary.test.ts
git commit -m "test(phase7g): freeze route and browser boundary audits"
~~~

Stop if the inventory requires a new endpoint, direct FastAPI browser access,
or a migration.

## Task 4: Add final authority, privacy, and import-inert regression audits

**Files:**

- Create: tests/test_phase7g_security.py
- Create: tests/test_phase7g_import_inert.py
- Create: frontend/tests/phase7g-security.test.ts

**Interfaces:**

- Consumes accepted Phase 6/7E services, Phase 7F projections, app
  construction, and the existing static frontend tree.
- Produces zero-authority-regression and zero-leakage tests. No test invokes a
  provider, model, RAG runtime, external write, or browser-held approver.

- [ ] **Step 1: Write focused security/import tests**

The backend tests must prove:

1. importing aaos.main in a subprocess with a temporary working directory
   creates no data/database/aaos.db;
2. create_app() has no startup handlers that open a database or start a loop;
3. Activity and System Health route dependencies are read-only;
4. approval command routes obtain TrustedApproverContext from the server
   provider, never request JSON fields for it;
5. no route named execute, retry, reconcile, or provider exists.

The frontend test must scan source and committed generated artifacts for the
forbidden browser terms from the spec, while allowing the server-only
AAOS_BACKEND_ORIGIN reference in next.config.ts only.

Use a subprocess for the import fence rather than relying on test-process
module caches:

~~~python
result = subprocess.run(
    [sys.executable, "-c", "import aaos.main; aaos.main.create_app()"],
    cwd=temporary_project_root,
    env={**os.environ, "PYTHONPATH": str(repository_root / "src")},
    capture_output=True,
    text=True,
    check=True,
)
assert not (temporary_project_root / "data/database/aaos.db").exists()
assert result.stderr == ""
~~~

The source audit must enumerate only browser-delivered frontend files and
reject these case-insensitive tokens there:
DATABASE_URL, Authorization, Bearer, data/auth, data/credentials,
TrustedApproverContext, provider payload, Google tokens, OAuth, cookies,
filesystem paths, traceback, SQL, API keys, raw PreparedAction arguments, raw
ActionEvent payload, request fingerprints, and idempotency keys. The one
server-only AAOS_BACKEND_ORIGIN occurrence in next.config.ts is explicitly
excluded from the browser-file set.

- [ ] **Step 2: Run the characterization test set**

~~~text
uv run pytest tests/test_phase7g_security.py tests/test_phase7g_import_inert.py -q
cd frontend
npm test -- --run tests/phase7g-security.test.ts
cd ..
~~~

This is an audit-only task. The first run may pass immediately when the
accepted import-inert and authority boundaries are already intact. Any
failure must identify a concrete leak or side effect before production code
is considered.

- [ ] **Step 3: Implement tests without widening authority**

Use subprocess/import monkeypatches and source inspection. Never read or print
credential files. Do not assert that a secret value is absent by loading it;
assert only safe path/name and response-field properties. Keep server-only
configuration classified separately from browser assets.

- [ ] **Step 4: Run GREEN and accepted security regressions**

~~~text
uv run pytest tests/test_phase7g_security.py tests/test_phase7g_import_inert.py tests/test_phase7f_security.py tests/test_phase7e_mutation_security.py tests/test_phase7_rag_security.py -q
cd frontend
npm test -- --run tests/phase7g-security.test.ts tests/proxy-config.test.ts tests/activity-sse-proxy.test.ts tests/task-api.test.ts tests/rag-api.test.ts
cd ..
~~~

- [ ] **Step 5: Commit**

~~~text
git add tests/test_phase7g_security.py tests/test_phase7g_import_inert.py frontend/tests/phase7g-security.test.ts
git commit -m "test(phase7g): audit authority privacy and startup inertness"
~~~

Stop on any authority regression, raw event/exception/credential leak, normal
database creation, model/provider initialization, or weakened historical
security test.

## Task 5: Add final UI integration and acceptance assertions

**Files:**

- Create: frontend/tests/phase7g-ui-integration.test.tsx
- Create: frontend/tests/phase7g-state-boundaries.test.tsx

**Interfaces:**

- Consumes the accepted AppShell, PageHeader, navigation, Activity stream,
  System Health, RAG, Tasks, Approvals, academic views, query clients, and
  error projections.
- Produces component-level regressions for loading/empty/error/reconnecting
  behavior, accessible labels/statuses, no fake metrics/content, no
  unsupported Settings controls, and no Activity/Health mutation controls.

- [ ] **Step 1: Write focused component assertions**

Add focused tests that render representative states and assert:

~~~typescript
expect(screen.queryByText(/settings/i)).not.toBeInTheDocument();
expect(screen.getByRole("heading", { name: "Activity" })).toBeVisible();
expect(screen.getByText("Unknown")).toBeVisible();
expect(screen.getByRole("status")).toHaveAccessibleName();
expect(screen.queryByRole("button", { name: /retry execution|repair|restart/i }))
  .not.toBeInTheDocument();
~~~

Cover RAG insufficient evidence, approval unavailable, SSE reconnecting,
health unavailable, academic empty data, keyboard-visible navigation, and
status text that does not rely on color alone. Keep fixture text synthetic and
do not add user-facing fake records.

- [ ] **Step 2: Run the characterization test set**

~~~text
cd frontend
npm test -- --run tests/phase7g-ui-integration.test.tsx tests/phase7g-state-boundaries.test.tsx
cd ..
~~~

The first run may pass immediately because accepted component behavior is
already frozen. A failure must identify a concrete UI/accessibility defect;
do not manufacture a failing assertion.

- [ ] **Step 3: Make only evidence-backed integration corrections**

Run the new tests. If they pass, make no production change. If one fails,
change only the component named by the failure, preserve existing design
tokens/layout/copy, add the focused regression, and do not add a new surface,
metric, authority, or route. A failure that would require a migration,
provider call, RAG redesign, credential UI, or broad redesign is a hard stop.

- [ ] **Step 4: Run GREEN and frontend regressions**

~~~text
cd frontend
npm test -- --run tests/phase7g-ui-integration.test.tsx tests/phase7g-state-boundaries.test.tsx tests/app-shell.test.tsx tests/activity-views.test.tsx tests/health-views.test.tsx tests/approval-views.test.tsx tests/task-views.test.tsx tests/ask-view.test.tsx tests/course-views.test.tsx tests/assignment-view.test.tsx tests/document-views.test.tsx
npm run typecheck
npm run lint
npm run build
cd ..
~~~

- [ ] **Step 5: Commit**

If only tests changed:

~~~text
git add frontend/tests/phase7g-ui-integration.test.tsx frontend/tests/phase7g-state-boundaries.test.tsx
git commit -m "test(phase7g): cover final UI state boundaries"
~~~

If a focused production correction was proven, include only the named
component and its regression in a separate commit with a message describing
that defect. Do not combine unrelated formatting or visual changes.

## Task 6: Whole-Phase-7 production acceptance and freeze recommendation

**Files:**

- No implementation files; verification-only.
- Persist evidence outside tracked paths, then delete the disposable database
  and temporary runtime data.

**Interfaces:**

- Consumes the final implementation HEAD, accepted Phase 7 fixtures, the
  production FastAPI/Next runtime, and the canonical test commands.
- Produces a bounded acceptance report, screenshot/network evidence, artifact
  scan result, independent review, and a recommendation. It does not create a
  governance acceptance record or phase-07-passed tag.

- [ ] **Step 1: Verify final preflight**

~~~text
git status --short
git rev-parse HEAD
git tag --list phase-07-passed
Test-Path data/database/aaos.db
Get-NetTCPConnection -LocalPort 3000,8000,9222 -State Listen
~~~

Require a clean tree, absent normal database, inactive ports, no tag, and the
expected final task commit range. Confirm migration head
0006_phase7e_task_records without mutating the normal database.

- [ ] **Step 2: Create and freeze only the disposable fixture**

Use the accepted deterministic Phase 7 fixture conventions. Apply Alembic to
the worktree database, populate only synthetic rows required for academic,
RAG/read, task/approval, Activity, and health evidence, compute a manifest and
database hash, and freeze both before inspecting browser results. Do not
modify rows after freeze. Use FakeExternalAdapter for any task/approval
execution state and record REAL_EXTERNAL_WRITES=0.

- [ ] **Step 3: Run fresh canonical gates**

~~~text
uv run pytest -q
uv run ruff check .
uv run mypy src
uv run mypy src/aaos scripts/export_openapi.py
uv lock --check
git diff --check
cd frontend
npm test -- --run
npm run typecheck
npm run lint
npm run build
npm run check:api-types
cd ..
~~~

Import-inert, route-inventory, security, and UI-integration tests must be
included. Any failure is recorded and fixed only through a new focused task
commit; acceptance does not waive it.

- [ ] **Step 4: Run production browser acceptance**

Start FastAPI on 127.0.0.1:8000 and production next start on 127.0.0.1:3000.
Verify /health, every navigation route, direct browser API/proxy parity, RAG
answered/insufficient states using the existing local stack when available,
task/approval read and fake decision states, Activity history/SSE replay, and
passive System Health. Capture responsive evidence at 1440, 1280, 768, and
390 widths. Classify aborted speculative prefetches separately; require zero
unexpected 404/other 4xx/5xx, zero JavaScript exceptions, zero direct
127.0.0.1:8000 browser requests, zero provider requests, and zero writes.

- [ ] **Step 5: Run security/artifact and cleanup checks**

Scan browser assets and persisted evidence using the exact forbidden-token list
in the spec. Inspect ambiguous hits manually. Stop servers, confirm ports are
inactive, verify the frozen fixture hash, delete only the disposable
worktree database, and require clean tracked status. Keep final screenshots and
machine-readable evidence outside tracked paths for human review.

- [ ] **Step 6: Independent review and stop**

Review the final diff and evidence for carryover resolution, route truth,
same-origin enforcement, authority preservation, RAG validation, Activity/SSE
semantics, passive health, import inertness, no fake data, accessibility,
responsive behavior, no migration, no external writes, and no Phase 7G
leakage. Classify findings as CRITICAL, IMPORTANT, or MINOR. Resolve every
Critical/Important finding with a new focused commit and rerun affected gates.
Report:

~~~text
PHASE_7G_READY_FOR_HUMAN_ACCEPTANCE=YES
REAL_EXTERNAL_WRITES=0
PHASE_7G_TAG_CREATED=NO
~~~

Stop at HUMAN_REVIEW_OF_PHASE7G_IMPLEMENTATION_AND_ACCEPTANCE. Governance
recording, whole-Phase-7 acceptance, and phase-07-passed creation require
separate explicit human decisions.

The later tag operation must follow the repository precedent: create an
annotated phase-07-passed tag only after both human acceptance records and the
final clean-tree check, targeting the exact accepted implementation HEAD.

## Invariant-to-task coverage

| Invariant | Proving task |
| --- | --- |
| No dead Settings navigation or invented settings authority | 1, 3, 6 |
| Favicon route is local, deterministic, and leak-free | 2, 6 |
| Exact navigation/API route truth and no prefetch dead links | 1, 3, 6 |
| Same-origin proxy; no direct backend/provider browser access | 3, 4, 6 |
| Only approved Phase 7E mutations remain | 3, 4, 6 |
| RAG validated output and insufficient evidence remain distinct | 4, 5, 6 |
| Activity/SSE order, replay, dedup, and no mutation | 4, 5, 6 |
| Passive truthful System Health | 4, 5, 6 |
| Import-inert startup and no normal DB mutation | 4, 6 |
| OpenAPI/generated TypeScript zero drift | 3, 6 |
| No browser secrets/raw internal fields/fake data | 3, 4, 5, 6 |
| Responsive/accessibility/design consistency | 5, 6 |
| Zero real external writes and no Phase 6/7E authority changes | Global, 4, 6 |
| No migration or future-phase leakage | Global, 3, 4, 6 |
| Human acceptance before governance/tag freeze | 6 |

## Plan self-review

- All six tasks have exact files, a focused RED or characterization boundary,
  a smallest GREEN boundary where production behavior changes, commands,
  commit policy, and stop conditions.
- The plan creates no Settings feature, no migration, no provider route, no
  duplicate Activity store, no model download, and no phase-07-passed tag.
- Task 6 is verification-only and cannot mutate the normal database or perform
  a real external write.
- Every browser mutation is either the existing closed RAG POST or the
  server-derived Phase 7E task/approval path; no generic execute/retry route is
  introduced.
- The plan preserves Phase 6 UNKNOWN_OUTCOME and reconciliation semantics and
  treats Activity/SSE as read/recovery transport only.
- No task depends on a later task's uncommitted implementation. If a RED or
  characterization result requires scope outside the named files or global
  constraints, the worker stops for human architecture review.

## Execution handoff

This plan is not implementation authorization. After human approval, execute
Tasks 1 through 5 sequentially with review after each commit, then perform
Task 6 verification-only. Stop at
HUMAN_REVIEW_OF_PHASE7G_IMPLEMENTATION_AND_ACCEPTANCE. Do not record Phase 7G
acceptance, mark Phase 7 passed, or create phase-07-passed without separate
human authorization.
