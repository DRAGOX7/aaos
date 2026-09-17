# Phase 7D Ask AAOS RAG Experience Implementation Plan

> **Execution note:** This plan is executed task-by-task. Each task follows
> RED → GREEN → focused regression → review → one commit. Phase 7E and all
> mutation/external-write behavior remain out of scope.

## Goal

Expose the existing validated Phase 4 local RAG service through a narrow
browser-safe Ask AAOS experience. The browser may submit a bounded question
and either global or active-course scope. Trusted AAOS code constructs the
existing `RagQuery`, invokes `LocalRagService.ask()`, and projects only
validated final answer data and safe citation metadata. Insufficient evidence
is a successful typed result; operational failures use the existing stable
browser error envelope. No model, retrieval, provider, filesystem, SQL, task,
approval, or external-write authority is exposed to the browser.

## Existing seams and fixed decisions

- Backend API namespace is `/api/v1`; the canonical RAG route is
  `POST /api/v1/rag/queries`.
- Existing Phase 4 `RagQuery`, `CourseScope`, `GlobalScope`,
  `RagAnswerStatus`, `RenderedRagResponse`, and `LocalRagService` remain the
  source of RAG semantics. The browser contract does not reproduce internal
  retrieval knobs, prompts, provider identity, generation IDs, content
  revisions, evidence text, or raw `document_version_id` values.
- Only `ANSWER`, `PARTIAL_ANSWER`, and `INSUFFICIENT_EVIDENCE` are valid RAG
  result statuses. The first two are rendered only after Phase 4 validation;
  insufficient evidence renders the existing safe refusal/result text and no
  citations.
- Citation mapping is trusted backend work: each validated citation's
  `document_version_id` is joined through the existing document tables to the
  active public document ID/title. Missing or inconsistent mapping fails
  closed as a sanitized RAG operational error. Page bounds come only from
  Phase 4 citation metadata. The browser route is `/documents/{document_id}`;
  7D does not invent a page query parameter.
- Production RAG composition is lazy. Importing or starting FastAPI does not
  load models, contact LM Studio, rebuild indexes, or migrate the database.
  The existing Phase 4 runtime owns model/embedding/gateway lifecycle and is
  closed by application shutdown.
- Phase 7D contains no SSE, activity persistence, task/approval mutation,
  ingestion, indexing/rebuild, upload, Calendar/GitHub/MCP write, or Phase 7E
  functionality.

## Task 1 — Browser-safe RAG contracts and closed error code

**Files**

- `src/aaos/web/contracts.py`
- `src/aaos/web/errors.py` (only if the new typed error code needs a constant)
- `frontend/lib/api/errors.ts`
- `tests/test_phase7_rag_contracts.py`

**RED**

Add tests for exact frozen/strict/`extra="forbid"` Pydantic models:

- `RagScopeRequest` accepts only `global` or positive `course` scope;
- `RagQueryRequest` requires a bounded non-empty question and rejects
  retrieval knobs, prompts, provider/model identifiers, authorization fields,
  arbitrary extras, non-strict types, and non-positive course IDs;
- `RagCitation` contains only `document_id`, `document_title`, `page_start`,
  and `page_end` with positive bounded page values;
- `RagAnswerResponse` contains only `status`, `answer_text`, `language`, and
  the immutable citation tuple; insufficient evidence has an empty citation
  tuple;
- the result enum is exactly the existing three Phase 4 statuses;
- `RAG_UNAVAILABLE` is accepted only as the browser operational error code.

Run the focused test and capture the intended RED before adding production
contracts.

**GREEN**

Implement the minimum immutable browser projection types and the closed
frontend error-code mapping. Do not expose internal generation/content
revision IDs, source IDs, local paths, provider payloads, claims/evidence
text, SQL, exceptions, or authority fields.

**Verification and commit**

Run `uv run pytest tests/test_phase7_rag_contracts.py -q`, `uv run mypy
src/aaos`, `cd frontend; npm test -- --run tests/rag-contracts.test.ts`, and
`git diff --check`. Inspect the serialized allowlists and commit:

`feat(phase7d): define browser rag contracts`

## Task 2 — Trusted RAG read service and safe citation projection

**Files**

- `src/aaos/web/rag.py`
- `src/aaos/web/contracts.py` (only projection imports/types required by the
  service)
- `tests/test_phase7_rag_service.py`

**RED**

Use a fake `LocalRagService` returning deterministic validated
`RenderedRagResponse` values. Add failing tests proving:

- global and course requests become exactly `GlobalScope` and `CourseScope`;
- question text is passed unchanged after bounded contract validation;
- validated answer/partial results project safe answer text/language/citations;
- insufficient evidence projects a successful empty-citation result;
- citation mapping resolves document-version identity to active public
  document ID/title and preserves only trusted page bounds;
- missing/inactive/multiple mapping and internal operational errors become a
  bounded `RagOperationalError` without returning raw details;
- the service cannot accept browser retrieval knobs or choose a provider.

Run only this test module and capture RED.

**GREEN**

Add a small `RagReadService` with an `AcademicRagReader` protocol. Its public
method is `ask(request: RagQueryRequest) -> RagAnswerResponse`. It constructs
the Phase 4 query, awaits the injected `LocalRagService`, and performs a
read-only SQLAlchemy mapping join from `document_versions` through
`documents`/`remote_files` to active `documents` and safe title data. It never
calls the adapter directly, exposes raw evidence, or writes the database.

**Verification and commit**

Run `uv run pytest tests/test_phase7_rag_service.py -q`, the Phase 4 RAG
contract/service tests, `uv run ruff check src/aaos/web/rag.py`, `uv run mypy
src/aaos`, and `git diff --check`. Commit:

`feat(phase7d): add trusted browser rag reader`

## Task 3 — API route, error boundary, and lazy production composition

**Files**

- `src/aaos/web/routes.py`
- `src/aaos/main.py`
- `src/aaos/rag/factory.py` (new shared lazy Phase 4 composition helper)
- `tests/test_phase7_rag_api.py`
- `tests/test_phase7_lazy_rag_wiring.py`

**RED**

Add API tests with an injected fake reader for:

- `POST /api/v1/rag/queries` success, partial, and insufficient responses;
- malformed JSON, blank/oversized question, invalid scope, unknown fields,
  and non-positive course IDs yielding the stable `INVALID_REQUEST` envelope;
- injected operational failure yielding `503 RAG_UNAVAILABLE` with no raw
  Phase 4 code/message;
- no SQL construction in routes, no writes, no task/approval calls, and no
  provider/model fields in OpenAPI.

Add composition tests proving app import and `/health` do not instantiate the
RAG runtime; the runtime is built only on the first RAG request and is closed
once at shutdown.

**GREEN**

Add the route using a strict request model and the injected reader protocol.
Map only `RagOperationalError` to `Phase7ApiError(503, "RAG_UNAVAILABLE", ...)`
and let request validation use the existing `INVALID_REQUEST` handler. Add a
lazy default reader in `main.py`. Extract the exact CLI runtime configuration
(`RagIndexConfig`, BGE metadata, `resolve_rag_runtime_paths`, local LM Studio
gateway) into `src/aaos/rag/factory.py` so the web path and CLI share one
composition; the factory must not perform work until called and must preserve
the existing `Phase4RagRuntime` close ownership.

**Verification and commit**

Run focused API/wiring tests, `uv run pytest tests/test_phase7_openapi_contract.py
tests/test_phase7_rag_api.py -q`, `uv run python scripts/export_openapi.py`,
the repository API drift check, `uv run ruff check src/aaos/web src/aaos/main.py
src/aaos/rag/factory.py`, `uv run mypy src/aaos`, and `git diff --check`.
Commit:

`feat(phase7d): expose validated rag query api`

## Task 4 — Generated contract sync and typed frontend RAG client

**Files**

- `frontend/lib/api/rag.ts`
- `frontend/lib/query/rag.ts`
- `frontend/lib/query/keys.ts`
- generated OpenAPI TypeScript artifact(s) under the existing frontend API
  type path
- `frontend/tests/rag-api.test.ts`
- `frontend/tests/rag-query.test.ts`
- `frontend/tests/generated-api-contract.test.ts` only if existing drift
  coverage requires the new operation

**RED**

Add client/query tests proving:

- only same-origin `POST /api/v1/rag/queries` is called;
- JSON body contains only question and validated global/course scope;
- no provider/retrieval/authority fields can be supplied;
- `AbortSignal` is passed through;
- safe API errors normalize to `ApiError` with `RAG_UNAVAILABLE` or
  `INVALID_REQUEST` and never expose response text/raw payloads;
- query/mutation keys are distinct from academic list/detail keys and stable
  for equal request values.

Run the focused frontend tests and capture RED.

**GREEN**

Implement the hand-written fetch helper and a typed mutation/query helper
using the generated OpenAPI types. Keep frontend code on `/api/v1` paths only;
the Next.js route `/ask` is separate navigation, not an API path. Do not
cache unvalidated text or expose request IDs/raw payloads in the UI.

**Verification and commit**

Run focused tests, `npm run check:api-types`, `npm run typecheck`, `npm run
lint`, and `git diff --check`. Commit:

`feat(phase7d): add typed browser rag client`

## Task 5 — Ask AAOS page, scope selection, and state rendering

**Files**

- `frontend/app/ask/page.tsx`
- `frontend/components/aaos/ask-view.tsx`
- `frontend/components/aaos/ask-result.tsx`
- `frontend/components/aaos/ask-scope-select.tsx`
- `frontend/tests/ask-view.test.tsx`
- `frontend/tests/ask-page.test.tsx`

**RED**

Add component tests for:

- question required/bounded validation and no request for invalid input;
- global scope and active-course scope selection produce the exact closed
  request shape;
- loading state uses only safe bounded progress copy;
- answer and partial-answer states render trusted answer text and citations;
- insufficient evidence renders a distinct safe explanation, no citation
  claim, and no fabricated answer;
- `RAG_UNAVAILABLE`, invalid request, and unknown errors render bounded
  recovery copy without raw payloads;
- no task/approval/external-write controls or fake academic content appear.

Run focused tests and capture RED.

**GREEN**

Implement the `/ask` page using existing `AppShell`/`PageHeader` composition
and the typed RAG client. Scope selection may list only active course IDs and
safe titles from the existing course query. The page submits one bounded
request, renders only the validated response, and never performs a write or
directly accesses model/provider state.

**Verification and commit**

Run focused Ask tests, all frontend tests, typecheck, lint, production build,
API drift check, and `git diff --check`. Commit:

`feat(phase7d): add ask aaos browser experience`

## Task 6 — Citation source navigation and browser-safe error/security tests

**Files**

- `frontend/components/aaos/ask-citation.tsx`
- `frontend/lib/routing/document-links.ts`
- `tests/test_phase7_rag_security.py`
- `frontend/tests/ask-citation.test.tsx`
- `frontend/tests/browser-security.test.ts` only if the existing test seam
  needs one additional assertion

**RED**

Add tests proving:

- each citation links only to `/documents/{positive document_id}`;
- title/page labels are escaped by React and page bounds are rendered as
  bounded numbers;
- no `document_version_id`, source IDs, storage keys, paths, SQL, provider
  payloads, raw claim/evidence data, traceback, or credentials reach browser
  markup/logs;
- a missing source mapping is an error state, never a fabricated link;
- citations are not shown for insufficient evidence.

Run the focused tests and capture RED.

**GREEN**

Implement `documentHref(documentId: number)` using the existing positive-ID
route helper. Keep page numbers as display metadata only; do not append
`?page=` in 7D. Add backend security tests for forbidden projection fields,
provider/authority injection, no DB writes, and error sanitization.

**Verification and commit**

Run focused security/citation tests, full frontend suite, relevant backend RAG
tests, Ruff/mypy/typecheck/build/API drift, and `git diff --check`. Commit:

`feat(phase7d): render safe rag citations`

## Task 7 — Deterministic acceptance fixtures and production runtime harness

**Files**

- `tests/fixtures/phase7d_rag_acceptance.py`
- `tests/test_phase7d_acceptance.py`
- `tests/test_phase7d_scope_audit.py`
- `frontend/tests/ask-browser-acceptance.test.ts` (deterministic browser
  contract coverage only)
- existing acceptance harness files only where a new read-only route must be
  observed

**RED**

Add deterministic fake-service acceptance tests for answered, partial,
insufficient, invalid scope, operational failure, citation mapping, and
forbidden-field probes. Add a fixture manifest with no external data and no
model calls; it is synthetic test input, not production seed data.

**GREEN**

Implement the fixture builders and tests. Freeze the fixture manifest before
any acceptance assertion; after freeze no fixture row or expected outcome may
change based on observed results. The unit harness must assert zero model
calls, zero RAG runtime calls for fake-only tests, zero writes, and no Phase
6/7E/7F symbols/routes. Production acceptance uses the existing isolated
disposable DB procedure and configured local Phase 4 runtime only; it may
stop with `HUMAN_INTERVENTION_REQUIRED` if local models/indexes are absent or
if any genuine RAG semantic/lifecycle defect appears.

**Verification and commit**

Run all Phase 7D backend/frontend tests, all Phase 4/5/6 regressions, and
static gates. Commit:

`test(phase7d): add rag acceptance fixtures`

## Task 8 — Fresh production browser acceptance and independent review

No production behavior is added in this task; this is verification-only.

1. Create/migrate only the disposable Phase 7 acceptance DB through Alembic;
   never touch the normal AAOS DB and never seed fake user-facing data.
2. Verify a compliant read-only index/fixture or stop if the existing local
   RAG runtime cannot prove Phase 4 semantics without a new migration,
   external write, or model download.
3. Run FastAPI on `127.0.0.1:8000` and production `next start` on
   `127.0.0.1:3000`; never use `next dev`.
4. Check `/health`, direct `/api/v1/rag/queries`, same-origin proxy behavior,
   and frontend `/ask` at 1440/1280/768/390.
5. Capture persisted screenshots and JSON evidence with hashes. Verify no
   Next dev badge, horizontal overflow, console errors, raw RAG fields,
   sensitive data, fake content, or unvalidated answer text.
6. Exercise global/course scope, answered/partial/insufficient/operational
   error states where the real frozen runtime can provide them. Confirm
   `RAG_INSUFFICIENT_EVIDENCE` stays a typed result, not a 5xx.
7. Stop servers, delete only the disposable DB, check ports are inactive, and
   leave the tracked worktree clean. Do not mark 7D human accepted or create
   `phase-07-passed`.

## Cross-task acceptance invariants

- Browser authority is limited to bounded question plus global/course scope.
- No browser input selects provider, model, retrieval knob, prompt, tool,
  permission, runtime policy, task, approval, or external write.
- Only validated Phase 4 final answers are returned/rendered; insufficient
  evidence is explicit and citation-free.
- Citation IDs are mapped server-side to active public document IDs/titles;
  no internal source/version/path/SQL/provider data crosses the boundary.
- RAG operational failures are sanitized to `RAG_UNAVAILABLE`; no raw
  exceptions or provider payloads are returned.
- Imports/startup/health remain side-effect-free with lazy runtime creation;
  shutdown closes the existing runtime owner exactly once.
- No new migration, external write, task/approval mutation, model download,
  Phase 7E/7F behavior, or Phase 4 semantic redesign is introduced.
- The final gates include focused tests, full pytest, frontend tests, OpenAPI
  drift, Ruff, mypy, typecheck, lint, build, lock/diff checks, import smoke,
  scope/security audit, and production browser evidence.

