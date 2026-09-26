# Phase 7A Browser/API Contract Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish the Phase 7 browser trust boundary and prove it end-to-end with a read-only `GET /api/v1/courses` contract, safe API errors/request IDs, OpenAPI export, and generated TypeScript types.

**Architecture:** FastAPI remains the trusted browser boundary. Phase 7 adds explicit Pydantic browser contracts, a focused read-only academic projection service, and a versioned `/api/v1` router; the route layer never serializes ORM rows directly and never queries SQLAlchemy directly. OpenAPI is exported deterministically and `openapi-typescript` generates the first frontend-facing TypeScript contract without scaffolding the full Next.js application yet.

**Tech Stack:** Python 3.10+, FastAPI, Pydantic, SQLAlchemy, pytest, Ruff, mypy, uv, Node/npm, `openapi-typescript`.

**Spec:** `docs/superpowers/specs/2026-09-16-phase-7-dashboard-operational-visibility-design.md`

## Global Constraints

- Baseline is the human-approved Phase 7 design at commit `095979f` on branch `phase-07-dashboard`.
- Phase 7 is local-first and single-user, but browser contracts must remain future-authentication-ready.
- Browser state is never authoritative state.
- Internal AAOS models are never browser API models.
- The browser receives zero credentials, Moodle cookies, OAuth tokens, provider secrets, authorization headers, local database paths, filesystem secrets, raw internal events, raw exception text, or unvalidated RAG generation.
- `/health` remains the existing process-liveness contract and must stay semantically unchanged.
- All product routes live under `/api/v1`.
- API timestamps are timezone-aware UTC ISO-8601 values.
- Course list exposes active courses only with deterministic ordering: code/title with a stable ID tie-breaker.
- The first course browser contract contains exactly `id`, `title`, `code`, `description`, `updated_at`.
- `source_system` and `source_id` are intentionally excluded from the browser contract.
- No route layer SQLAlchemy queries; the focused academic read service owns reads/projections.
- No production code is written before a failing test for that behavior has been observed.
- Generated OpenAPI/TypeScript artifacts are committed and verification fails on drift.

---

## File Structure for 7A

**Create:**

- `src/aaos/web/__init__.py` — Phase 7 web-boundary package marker and public exports only.
- `src/aaos/web/contracts.py` — browser-safe Pydantic contracts for 7A.
- `src/aaos/web/errors.py` — stable error envelope, API error type, request-ID middleware/handlers.
- `src/aaos/web/academic.py` — read-only academic projection service; owns SQLAlchemy course reads.
- `src/aaos/web/routes.py` — `/api/v1` router and course HTTP endpoint; contains no direct SQL query.
- `tests/test_phase7_web_errors.py` — exact error/request-ID boundary tests.
- `tests/test_phase7_courses_service.py` — academic read-service tests.
- `tests/test_phase7_courses_api.py` — HTTP contract/leakage/ordering tests.
- `tests/test_phase7_openapi_contract.py` — deterministic OpenAPI contract assertions.
- `scripts/export_openapi.py` — deterministic OpenAPI exporter.
- `frontend/package.json` — minimal Phase 7 frontend-contract workspace only; 7B will add Next.js.
- `frontend/package-lock.json` — npm lockfile.
- `frontend/openapi/aaos-v1.json` — committed deterministic OpenAPI snapshot.
- `frontend/types/api.generated.ts` — generated TypeScript API types.

**Modify:**

- `src/aaos/main.py` — install the Phase 7 router and safe browser error boundary while preserving `/health`.
- `.gitignore` only if Node artifacts are not already ignored; add `frontend/node_modules/` and no broader ignore rules.

No Phase 4/5/6 production modules are modified in 7A.

---

### Task 1: Freeze the browser-safe error envelope and request correlation boundary

**Files:**
- Create: `src/aaos/web/__init__.py`
- Create: `src/aaos/web/contracts.py`
- Create: `src/aaos/web/errors.py`
- Create: `tests/test_phase7_web_errors.py`
- Modify: `src/aaos/main.py`

**Interfaces:**
- Produces: `ApiErrorDetail(code: str, message: str, request_id: str)`
- Produces: `ApiErrorResponse(error: ApiErrorDetail)`
- Produces: `Phase7ApiError(status_code: int, code: str, message: str)`
- Produces: `install_error_boundary(application: FastAPI) -> None`
- Produces: `request_id_from(request: Request) -> str`
- Request IDs are application-owned lowercase UUID hex strings and are returned in both `error.request_id` and `X-Request-ID`.

- [ ] **Step 1: Write the failing safe-error tests**

Create `tests/test_phase7_web_errors.py` with focused tests equivalent to:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aaos.web.errors import Phase7ApiError, install_error_boundary


def _app() -> FastAPI:
    app = FastAPI()
    install_error_boundary(app)

    @app.get("/expected")
    async def expected() -> None:
        raise Phase7ApiError(503, "SYSTEM_UNAVAILABLE", "AAOS is temporarily unavailable.")

    @app.get("/unexpected")
    async def unexpected() -> None:
        raise RuntimeError(r"secret path C:\\Users\\example\\data\\credentials\\token.json")

    return app


def test_expected_api_error_has_closed_safe_shape_and_request_id() -> None:
    response = TestClient(_app(), raise_server_exceptions=False).get("/expected")
    assert response.status_code == 503
    payload = response.json()
    assert set(payload) == {"error"}
    assert set(payload["error"]) == {"code", "message", "request_id"}
    assert payload["error"]["code"] == "SYSTEM_UNAVAILABLE"
    assert payload["error"]["message"] == "AAOS is temporarily unavailable."
    assert len(payload["error"]["request_id"]) == 32
    assert response.headers["X-Request-ID"] == payload["error"]["request_id"]


def test_unexpected_exception_never_leaks_exception_text_or_local_path() -> None:
    response = TestClient(_app(), raise_server_exceptions=False).get("/unexpected")
    assert response.status_code == 500
    rendered = response.text
    assert "RuntimeError" not in rendered
    assert "Users" not in rendered
    assert "credentials" not in rendered
    payload = response.json()
    assert payload["error"]["code"] == "INTERNAL_ERROR"
    assert payload["error"]["message"] == "AAOS could not complete the request."
```

Also add a validation-error test using a small route with an integer path/query parameter and assert `422`, `INVALID_REQUEST`, the same exact error envelope, and no Pydantic internal error structure.

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```powershell
uv run pytest tests/test_phase7_web_errors.py -q
```

Expected: collection/import failure because `aaos.web.errors` does not exist yet. This is the required RED state.

- [ ] **Step 3: Implement the minimal error boundary**

Implement `src/aaos/web/contracts.py` with strict Pydantic response types and `src/aaos/web/errors.py` with:

```python
class Phase7ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None: ...


def install_error_boundary(application: FastAPI) -> None: ...


def request_id_from(request: Request) -> str: ...
```

Required behavior:

- middleware assigns `uuid.uuid4().hex` before request handling;
- every response receives `X-Request-ID`;
- `Phase7ApiError` maps to the stable envelope;
- `RequestValidationError` maps to `422 / INVALID_REQUEST` without echoing input values or raw validation detail;
- unhandled exceptions map to `500 / INTERNAL_ERROR` with the fixed safe message;
- the backend may log the exception server-side, but the response never contains exception text;
- use only application-owned fixed messages/codes at this boundary.

Call `install_error_boundary(application)` from `create_app()` in `src/aaos/main.py`; do not alter the existing `/health` response model or payload.

- [ ] **Step 4: Run the focused tests and verify GREEN**

```powershell
uv run pytest tests/test_phase7_web_errors.py -q
uv run pytest tests -q -k "health or phase7_web_errors"
```

Expected: focused tests pass; existing health behavior remains green.

- [ ] **Step 5: Run static checks for the touched backend files**

```powershell
uv run ruff check src/aaos/main.py src/aaos/web tests/test_phase7_web_errors.py
uv run mypy src/aaos/main.py src/aaos/web
```

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

```powershell
git add src/aaos/main.py src/aaos/web tests/test_phase7_web_errors.py
git commit -m "feat(phase7a): add safe browser error boundary"
```

---

### Task 2: Add the read-only course projection service

**Files:**
- Modify: `src/aaos/web/contracts.py`
- Create: `src/aaos/web/academic.py`
- Create: `tests/test_phase7_courses_service.py`

**Interfaces:**
- Produces: `CourseSummary(BaseModel)` with exactly `id`, `title`, `code`, `description`, `updated_at`.
- Produces: `CourseListResponse(BaseModel)` with exactly `courses: list[CourseSummary]`.
- Produces: `AcademicReadService(session_factory: sessionmaker[Session])`.
- Produces: `AcademicReadService.list_courses() -> tuple[CourseSummary, ...]`.
- Consumes: existing `CourseORM` and SQLAlchemy session factory.

- [ ] **Step 1: Write failing service tests**

Use a temporary SQLite database, create only the academic metadata, and insert active/inactive courses directly as test fixtures. The tests must assert real query behavior rather than mocks.

Required tests:

```python
def test_list_courses_returns_only_active_courses_in_deterministic_order(): ...


def test_list_courses_projects_only_browser_safe_fields(): ...


def test_list_courses_preserves_nullable_code_and_description(): ...


def test_list_courses_normalizes_updated_at_to_utc(): ...
```

Ordering fixture must prove these rules, including ties:

1. non-null course code before null code;
2. code ascending case-insensitively;
3. title ascending case-insensitively;
4. integer ID as final stable tie-breaker.

The projection-field test must assert:

```python
assert set(summary.model_dump()) == {
    "id",
    "title",
    "code",
    "description",
    "updated_at",
}
assert "source_system" not in summary.model_dump()
assert "source_id" not in summary.model_dump()
```

- [ ] **Step 2: Run service tests and verify RED**

```powershell
uv run pytest tests/test_phase7_courses_service.py -q
```

Expected: FAIL because `CourseSummary` / `AcademicReadService` do not exist.

- [ ] **Step 3: Implement the minimal contracts and service**

`CourseSummary` must use explicit fields only; do not enable generic ORM serialization.

`AcademicReadService.list_courses()` must:

- open a short-lived session from the injected session factory;
- execute a `select(CourseORM)` filtered by `CourseORM.is_active.is_(True)`;
- use explicit SQL ordering matching the frozen rule;
- construct `CourseSummary` explicitly field-by-field;
- return an immutable tuple;
- perform no commit, flush, insert, update, or delete.

For null-code ordering, use an explicit expression such as `CourseORM.code.is_(None)` before the case-insensitive code sort so non-null values sort first across SQLite.

- [ ] **Step 4: Run service tests and verify GREEN**

```powershell
uv run pytest tests/test_phase7_courses_service.py -q
```

Expected: PASS.

- [ ] **Step 5: Prove the service is read-only**

Add a test that snapshots all inserted course rows before and after `list_courses()` and asserts equality, then run:

```powershell
uv run pytest tests/test_phase7_courses_service.py -q
```

Expected: PASS.

- [ ] **Step 6: Run static checks and commit**

```powershell
uv run ruff check src/aaos/web tests/test_phase7_courses_service.py
uv run mypy src/aaos/web

git add src/aaos/web tests/test_phase7_courses_service.py
git commit -m "feat(phase7a): add safe academic course projection"
```

---

### Task 3: Expose `GET /api/v1/courses` through FastAPI without direct route-layer SQL

**Files:**
- Create: `src/aaos/web/routes.py`
- Modify: `src/aaos/web/__init__.py`
- Modify: `src/aaos/main.py`
- Create: `tests/test_phase7_courses_api.py`

**Interfaces:**
- Produces: `create_api_v1_router(session_factory: sessionmaker[Session]) -> APIRouter`.
- Produces: `GET /api/v1/courses -> CourseListResponse`.
- Extends: `create_app(settings=DEFAULT_SETTINGS, *, session_factory: sessionmaker[Session] | None = None) -> FastAPI`.
- When no session factory is injected, `create_app()` constructs the normal local SQLite engine using `create_database_engine(DEFAULT_DATABASE_PATH)` and `create_session_factory(engine)`; tests inject a temporary session factory.

- [ ] **Step 1: Write the failing HTTP contract tests**

Create `tests/test_phase7_courses_api.py` using `TestClient(create_app(..., session_factory=temp_factory))`.

Required tests:

```python
def test_courses_endpoint_returns_exact_safe_contract(): ...


def test_courses_endpoint_excludes_inactive_courses(): ...


def test_courses_endpoint_has_deterministic_order(): ...


def test_courses_endpoint_preserves_existing_health_contract(): ...


def test_courses_endpoint_does_not_expose_source_identity_anywhere(): ...
```

The exact endpoint payload must be:

```python
assert set(payload) == {"courses"}
assert set(payload["courses"][0]) == {
    "id",
    "title",
    "code",
    "description",
    "updated_at",
}
```

The health regression must assert the existing `/health` response still has exactly `status`, `service`, and `version`, and status remains `"ok"`.

- [ ] **Step 2: Run API tests and verify RED**

```powershell
uv run pytest tests/test_phase7_courses_api.py -q
```

Expected: FAIL because the `/api/v1/courses` router is not installed.

- [ ] **Step 3: Implement the router and dependency wiring**

`src/aaos/web/routes.py` must create an `APIRouter(prefix="/api/v1")` and instantiate/use `AcademicReadService`; the endpoint itself must contain no `select(...)`, `Session.execute(...)`, ORM iteration, commit, or mutation logic.

`create_app()` accepts the optional injected session factory for testability. If it creates the default engine itself, retain a reference on `application.state` and register safe engine disposal at application shutdown. Do not expose the engine/session factory to browser responses.

- [ ] **Step 4: Run API tests and verify GREEN**

```powershell
uv run pytest tests/test_phase7_courses_api.py -q
uv run pytest tests/test_phase7_web_errors.py tests/test_phase7_courses_service.py tests/test_phase7_courses_api.py -q
```

Expected: PASS.

- [ ] **Step 5: Prove route code contains no direct SQL query**

Add a focused structural regression test that reads `src/aaos/web/routes.py` and rejects imports/usages of `CourseORM`, `select(`, or `Session.execute`. This test protects the explicit route/service boundary rather than relying on code review alone.

Run:

```powershell
uv run pytest tests/test_phase7_courses_api.py -q
```

Expected: PASS.

- [ ] **Step 6: Run static checks and commit**

```powershell
uv run ruff check src/aaos/main.py src/aaos/web tests/test_phase7_courses_api.py
uv run mypy src/aaos/main.py src/aaos/web

git add src/aaos/main.py src/aaos/web tests/test_phase7_courses_api.py
git commit -m "feat(phase7a): expose browser-safe course API"
```

---

### Task 4: Freeze deterministic OpenAPI and generated TypeScript contracts

**Files:**
- Create: `scripts/export_openapi.py`
- Create: `tests/test_phase7_openapi_contract.py`
- Create: `frontend/package.json`
- Create: `frontend/package-lock.json`
- Create: `frontend/openapi/aaos-v1.json`
- Create: `frontend/types/api.generated.ts`
- Modify: `.gitignore` only if needed for `frontend/node_modules/`

**Interfaces:**
- Produces: deterministic `frontend/openapi/aaos-v1.json` from `create_app().openapi()`.
- Produces: `frontend/types/api.generated.ts` via `openapi-typescript`.
- Produces npm scripts:
  - `generate:openapi` invokes `uv run python ../scripts/export_openapi.py` from `frontend/` or an equivalent root-safe command;
  - `generate:api-types` runs OpenAPI export first, then `openapi-typescript`;
  - `check:api-types` regenerates and fails when Git reports drift in the two committed generated files.

- [ ] **Step 1: Write failing OpenAPI contract tests**

Create tests that inspect `create_app(...).openapi()` and assert:

```python
assert "/api/v1/courses" in schema["paths"]
course_schema = schema["components"]["schemas"]["CourseSummary"]
assert set(course_schema["properties"]) == {
    "id",
    "title",
    "code",
    "description",
    "updated_at",
}
assert "source_system" not in str(course_schema)
assert "source_id" not in str(course_schema)
```

Also assert `/health` remains in OpenAPI and that the course operation references `CourseListResponse` rather than an untyped dictionary.

- [ ] **Step 2: Run OpenAPI tests and verify RED where generation/export is missing**

```powershell
uv run pytest tests/test_phase7_openapi_contract.py -q
```

If schema assertions already pass because Tasks 1–3 created the models correctly, add the exporter existence/determinism test before proceeding so there is a genuine RED test for Task 4.

The exporter test must call the script twice into temporary output locations and assert byte-identical JSON.

- [ ] **Step 3: Implement deterministic exporter**

`scripts/export_openapi.py` must:

- import `create_app`;
- call `create_app().openapi()` without starting a server;
- serialize JSON with `ensure_ascii=False`, `sort_keys=True`, deterministic indentation, and one terminal newline;
- default to `frontend/openapi/aaos-v1.json`;
- accept an optional output path only for tests;
- never read credentials or include environment secrets.

- [ ] **Step 4: Verify exporter tests GREEN**

```powershell
uv run pytest tests/test_phase7_openapi_contract.py -q
uv run python scripts/export_openapi.py
```

Expected: PASS and `frontend/openapi/aaos-v1.json` created.

- [ ] **Step 5: Create the minimal frontend contract workspace**

From repository root:

```powershell
New-Item -ItemType Directory -Force frontend\types | Out-Null
cd frontend
npm init -y
npm install --save-dev openapi-typescript
cd ..
```

Edit `frontend/package.json` to remain `private: true`, remove irrelevant default scripts, and add deterministic generation/check scripts. Do not install Next.js, React, Tailwind, shadcn/ui, Lucide, or TanStack Query in 7A; those belong to 7B.

Generate the committed TypeScript contract:

```powershell
cd frontend
npm run generate:api-types
cd ..
```

- [ ] **Step 6: Verify the generated TypeScript excludes backend-only course fields**

Run:

```powershell
Select-String -Path frontend\types\api.generated.ts -Pattern "CourseSummary|source_system|source_id"
```

Expected: `CourseSummary` is present; `source_system` and `source_id` do not appear in the generated CourseSummary contract. If either backend-only field appears, stop and fix the Pydantic/OpenAPI contract rather than post-processing the generated file.

- [ ] **Step 7: Verify generation drift gate**

Run generation again and require no diff:

```powershell
cd frontend
npm run generate:api-types
cd ..
git diff --exit-code -- frontend/openapi/aaos-v1.json frontend/types/api.generated.ts frontend/package.json frontend/package-lock.json
```

Expected: clean generated artifact diff after files are staged/committed consistently.

- [ ] **Step 8: Run static/backend checks and commit**

```powershell
uv run pytest tests/test_phase7_openapi_contract.py -q
uv run ruff check scripts/export_openapi.py tests/test_phase7_openapi_contract.py
uv run mypy scripts/export_openapi.py

git add scripts/export_openapi.py tests/test_phase7_openapi_contract.py frontend .gitignore
git commit -m "build(phase7a): freeze OpenAPI TypeScript contract"
```

---

### Task 5: Phase 7A regression, leak, and acceptance gate

**Files:**
- No new production files expected.
- Modify tests only if a genuine uncovered 7A requirement is discovered; do not weaken existing assertions.

**Interfaces:**
- Consumes all Task 1–4 outputs.
- Produces the evidence package for human review before 7B.

- [ ] **Step 1: Run the complete Phase 7A focused suite**

```powershell
uv run pytest `
  tests/test_phase7_web_errors.py `
  tests/test_phase7_courses_service.py `
  tests/test_phase7_courses_api.py `
  tests/test_phase7_openapi_contract.py `
  -q
```

Expected: PASS.

- [ ] **Step 2: Run the full backend regression suite**

```powershell
uv run pytest -q
```

Expected: PASS. Existing known warnings may be reported separately; no new failure is acceptable.

- [ ] **Step 3: Run backend quality gates**

```powershell
uv run ruff check .
uv run mypy src
uv lock --check
git diff --check
```

Expected: PASS.

- [ ] **Step 4: Run generated contract drift gate**

```powershell
cd frontend
npm run generate:api-types
cd ..
git diff --exit-code -- frontend/openapi/aaos-v1.json frontend/types/api.generated.ts
```

Expected: PASS / no diff.

- [ ] **Step 5: Run explicit browser-leak scans over 7A public artifacts**

Use scoped searches rather than scanning credential files themselves:

```powershell
Select-String -Path `
  frontend\openapi\aaos-v1.json, `
  frontend\types\api.generated.ts `
  -Pattern `
  "source_system", `
  "source_id", `
  "Authorization", `
  "OAuth", `
  "cookie", `
  "data/auth", `
  "data/credentials", `
  "sqlite+pysqlite", `
  "C:\\Users\\" `
  -SimpleMatch
```

Expected: no matches. If a generic schema title legitimately contains a false-positive word, review it manually and tighten the search; never waive a real secret/path leak.

- [ ] **Step 6: Verify repository state and review the complete 7A diff**

```powershell
git status --short
git diff phase-06b-passed...HEAD --stat
git diff phase-06b-passed...HEAD -- `
  src/aaos/main.py `
  src/aaos/web `
  scripts/export_openapi.py `
  tests/test_phase7_* `
  frontend
```

Confirm manually:

- no Phase 4/5/6 authority logic changed;
- no direct provider integration added;
- no frontend application dependencies beyond contract generation were added;
- `/health` remains semantically unchanged;
- browser CourseSummary exposes exactly the approved fields;
- route layer contains no direct ORM query;
- all new errors are safe and request-correlated.

- [ ] **Step 7: Record 7A completion evidence in a dedicated docs commit only after human review**

Do not mark Phase 7 passed. After the human reviews Tasks 1–6 evidence, update governance/roadmap only to reflect the actual accepted 7A state and commit that docs-only evidence separately.

Expected status before starting 7B:

```text
PHASE_7A=HUMAN_APPROVED_OR_EXPLICITLY_ACCEPTED
PHASE_7=IN_PROGRESS
PHASE_7_PASSED_TAG=ABSENT
```

---

## Plan Self-Review

- Spec coverage for 7A: browser-safe Pydantic boundary, `/api/v1`, error/request ID contract, read-only academic service, first Courses endpoint, `/health` preservation, deterministic ordering, exact field/leak constraints, OpenAPI export, generated TypeScript drift gate — covered.
- No Phase 7B UI work is pulled into 7A; the only `frontend/` content is the API-contract generation workspace.
- No direct Phase 4/5/6 modifications are required.
- No external writes, RAG execution, agent task creation, approval mutations, SSE, or health aggregation are part of 7A.
- Every production change is preceded by a focused failing test.
- The first human review boundary is the complete 7A evidence package; 7B does not begin automatically.
