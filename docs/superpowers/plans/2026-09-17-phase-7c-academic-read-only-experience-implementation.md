# Phase 7C Academic Read-Only Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use the superpowers:executing-plans skill to implement this plan task by task.

**Goal:** Extend the accepted Phase 7 dashboard with a truthful academic read-only experience for courses, assignments, documents, readiness metadata, overview composition, and the prerequisites for later citation navigation.

**Architecture:** Keep FastAPI as the trusted browser boundary. Reuse the existing academic/document/RAG persistence read models through narrow application read services, project every response into strict browser-safe Pydantic contracts, and expose only deterministic GET routes. Add typed relative-URL query clients and explicit loading/empty/success/error UI states in the existing Next.js shell. Do not invoke ingestion, RAG execution, model inference, or any write path.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy/SQLite, existing Alembic schema, Next.js App Router, React, TypeScript, TanStack Query, existing AAOS design-system components, pytest, Vitest, Ruff, mypy, and the repository's existing OpenAPI generation/check workflow.

**Authoritative spec:** `docs/superpowers/specs/2026-09-16-phase-7-dashboard-operational-visibility-design.md`

## Global constraints

- Work only in the Phase 7 worktree on `phase-07-dashboard`; begin from accepted 7B HEAD `52d96cd65fa8e50e8c72d66f8f9a9133eb12c9c8` and keep the frozen Phase 4/5/6 boundaries intact.
- This plan covers 7C only. Do not implement Ask AAOS/RAG (7D), task/approval actions (7E), activity/SSE/system health (7F), external writes, new provider authority, credentials, or fake dashboard data.
- Browser-facing contracts are strict allowlists. Never expose ORM instances, source identifiers, local paths, storage keys, SQL, tracebacks, credentials, provider payloads, or raw RAG output.
- Reads must be deterministic and side-effect free: no INSERT/UPDATE/DELETE, ingestion calls, embedding/model loading, FTS/Chroma execution, network calls, or external writes.
- Preserve the existing C1–C9/Phase 6 approval and least-authority model. The browser is a read client; it cannot grant permissions, approval, or execution authority.
- Tasks 1–8 start with focused RED tests, make the smallest implementation change, run focused GREEN tests plus relevant regressions, and create one reviewable commit. Task 9 is verification-only and creates no commit. Never weaken, skip, or xfail an existing test.

## Current-state findings and reuse map

- `src/aaos/web/contracts.py` already contains strict frozen `CourseSummary`, `CourseListResponse`, and stable API error contracts.
- `src/aaos/web/academic.py` already provides `AcademicReadService.list_courses()` with active-course filtering, deterministic ordering, and UTC normalization. Extend this service rather than introducing a parallel database abstraction.
- `src/aaos/web/routes.py` currently owns the `CourseReader` protocol and `/api/v1/courses` router factory. Extend that composition boundary with typed academic/document reader protocols and GET routes.
- `src/aaos/main.py` owns lazy default reader construction and `/health`; preserve lazy startup and add read-only reader wiring without startup migrations or eager model/RAG initialization.
- `src/aaos/persistence/academic_models.py` contains `CourseORM`, `AssignmentORM`, and related academic rows. `AssignmentORM` is the source for assignment summaries; inactive rows remain hidden by the trusted read service.
- `src/aaos/persistence/document_models.py` and `src/aaos/persistence/rag_models.py` contain document versions/pages and index-generation/readiness state. Map only safe title/status/page-count/update fields into the browser contract; never expose storage keys, local paths, page content, or provider identifiers.
- `src/aaos/documents/repository.py` and service methods are ingestion/write boundaries and must not be called by 7C reads. `src/aaos/rag/repository.py` and Phase 4 contracts are useful typed state vocabulary, but 7C must not invoke RAG execution or load models.
- `src/aaos/web/errors.py` provides `Phase7ApiError`, request IDs, and stable error envelopes. Reuse it for not-found and read-service failures.
- The frontend already has the accepted shell/sidebar/header/card components, `frontend/lib/api/courses.ts`, `frontend/lib/query/courses.ts`, query-key conventions, generated OpenAPI types, and established Vitest/component-test patterns.
- Persistence ordering was verified from `migrations/versions`: `0004_phase4_local_rag` follows `0003_phase3_document_ingestion`, and the current approved head is `0005_phase6a_approval_core` with `down_revision = "0004_phase4_local_rag"`. 7C uses this existing schema through SELECT-only services and does not add a migration.
- Phase 5 authority interfaces were verified in `src/aaos/agent/registry.py`, `src/aaos/agent/policy.py`, `src/aaos/agent/runtime_policy.py`, and `src/aaos/agent/orchestrator.py`: `ToolRegistry` is static, `PermissionPolicy.evaluate()` is trusted, and `EffectiveRuntimePolicy`/`AgentOrchestrator` remain outside the browser read path. No 7C route may call `ToolRegistry.execute`, mutate policy, or construct a proposal.

## Scope matrix

| Capability | 7C treatment | Trusted source | Explicitly deferred |
| --- | --- | --- | --- |
| Course list/detail | Add detail and retain existing list | `AcademicReadService` + `CourseORM` | course mutation |
| Assignments | Add list/filter and deterministic due grouping | `AssignmentORM` | completion, task writes, reminders |
| Documents | Add safe metadata and ingestion/extraction/index readiness | document/RAG ORM read projection | ingestion, page-content search, RAG execution |
| Overview | Compose existing course/assignment/document queries | typed GET clients | dedicated analytics aggregate, invented metrics |
| Citation navigation prerequisites | Validate safe document destinations only | bounded `documentHref` helper and document read route | Ask AAOS/RAG answer/citation parsing |
| Future phases | No implementation | — | 7D, 7E, 7F and external providers |

## Implementation tasks

### Task 1: Define closed academic read contracts

**Files:**

- Modify `src/aaos/web/contracts.py`.
- Create `tests/test_phase7_academic_contracts.py`.

**Interfaces:**

- Add frozen, strict `AssignmentSummary` with exactly `id: int`, `course_id: int`, `title: str`, `description: str | None`, `due_at: datetime | None`, and `updated_at: datetime | None`.
- Add frozen, strict `AssignmentListResponse(assignments: tuple[AssignmentSummary, ...])`.
- Add closed enums `DocumentIngestionStatusView`, `DocumentExtractionStatusView`, `DocumentIndexReadinessView`, and `DocumentIndexCoverageView`, with values derived only from the existing trusted status vocabulary; use optional fields (`None`) for absent persisted rows rather than inventing an unknown/unavailable status.
- Add frozen, strict `DocumentSummary` with exactly `id: int`, `course_id: int`, `title: str`, `ingestion_status: DocumentIngestionStatusView`, `extraction_status: DocumentExtractionStatusView | None`, `page_count: int | None`, `index_readiness: DocumentIndexReadinessView | None`, `index_coverage: DocumentIndexCoverageView | None`, and `updated_at: datetime`.
- Define the browser enums as exact projections of existing persisted values: `DocumentIngestionStatusView = NEVER_ATTEMPTED | READY | FAILED`; `DocumentExtractionStatusView = SUCCESS | PARTIAL | NO_TEXT`; `DocumentIndexReadinessView = PREPARING | READY | NOT_SEARCHABLE | FAILED`; and `DocumentIndexCoverageView = FULL | PARTIAL | NONE`. `None` means the corresponding persisted row does not exist; it is absence, not an invented status.
- Add frozen, strict `DocumentListResponse(documents: tuple[DocumentSummary, ...])`.

**TDD steps:**

1. RED: assert exact field sets, strict type rejection, `extra="forbid"`, immutable instances, and rejection/absence of source IDs, storage keys, filesystem paths, SQL, tracebacks, provider payloads, and arbitrary dictionaries. Include security regressions proving browser contracts cannot carry a write/approval/tool field.
2. GREEN: add only the contracts and bounded enums; keep existing course contracts unchanged.
3. Run `uv run pytest tests/test_phase7_academic_contracts.py tests/test_phase7_courses_api.py -q`, then `uv run mypy src/aaos`.
4. Review the public model schema for accidental authority or secret-bearing fields and commit `feat(phase7c): define academic read contracts`.

**Acceptance invariant:** A response object can be constructed only from the explicit safe projection fields; parsing untrusted extra fields cannot create a new capability or reveal persistence internals.

### Task 2: Extend the trusted academic read service with course detail and assignments

**Files:**

- Modify `src/aaos/web/academic.py`, `src/aaos/web/routes.py`, and `src/aaos/main.py`.
- Extend `tests/test_phase7_courses_service.py` and `tests/test_phase7_courses_api.py`.
- Create `tests/test_phase7_assignments_api.py`.

**Interfaces:**

- Add `AcademicReadService.get_course(course_id: int) -> CourseSummary`; return the existing stable not-found error for absent/inactive courses.
- Add `AcademicReadService.list_assignments(course_id: int | None = None) -> tuple[AssignmentSummary, ...]`; include active assignments only, filter by active course when a course is supplied, normalize timestamps to UTC, and order by due time ascending with null due dates last and stable ID tie-breaking.
- Define `AcademicReader` with `list_courses()`, `get_course()`, and `list_assignments()` methods. Preserve the existing `create_api_v1_router(course_reader)` call form and add only keyword-only `academic_reader: AcademicReader | None = None`; the production composition root supplies the reader, while legacy course-only callers retain the existing course route behavior. Document-reader wiring is introduced only by Task 3.
- Add `GET /api/v1/courses/{course_id}` and `GET /api/v1/assignments?course_id=<positive integer>` with explicit response models and stable `COURSE_NOT_FOUND`/validation errors.

**TDD steps:**

1. RED: add service/API fixtures for active and inactive courses, missing IDs, assignment ordering, null due dates, optional course filtering, malformed query values, and deterministic error envelopes. A supplied `course_id` must return the same non-leaking `COURSE_NOT_FOUND` response for a missing or inactive course; an omitted filter lists only active-course assignments. Spy that route handling performs no writes and does not call RAG/model code.
2. GREEN: extend the existing SQLAlchemy read service and router wiring only; keep SQL construction in the service, not route functions.
3. Run focused course and assignment tests, OpenAPI tests, and `uv run pytest tests/test_phase7_courses_service.py tests/test_phase7_courses_api.py tests/test_phase7_assignments_api.py tests/test_phase7_openapi_contract.py -q`.
4. Run Ruff/mypy for changed backend files and commit `feat(phase7c): add course detail and assignment reads`.

**Acceptance invariants:** Only active trusted rows are projected; ordering is stable; a missing/inactive course cannot leak whether hidden rows exist; route code has no mutation or provider authority.

### Task 3: Add a read-only document readiness projection

**Files:**

- Create `src/aaos/web/documents.py`.
- Modify `src/aaos/web/routes.py` and `src/aaos/main.py`.
- Create `tests/test_phase7_documents_service.py` and `tests/test_phase7_documents_api.py`.

**Interfaces:**

- Add `DocumentReadService.list_documents(course_id: int | None = None) -> tuple[DocumentSummary, ...]` and `get_document(document_id: int) -> DocumentSummary`.
- Define `DocumentReader` with those two methods and extend `create_api_v1_router` with keyword-only `document_reader: DocumentReader | None = None`. Register document routes only when this trusted reader is supplied; `create_app` always supplies it after constructing the lazy/default reader pair, so production OpenAPI contains the complete 7C surface while direct legacy router callers remain compatible.
- Add `GET /api/v1/documents?course_id=<positive integer>` and `GET /api/v1/documents/{document_id}` with explicit `DocumentListResponse`/`DocumentSummary` models.
- `DocumentSummary.course_id` is an `int` because `documents.remote_file_id` is non-null and foreign-keys `remote_files.id`, while `remote_files.course_id` is non-null and foreign-keys `courses.id`; every persisted document is therefore course-scoped. The service joins through those two relationships and filters both the course and remote file to `is_active = true`.
- Implement a bounded SELECT-only projection over the document, latest-version, remote-file/course, and active-generation/index rows. `page_count` comes only from the latest `DocumentVersionORM.page_count`; it is `None` when no version exists. `updated_at` comes only from `DocumentORM.updated_at`, never from ingestion/index timestamps.

**Persisted-state to browser-state mapping:**

| Persisted condition | `ingestion_status` | `extraction_status` | `index_readiness` | `index_coverage` |
| --- | --- | --- | --- | --- |
| `DocumentORM` row, no latest version | exact `NEVER_ATTEMPTED`, `READY`, or `FAILED` value | `None` | `None` | `None` |
| Latest version exists | exact `DocumentORM.ingestion_status` | exact latest `DocumentVersionORM.extraction_status` (`SUCCESS`, `PARTIAL`, or `NO_TEXT`) | determined below | determined below |
| No `ACTIVE` `RagIndexGenerationORM` | unchanged | unchanged | `None` | `None` |
| Only `RETIRED`/`BUILDING` generations exist | unchanged | unchanged | `None` | `None` |
| Active generation has no row for this latest version | unchanged | unchanged | `None` | `None` |
| Active row `PREPARING` | unchanged | unchanged | `PREPARING` | `None` |
| Active row `FAILED` | unchanged | unchanged | `FAILED` | `None` |
| Active row `NOT_SEARCHABLE` | unchanged | unchanged | `NOT_SEARCHABLE` | exact persisted `NONE` |
| Active row `READY` with `FULL` or `PARTIAL` coverage | unchanged | unchanged | `READY` | exact persisted `FULL` or `PARTIAL` |

The precedence is: active-course/remote-file filtering, latest document version, active generation only, then active index status before coverage. A retired or building generation never overrides an active generation; a missing active generation/index is absence (`None`), not a new status. A persisted `READY`/`NONE` combination is inconsistent with the existing Phase 4 repository contract and must produce the stable safe database/projection error rather than be silently relabeled. No browser enum value is invented.

**TDD steps:**

1. RED: cover every row of the mapping table, active/retired/building generation precedence, no-active-index handling, inactive-parent filtering, missing IDs, deterministic title/ID order, safe projection, and a spy proving no repository write, model, embedding, Chroma, FTS, or network construction occurs. A supplied `course_id` must return the same non-leaking `COURSE_NOT_FOUND` response for a missing or inactive course; an omitted filter lists documents only for active courses/files.
2. GREEN: implement the read service with SQLAlchemy SELECT-only queries and the existing error boundary. Do not call `DocumentRepository`, ingestion services, `RagService`, or Phase 4 model adapters.
3. Run focused document tests, OpenAPI tests, and relevant academic persistence regressions.
4. Run Ruff/mypy and commit `feat(phase7c): expose document readiness reads`.

**Acceptance invariants:** A document read cannot mutate ingestion/index state or expose local paths, storage keys, source IDs, provider metadata, page content, or raw exceptions. Retired/partial/failed states remain truthful and safe.

### Task 4: Generate the API contract and add typed frontend query clients

**Files:**

- Regenerate only `frontend/openapi/aaos-v1.json` and `frontend/types/api.generated.ts` using the repository exporter.
- Modify `frontend/lib/query/keys.ts`.
- Create `frontend/lib/api/academic.ts`, `frontend/lib/query/academic.ts`, `frontend/tests/academic-api.test.ts`, and `frontend/tests/academic-query.test.ts`.

**Interfaces:**

- Add `fetchCourse(courseId: number, signal?: AbortSignal): Promise<CourseSummary>`.
- Add `fetchAssignments(courseId?: number, signal?: AbortSignal): Promise<AssignmentListResponse>`.
- Add `fetchDocuments(courseId?: number, signal?: AbortSignal): Promise<DocumentListResponse>`.
- Add `fetchDocument(documentId: number, signal?: AbortSignal): Promise<DocumentSummary>`.
- Add stable query keys `academicKeys.course(courseId)`, `academicKeys.assignments(courseId | undefined)`, `academicKeys.documents(courseId | undefined)`, and `academicKeys.document(documentId)` while preserving the existing course key compatibility. Every fetch helper has the corresponding query-options factory: `courseQueryOptions`, `assignmentsQueryOptions`, `documentsQueryOptions`, and `documentQueryOptions`.
- Frontend navigation routes are `/courses/<positive integer>`, `/assignments?course_id=<positive integer>`, `/documents?course_id=<positive integer>`, and `/documents/<positive integer>`; navigation components use these non-`/api` routes. Client-side navigation preserves positive-integer validation and bookmarkable `course_id` parameters.
- Browser API fetch helpers call only the same-origin FastAPI paths `GET /api/v1/courses/<positive integer>`, `GET /api/v1/assignments?course_id=<positive integer>`, `GET /api/v1/documents?course_id=<positive integer>`, and `GET /api/v1/documents/<positive integer>`. Unfiltered assignment/document API calls omit `course_id`. `fetchCourse()`, `fetchAssignments()`, `fetchDocuments()`, and `fetchDocument()` never call the non-`/api` navigation routes.
- Export typed TanStack Query option factories for each read; all API requests remain relative same-origin GETs with `Accept: application/json`, `AbortSignal` support, and the existing safe error normalization. Query-key behavior remains unchanged.

**TDD steps:**

1. RED: assert exact relative URLs, GET-only method, Accept header, query encoding, abort propagation, safe non-2xx errors, and generated types matching backend schemas.
2. GREEN: add clients/options and regenerate OpenAPI/types with `python scripts/export_openapi.py` through the repository workflow.
3. Run frontend API/query tests, `npm run check:api-types`, and frontend typecheck.
4. Commit `feat(phase7c): add typed academic query clients` with no hand-edited generated drift.

**Acceptance invariant:** The browser can read only the declared academic endpoints; no client helper can send a mutation, pass provider credentials, or construct a tool/approval request.

### Task 5: Build course list and detail read views

**Files:**

- Create the approved course route/view files under `frontend/app/courses/`, including the list route and `[courseId]` detail route.
- Create `frontend/app/courses/page.tsx` and `frontend/app/courses/[courseId]/page.tsx`.
- Create `frontend/components/aaos/course-card.tsx`, `frontend/components/aaos/course-list-view.tsx`, and `frontend/components/aaos/course-detail-view.tsx`.
- Create `frontend/tests/course-views.test.tsx`.

**Interfaces:**

- `CourseCard` receives only `CourseSummary` and renders title/code/status-safe fields.
- `CourseListView` consumes the course query options and renders loading, empty, success, and error states.
- `CourseDetailView` consumes `fetchCourse` and links to the assignment/document read routes using validated numeric IDs.

**TDD steps:**

1. RED: test loading, real empty state, success ordering, safe error state, not-found state, accessible headings/links, and absence of fabricated course/metric/content values.
2. GREEN: compose existing `AppShell`, `PageHeader`, `SectionCard`, and accepted AAOS tokens; do not change unrelated navigation, spacing, colors, or shell behavior.
3. Run the focused course component tests plus existing app-shell/navigation/design-system regressions.
4. Run frontend lint/typecheck and commit `feat(phase7c): add course read views`.

**Acceptance invariant:** Course UI is a truthful projection of the API response and never invents a course, schedule, metric, or completion state.

### Task 6: Add the assignment read view and deterministic due buckets

**Files:**

- Create `frontend/app/assignments/page.tsx`.
- Create `frontend/components/aaos/assignment-list.tsx` and `frontend/components/aaos/assignment-row.tsx`.
- Create `frontend/lib/assignments/due-buckets.ts`, `frontend/lib/query/course-filter.ts`, `frontend/tests/due-buckets.test.ts`, `frontend/tests/course-filter.test.ts`, and `frontend/tests/assignment-view.test.tsx`.

**Interfaces:**

- Define `AssignmentDueBucket = "OVERDUE" | "DUE_SOON" | "THIS_WEEK" | "LATER" | "NO_DUE_DATE"`. `OVERDUE` is required so an already-past assignment is never mislabeled as upcoming; this is presentation-only and does not create a task/completion authority.
- Define `bucketAssignment(dueAt: string | null, now: Date): AssignmentDueBucket` with both inputs interpreted as ISO timestamps and normalized to UTC. `now` is always an explicit caller argument: component tests inject a fixed UTC `Date`, and the page obtains one request-local `now` at render/query projection time rather than reading the clock inside the pure helper.
- Exact boundaries: `NO_DUE_DATE` applies only to `null`; `OVERDUE` applies when `due_at < now`; let `week_end` be the next Monday at 00:00:00 UTC after `now`, and `soon_end = min(now + 48 hours, week_end)`. `DUE_SOON` is `now <= due_at < soon_end`; `THIS_WEEK` is `soon_end <= due_at < week_end` (empty when `soon_end == week_end`); `LATER` is `due_at >= week_end`. Equality at each boundary belongs to the later interval as written. Invalid timestamps are rejected by the API contract and cannot reach this helper.
- Define `parseCourseIdParam(value: string | null): { kind: "ABSENT" } | { kind: "VALID"; courseId: number } | { kind: "INVALID" }` in `frontend/lib/query/course-filter.ts`; `ABSENT` is only a missing parameter, `VALID` requires an ASCII decimal positive finite integer, and every other value is `INVALID`.
- `AssignmentList` consumes `AssignmentListResponse` plus the existing all-active-course query. It builds a read-only `Map<number, CourseSummary>` keyed by `course.id`; deterministic course lookup is numeric-ID keyed and does not depend on response order. `AssignmentRow` receives either `{ kind: "AVAILABLE"; title: string; code: string | null }` from the map or `{ kind: "UNAVAILABLE" }`, which renders the fixed safe label `Course unavailable`. It never invents a title/code and does not duplicate course metadata into `AssignmentSummary`.
- `frontend/app/assignments/page.tsx` reads `course_id` from URL search parameters, passes a `VALID` ID to `assignmentsQueryOptions(courseId)`, and passes no ID when the parameter is `ABSENT`. For `INVALID`, it makes no request and renders a bounded validation state. A backend `COURSE_NOT_FOUND` response for a missing/inactive course renders the stable not-found state. The current search string is preserved in links and navigation, so `/courses/<id>` links to `/assignments?course_id=<id>` remain bookmarkable and directly navigable.

**TDD steps:**

1. RED: test fixed-UTC `now` injection, exact 48-hour/week boundaries, Monday rollover, overdue assignments, null due dates, invalid/unsupported date handling, deterministic row order, matching and unavailable course metadata, no fabricated course title, `ABSENT`/`VALID`/`INVALID` query parsing, no request for malformed/non-positive IDs, preserved `course_id` URLs, backend `COURSE_NOT_FOUND`, loading/empty/success/error states, accessible labels, and absence of completion/task mutation controls.
2. GREEN: implement pure bucket logic and read-only components with local UI state only.
3. Run focused assignment tests, existing frontend tests, lint, and typecheck.
4. Commit `feat(phase7c): add assignment read view`.

**Acceptance invariant:** Due grouping is a presentation rule over trusted timestamps; it cannot trigger task creation, completion, reminders, or external writes.

### Task 7: Add documents/readiness views and citation-navigation prerequisites

**Files:**

- Create `frontend/app/documents/page.tsx` and `frontend/app/documents/[documentId]/page.tsx`.
- Create `frontend/components/aaos/document-list.tsx`, `document-row.tsx`, and `document-readiness.tsx`.
- Create `frontend/components/aaos/document-detail-view.tsx`, `frontend/lib/documents/source-links.ts`, `frontend/tests/document-views.test.tsx`, and `frontend/tests/source-links.test.ts`.

**Interfaces:**

- Define `documentHref(documentId: number): string`; accept only a positive finite integer and return `/documents/<id>`. 7C does not fetch or display page content, so page-specific citation URLs are deferred to 7D; no `page` query parameter is defined in this phase.
- Components render title, bounded status labels, page count, and index/readiness state. Status must not be communicated by color alone.
- Detail view must use `fetchDocument` and display a truthful safe not-found/error state; it must not claim to show page content that 7C does not fetch.
- `frontend/app/documents/page.tsx` reads `course_id` from URL search parameters using the shared `parseCourseIdParam`. `ABSENT` calls `documentsQueryOptions()` without a filter; `VALID` calls `documentsQueryOptions(courseId)`; `INVALID` makes no request and renders a bounded validation state. A backend `COURSE_NOT_FOUND` response for a missing/inactive course renders the stable not-found state. The original search string is preserved so `/courses/<id>` links to `/documents?course_id=<id>` remain bookmarkable and directly navigable.

**TDD steps:**

1. RED: test deterministic document ordering, every readiness state, `ABSENT`/`VALID`/`INVALID` course-filter behavior, no request for malformed/non-positive IDs, preserved `course_id` URLs, backend `COURSE_NOT_FOUND`, loading/empty/success/error/not-found UI, positive-ID-only `documentHref`, and rejection/absence of page content, local path, storage key, source/provider identifier, citation text parsing, and RAG calls.
2. GREEN: implement read-only document views and the bounded link helper. Do not add citation parsing; this task only establishes a safe destination for later validated citations.
3. Run focused document frontend tests plus API/query regressions, lint, and typecheck.
4. Commit `feat(phase7c): add document readiness views`.

**Acceptance invariant:** A future citation may navigate only through a validated document destination URL; current UI has no authority to manufacture or interpret grounded evidence.

### Task 8: Compose the academic overview

**Files:**

- Modify `frontend/app/page.tsx` only; preserve the accepted `frontend/components/aaos/course-proof-card.tsx` unchanged.
- Create `frontend/components/aaos/overview-academic-summary.tsx`, `overview-assignment-panel.tsx`, and `overview-document-panel.tsx`.
- Create `frontend/tests/overview-academic-summary.test.tsx`.

**Interfaces:**

- `OverviewAcademicSummary` consumes the existing course, assignment, and document query option factories; it may compose multiple GET results but must not add a dedicated aggregate API.
- Each panel owns loading, empty, success, and error rendering so one unavailable read does not become fabricated global metrics.
- Preserve the accepted empty-course state and the existing SectionCard radius/shadow tokens.

**TDD steps:**

1. RED: assert independent query states, truthful counts derived only from returned rows, no fake schedules/analytics/metrics, accessible links to the three read experiences, and no writes/model/RAG/provider calls.
2. GREEN: compose the panels into the existing overview without redesigning unrelated shell or navigation.
3. Run all focused overview/frontend tests, `npm run lint`, `npm run typecheck`, `npm run build`, and `npm run check:api-types`.
4. Commit `feat(phase7c): compose academic overview reads`.

**Acceptance invariant:** Overview is a view composition, not a new authority or analytics source; unsupported data remains explicitly empty/unavailable.

### Task 9: Security, regression, and production-browser acceptance gate

**Files:**

- No production or test files are created by this task; all implementation and genuine RED/GREEN security assertions are added in Tasks 1–8.
- Do not modify `src/aaos/persistence/*`, `tests/phase6a_evaluation/manifest.py`, Phase 4/5/6 modules, or the frozen browser acceptance database.

**Verification/security checks:**

1. Verify the security assertions already introduced with their relevant contracts/services/views: all 7C routes are GET-only; no route/repository path mutates rows; untrusted query/path values cannot select arbitrary tables/files/SQL/provider calls; projection responses omit secrets, paths, storage keys, raw SQL, tracebacks, source/provider IDs, page content, and raw RAG output; C1–C9 and Phase 6 approval boundaries remain unreachable from browser reads.
2. Run focused 7C tests, all Phase 7 tests, relevant Phase 4/5/6 regressions, and canonical repository checks: `uv run pytest -q`, `uv run ruff check .`, `uv run mypy src`, `uv run mypy src/aaos scripts/export_openapi.py`, `uv lock --check`, and `git diff --check`.
3. Run frontend `npm test -- --run`, `npm run lint`, `npm run typecheck`, `npm run build`, and `npm run check:api-types`.

**Browser acceptance protocol:**

- Run canonical repository tests while `data/database/aaos.db` is absent, so the repository suite cannot accidentally consume the disposable browser database. Only after those gates pass, create a new disposable worktree database through the existing Alembic path and verify the expected revision.
- Populate only this deterministic, isolated browser fixture: active course `101` titled `AAOS-101`; inactive course `199`; active-course assignments representing `OVERDUE`, `DUE_SOON`, `THIS_WEEK`, `LATER`, and `NO_DUE_DATE`; one inactive assignment and one assignment under course `199`; and active remote-file/document rows covering `NEVER_ATTEMPTED`, `READY`+`SUCCESS`, `READY`+`PARTIAL`, `READY`+`NO_TEXT`, and `FAILED` ingestion. Add active-index rows covering `PREPARING`, `READY`+`FULL`, `READY`+`PARTIAL`, `NOT_SEARCHABLE`+`NONE`, and `FAILED`, plus one document with no active generation/index. The inactive course/file/assignment rows must be present only to prove filtering and must never appear in browser responses.
- Freeze and hash this fixture manifest (row identities, safe titles, statuses, timestamps, and expected visible counts) before browser evaluation. The manifest is fixed before any screenshot/result is observed; it is independent of the Phase 6A/6B heldout manifest, and Phase 7C has zero model calibration, model-routing score, or heldout execution.
- Start FastAPI on `127.0.0.1:8000` and the already-built frontend with production `next start` on `127.0.0.1:3000`; never use `next dev` for final evidence.
- Verify `/health`, direct `/api/v1/courses`, same-origin proxy responses, course/assignment/document endpoints, and the frontend root before screenshots.
- Capture production evidence at 1440, 1280, 768, and 390 widths. Check success states, the real empty state using a separate empty fixture/component test, course/detail/assignment/document navigation, drawer keyboard/focus behavior, no horizontal overflow, safe readiness labels, no Next development badge, no fake content, and SectionCard tokens. Loading and error states are verified deterministically in the Task 5–8 component tests with mocked query states; a healthy production backend is not expected to fail naturally, and no browser interception is needed.
- Persist screenshots and machine-readable endpoint/DOM evidence before cleanup. Stop both servers, then delete only the disposable worktree database; never touch `C:\Users\aljaf\aaos\data` or any normal production database. Recheck fixture/evidence hashes as applicable and require a clean tracked tree.

**Independent review:** Review least authority, safe projection, deterministic ordering, no hidden writes, no model/RAG/provider initialization, Phase 6 approval boundary preservation, frontend proxy boundary, and deferred-phase leakage. Resolve Critical/Important findings in new commits and rerun affected checks.

**Commit policy:** Tasks 1–8 create the reviewable implementation commits listed above. Task 9 creates no commit when the tree is clean; human acceptance/governance recording occurs separately after review.

## Required invariant-to-task coverage

| Spec invariant | Plan coverage |
| --- | --- |
| Strict browser-safe contracts and no raw ORM/secrets/paths | Tasks 1, 3, 9 |
| Deterministic course/assignment/document ordering | Tasks 2, 3, 9 |
| Active-course/assignment/document visibility rules | Tasks 2, 3 |
| GET-only, no writes, no external authority | Tasks 2, 3, 4, 9 |
| Ingestion/extraction/index readiness truthful projection | Tasks 1, 3, 7 |
| No model/RAG execution or raw RAG output | Tasks 3, 7, 9 |
| Loading/empty/success/error browser states | Tasks 5, 6, 7, 8 |
| Overview composition without invented metrics | Task 8 |
| Safe citation destination prerequisites | Task 7 |
| C1–C9/Phase 6 authority preservation | Tasks 2, 3, 4, 9 |
| OpenAPI/generated client drift control | Task 4 and Task 9 |
| Production runtime and responsive acceptance | Task 9 |
| Frozen Phase 4/5/6 and no 7D–7F leakage | Global constraints and Task 9 |

## Plan self-review and execution order

Before implementation, confirm the plan contains no unfinished-marker text, no unspecified file or interface, no route/projection type mismatch, no adapter or remote I/O inside a read service, and no task that depends on a later task's uncommitted code. Tasks 1–8 each have a focused RED test, smallest GREEN implementation, regression commands, and one commit boundary; Task 9 is verification-only and intentionally creates no RED/GREEN cycle or commit. The implementation order is Task 1 → Task 2 → Task 3 → Task 4 → Task 5 → Task 6 → Task 7 → Task 8 → Task 9. Human review is required after this plan and again after the implementation commits; do not implement or tag Phase 7C from this document alone.
