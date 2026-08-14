# Phase 2 — Real Moodle Read-Only Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one explicitly approved, read-only Moodle source adapter that feeds the frozen Phase 1 collection, normalization, and synchronization pipeline without exposing credentials or permitting remote writes.

**Architecture:** A human-approved capability record selects exactly one mode: a typed Moodle API adapter or, only if separately approved, a fixed Playwright browser adapter. Both produce the existing `SourceCollection`; only a proven-complete result reaches `normalize_collection()` and the existing atomic `AcademicSyncService`. The first live acceptance uses a disposable migrated SQLite database before any separately authorized normal-database sync.

**Tech Stack:** Python 3.11, the repository's existing `httpx2` transport seam (confirmed by `pyproject.toml`, `uv.lock`, and current inference imports), existing SQLAlchemy/Alembic SQLite foundation, and Playwright only after a separate browser-mode/dependency approval.

## Global Constraints

- Work only after Phase 1 remains frozen at `phase-01-passed` (`8728aee00f4d6cddc8d1cc9b8f1d41a1c4684fef`).
- Phase 2 is strictly read-only: no Moodle write API, form submission, mutating browser action, download, or external write.
- The configured capability record selects `api` OR `browser`; failures never switch modes automatically.
- The configured `source_system` is an exact stable non-secret namespace whose completeness scope is authoritative.
- A partial, failed, redirected, unsupported, or unknown-completeness collection must never become a `CompleteSourceSnapshot` or reach synchronization.
- Preserve the existing `SourceCollection`, `normalize_collection()`, `CanonicalSnapshot`, and `AcademicSyncService.sync()` contracts; do not add a parallel persistence path or migration.
- API automatic redirects are disabled by default; approved redirects require target origin/path validation and never forward credentials to an unapproved origin.
- Browser auth state uses only a dedicated per-source AAOS profile/storage artifact under `data/auth/moodle/<source-alias>/`; ordinary browser profiles are forbidden.
- Capability records contain no passwords, tokens, cookies, session IDs, authorization headers, private content, browser state, or credential-bearing URLs.
- API/browser adapters expose fixed application-owned operations and routes; no generic arbitrary URL, API function, browser click/type, or JavaScript interface.
- Real credentials/session state are never used in ordinary automated tests; fixtures are synthetic and sanitized.
- The first live flow is adapter read → normalization → disposable isolated database sync → optional repeat sync; normal-database sync requires a separate explicit human authorization.
- `RemoteFile` remains metadata-only and never triggers a download.
- Do not add Phase 3+ functionality, RAG, LLM calls, agents, MCP, dashboard, REPL, vision, Calendar, GitHub, or portal write support.
- Every task uses TDD for deterministic code, runs focused tests before broader gates, and commits only its listed files after review.

---

## Task 1: Non-secret Moodle settings and frozen capability contract

**Files:**
- Modify: `src/aaos/config.py`
- Create: `src/aaos/moodle/__init__.py`
- Create: `src/aaos/moodle/models.py`
- Create: `src/aaos/moodle/auth.py`
- Test: `tests/test_moodle_config.py`
- Test: `tests/test_moodle_capability.py`
- Test: `tests/test_moodle_auth_paths.py`

**Interfaces:**
- Produces `MoodleMode = Literal["api", "browser"]`.
- Produces typed policy aliases `EntityType`, `ApiOperation`, `BrowserRoute`, `PaginationStrategy`, `IdentityStrategy`, `TimestampStrategy`, and `RedirectPolicy`; each is a closed `Literal` set owned by the application.
- Produces frozen `MoodleSettings(source_system: str, base_origin: str, mode: MoodleMode, auth_alias: str)` with no secret fields.
- Produces frozen `MoodleCapability(source_system: str, base_origin: str, mode: MoodleMode, complete_scope: str, entity_scope: tuple[EntityType, ...], approved_api_operations: tuple[ApiOperation, ...], approved_browser_routes: tuple[BrowserRoute, ...], pagination_strategy: PaginationStrategy, identity_strategy: IdentityStrategy, timestamp_strategy: TimestampStrategy, redirect_policy: RedirectPolicy, redirect_origins: tuple[str, ...], required_version: str | None, evidence_notes: str)`; `evidence_notes` is descriptive and never executable.
- Produces `validate_capability(settings: MoodleSettings, capability: MoodleCapability) -> None` that rejects source/origin/mode/scope disagreement, unsupported typed values, duplicate operations/routes, and any empty field required by the selected mode (API operations for `api`; browser routes for `browser`).
- Produces `AuthPaths(api_token_path: Path, browser_state_dir: Path)` and `resolve_auth_paths(settings: MoodleSettings, data_root: Path = Path("data")) -> AuthPaths`; the result is confined to the two approved ignored roots and uses only a validated filesystem-safe alias.
- Produces `MoodleCredentialReader.read_api_token() -> str`, backed by `read_api_token(paths: AuthPaths) -> str`, with `MAX_API_TOKEN_BYTES = 8192`; it reads only the exact validated token path during an initiated collection, uses strict UTF-8 and bounded bytes, rejects missing/empty/malformed state with fixed errors, and never exposes token contents.
- Produces `MoodleBrowserStateLoader.load_state_path() -> Path`, which accepts only the exact dedicated validated browser-state location and source/origin binding; it never enumerates or attaches to ordinary browser profiles.

- [ ] **Step 1: Write failing validation tests.** Cover loopback-independent HTTPS origin syntax, blank/whitespace/control-character rejection, mode outside `api|browser`, unsafe aliases, capability source/origin/mode mismatch, missing complete scope/entity scope, and a capability representation that contains no secret-bearing fields.
- [ ] **Step 2: Run the focused tests to verify RED.**

Run: `uv run pytest tests/test_moodle_config.py tests/test_moodle_capability.py tests/test_moodle_auth_paths.py -v`

Expected: collection or assertion failures because the new Moodle contracts do not exist.
- [ ] **Step 3: Implement the minimal typed settings, capability, path, and application-owned auth-reader boundary.** Do not create profile state, make network access, enumerate credential directories, or write files. `resolve_auth_paths` may compute paths only; `read_api_token` may read only the exact validated token path during explicit collection, with bounded strict decoding and fixed redacted errors.
- [ ] **Step 4: Run focused tests and static checks.**

Run: `uv run pytest tests/test_moodle_config.py tests/test_moodle_capability.py tests/test_moodle_auth_paths.py -v`; `uv run ruff check src/aaos/moodle tests/test_moodle_config.py tests/test_moodle_capability.py tests/test_moodle_auth_paths.py`; `uv run mypy src/aaos/moodle src/aaos/config.py tests/test_moodle_config.py tests/test_moodle_capability.py tests/test_moodle_auth_paths.py`

Expected: all focused tests, Ruff, and strict mypy pass.
- [ ] **Step 5: Commit.**

```bash
git add src/aaos/config.py src/aaos/moodle tests/test_moodle_config.py tests/test_moodle_capability.py tests/test_moodle_auth_paths.py
git commit -m "feat: define Moodle capability contract"
```

## Task 2: Preparation and capability gate

**Files:**
- Create: `src/aaos/moodle/preparation.py`
- Create: `tests/test_moodle_preparation.py`
- Modify: `docs/superpowers/sdd/2026-08-14-phase-2-moodle-capability-record.md` (ignored evidence/report path only; do not commit sensitive material)

**Interfaces:**
- Produces `CapabilityPreparationResult(status: Literal["ready", "blocked"], reason_code: str, capability: MoodleCapability | None)`.
- Produces `prepare_capability(settings: MoodleSettings, observed: Mapping[str, object]) -> CapabilityPreparationResult`; `observed` is synthetic/offline in tests and is never a generic execution policy.
- The result is `ready` only when exact origin, selected mode, complete scope, all four entity scopes, identity rule, timestamp rule, pagination rules, and redirect/origin policy are proven and represented in a `MoodleCapability`.

- [ ] **Step 1: Write failing tests for the preparation gate.** Prove missing API capability yields `blocked` without proposing browser fallback, incomplete entity scope yields `blocked`, an all-courses namespace rejects an observed one-course scope, and a complete synthetic record returns `ready` with no secret fields.
- [ ] **Step 2: Run the focused tests to verify RED.**

Run: `uv run pytest tests/test_moodle_preparation.py -v`

Expected: failure because the preparation module does not exist.
- [ ] **Step 3: Implement the deterministic preparation gate.** It may inspect synthetic capability evidence only. It must not authenticate, contact Moodle, read token files, create browser state, or choose a fallback mode.
- [ ] **Step 4: Run focused tests, Ruff, and mypy.**

Run: `uv run pytest tests/test_moodle_preparation.py -v`; `uv run ruff check src/aaos/moodle/preparation.py tests/test_moodle_preparation.py`; `uv run mypy src/aaos/moodle/preparation.py tests/test_moodle_preparation.py`

Expected: pass. Record the real deployment capability decision as a safe, non-secret handoff before Task 3 or Task 4 begins.
- [ ] **Step 5: Commit.**

```bash
git add src/aaos/moodle/preparation.py tests/test_moodle_preparation.py
git commit -m "feat: gate Moodle capability preparation"
```

**HUMAN CAPABILITY GATE:** After Task 2 is implemented, tested, and committed, stop unconditionally. Present a safe non-secret handoff containing the proposed namespace, base origin, mode, complete scope, four-entity availability, known/unknown capabilities, pagination/completeness evidence, identity strategy, timestamp/timezone strategy, redirect/origin policy, and blockers. Do not include passwords, tokens, cookies, session state, private content, or credential-bearing URLs. Task 3 may begin only after explicit human approval of API mode and its capability record. Task 4 may begin only after explicit human approval of browser mode; Playwright installation requires separate approval. No Moodle request may occur to make Task 2 pass.

## Task 3: API transport and typed read adapter (conditional on Task 2 API readiness)

**Files:**
- Create: `src/aaos/moodle/api.py`
- Create: `src/aaos/moodle/api_parsing.py`
- Create: `tests/test_moodle_api_transport.py`
- Create: `tests/test_moodle_api_parsing.py`

**Interfaces:**
- Consumes the `ready` API `MoodleCapability` from Task 2.
- Produces private `MoodleApiTransport` with fixed module-owned methods `fetch_courses(page: int) -> ApiResponse`, `fetch_assignments(course_source_id: str, page: int) -> ApiResponse`, `fetch_announcements(course_source_id: str, page: int) -> ApiResponse`, and `fetch_remote_files(course_source_id: str, page: int) -> ApiResponse`; it has no caller-provided URL, function name, or generic request method.
- Produces `MoodleApiSource(settings: MoodleSettings, capability: MoodleCapability, transport: MoodleApiTransport, credential_reader: MoodleCredentialReader)` implementing `AcademicSource.collect(name: str) -> SourceCollection`; production wiring uses the Task 1 application-owned reader, while tests inject a synthetic reader object.
- The adapter returns only `CompleteSourceSnapshot` or `SourceCollectionFailure`; it never returns raw API payloads.

- [ ] **Step 1: Add synthetic transport/parser tests.** Cover the exact approved operation allowlist, fixed base origin, token absence from outputs, HTTP status mapping, JSON schema rejection, provider identity mapping, API epoch/offset timestamp conversion, and entity extraction into Phase 1 raw records.
- [ ] **Step 2: Add redirect and credential-forwarding RED tests.** Assert redirects are disabled by default, approved redirects require exact origin/path validation, unapproved cross-origin redirects fail, and no token is present in failure messages or returned observations.
- [ ] **Step 3: Run focused tests to verify RED.**

Run: `uv run pytest tests/test_moodle_api_transport.py tests/test_moodle_api_parsing.py -v`

Expected: failures because the API transport and parser do not exist.
- [ ] **Step 4: Implement only the module-owned operation methods and capability-approved requests.** Use the existing `httpx2` seam; disable redirects by default; validate any approved redirect before sending credentials; bind typed `ApiOperation` values to application-owned endpoint/function constants; bound response sizes; decode and validate provider payloads before mapping; use fixed safe errors; and close the transport deterministically. Do not guess Moodle endpoint names before Task 2 records them.
- [ ] **Step 5: Run focused API tests and static checks.**

Run: `uv run pytest tests/test_moodle_api_transport.py tests/test_moodle_api_parsing.py -v`; `uv run ruff check src/aaos/moodle/api.py src/aaos/moodle/api_parsing.py tests/test_moodle_api_transport.py tests/test_moodle_api_parsing.py`; `uv run mypy src/aaos/moodle/api.py src/aaos/moodle/api_parsing.py tests/test_moodle_api_transport.py tests/test_moodle_api_parsing.py`

Expected: pass, including redirect and redaction cases.
- [ ] **Step 6: Commit.**

```bash
git add src/aaos/moodle/api.py src/aaos/moodle/api_parsing.py tests/test_moodle_api_transport.py tests/test_moodle_api_parsing.py
git commit -m "feat: add approved Moodle API source"
```

## Task 4: Browser adapter (conditional on Task 2 API insufficiency and explicit Playwright approval)

**Files:**
- Create: `src/aaos/moodle/browser.py`
- Create: `src/aaos/moodle/browser_parsing.py`
- Create: `tests/test_moodle_browser.py`
- Modify: `pyproject.toml` and `uv.lock` only if browser mode is explicitly approved and Playwright is accepted as a runtime dependency.

**Interfaces:**
- Consumes a `ready` browser `MoodleCapability`, `MoodleSettings`, and `AuthPaths.browser_state_dir`.
- Consumes `MoodleBrowserStateLoader.load_state_path()` from Task 1; no ad-hoc profile path or browser-state reader is permitted.
- Produces `MoodleBrowserSource(...).collect(name: str) -> SourceCollection` implementing `AcademicSource`.
- Browser state is loaded only from the dedicated source/origin-bound AAOS path; no ordinary browser profile path is accepted.

- [ ] **Step 1: Write tests against static sanitized HTML and a fake browser seam.** Cover dedicated-profile path enforcement, origin binding, login-page detection with HTTP 200, SSO/MFA redirects, fixed route/selector allowlists, no generic click/type/evaluate methods, no non-GET/HEAD portal requests, DOM mapping, and sanitized failures.
- [ ] **Step 2: Run focused tests to verify RED.**

Run: `uv run pytest tests/test_moodle_browser.py -v`

Expected: failure because browser modules are absent.
- [ ] **Step 3: Before implementation, verify Task 2 did not produce a complete API capability.** If API is complete, mark this task not applicable; do not install Playwright.
- [ ] **Step 4: If explicitly approved, add the smallest pinned Playwright runtime dependency and update `uv.lock`.** Run `uv lock --check` and inspect that lock changes are dependency-only. If browser mode is not approved, mark the task not applicable and do not touch dependency files.
- [ ] **Step 5: Implement fixed browser navigation and parsing.** Do not fill credentials, submit forms, attach to ordinary profiles, expose JavaScript or arbitrary browser controls, enable traces/screenshots/HTML dumps, or treat a partial page as complete. Enforce route/origin/method policy and deterministic cleanup.
- [ ] **Step 6: Run focused browser tests, Ruff, and mypy.**

Run: `uv run pytest tests/test_moodle_browser.py -v`; `uv run ruff check src/aaos/moodle/browser.py src/aaos/moodle/browser_parsing.py tests/test_moodle_browser.py`; `uv run mypy src/aaos/moodle/browser.py src/aaos/moodle/browser_parsing.py tests/test_moodle_browser.py`

Expected: pass, or task remains not applicable when API mode is complete.
- [ ] **Step 7: Commit only the browser files and approved dependency metadata.**

```bash
git add src/aaos/moodle/browser.py src/aaos/moodle/browser_parsing.py tests/test_moodle_browser.py pyproject.toml uv.lock
git commit -m "feat: add approved Moodle browser source"
```

Run this commit only when browser mode and the Playwright dependency were
explicitly approved and those files were actually implemented. In API-only
mode, do not stage or commit browser paths or dependency metadata.

## Task 5: Completeness, pagination, and read-only integration boundary

**Files:**
- Create: `src/aaos/moodle/collection.py`
- Create: `tests/test_moodle_collection.py`
- Modify: `src/aaos/moodle/api.py` or `src/aaos/moodle/browser.py` only to connect the common collector seam.

**Interfaces:**
- Produces `CollectionState` with explicit states `AUTHENTICATE`, `ENUMERATE_COURSES`, `COLLECT_ENTITIES`, `VALIDATE_COMPLETE`, `BUILD_COMPLETE`, and `FAILED`.
- Produces `collect_complete(source_reader: MoodleReadSource, capability: MoodleCapability) -> SourceCollection`.
- `MoodleReadSource` is a private protocol with `discover_courses() -> CourseDiscovery` and `collect_course_entities(course: RawCourse) -> EntityBundle`; `CourseDiscovery` carries the complete course tuple and proven pagination state, while `EntityBundle` carries the four entity tuples for one course. Neither type contains raw payloads or a transport handle.
- The collector consumes a mode-specific `MoodleReadSource` that supplies already-validated pages/records; it cannot expose a generic transport.

- [ ] **Step 1: Write RED tests.** Cover complete multi-course and complete-empty results, one-course failure, failed required endpoint/page, missing page, duplicate IDs, inconsistent totals, unknown completeness, login redirect, unexpected origin, malformed timestamps, and an all-courses namespace receiving a one-course result.
- [ ] **Step 2: Run focused tests to verify RED.**

Run: `uv run pytest tests/test_moodle_collection.py -v`

Expected: failure because the common collector does not exist.
- [ ] **Step 3: Implement the explicit state machine.** Any failure before `BUILD_COMPLETE` returns a typed `SourceCollectionFailure`; no empty success is synthesized; no normalization or sync is called by the collector; no automatic retry or mode switch occurs.
- [ ] **Step 4: Add read-only request assertions.** Test that API operations are module-owned and browser routes/methods are fixed, and that no mutation request can be emitted through the source boundary.
- [ ] **Step 5: Run focused tests and static checks.**

Run: `uv run pytest tests/test_moodle_collection.py -v`; `uv run ruff check src/aaos/moodle/collection.py tests/test_moodle_collection.py`; `uv run mypy src/aaos/moodle/collection.py tests/test_moodle_collection.py`

Expected: pass.
- [ ] **Step 6: Commit.**

```bash
git add src/aaos/moodle/collection.py tests/test_moodle_collection.py
# API mode only, if api.py changed:
git add src/aaos/moodle/api.py
git commit -m "feat: enforce complete Moodle snapshots"
```

For browser mode, replace the API path with
`src/aaos/moodle/browser.py`; never stage both adapter paths merely because
both are listed in the plan.

## Task 6: Phase 1 pipeline integration and isolated synchronization

**Files:**
- Create: `src/aaos/moodle/integration.py`
- Create: `tests/test_moodle_integration.py`
- Create: `tests/test_moodle_isolated_sync.py`

**Interfaces:**
- Produces `sync_moodle_snapshot(collection: SourceCollection, session_factory: Callable[[], Session]) -> None`; a `SourceCollectionFailure` raises a fixed `MoodleSyncPreconditionError` before opening a session.
- Produces `build_isolated_database(path: Path) -> tuple[Engine, Callable[[], Session]]` using existing Alembic migrations and the existing academic ORM metadata.
- Normal path remains an explicit caller action; the integration module never promotes an isolated database into the normal database.

- [ ] **Step 1: Write RED integration tests.** Cover source failure leaving the database unchanged, complete normalization then sync, all four entity relationships, repeat-sync idempotence, and disposal of a fresh isolated database.
- [ ] **Step 2: Run focused tests to verify RED.**

Run: `uv run pytest tests/test_moodle_integration.py tests/test_moodle_isolated_sync.py -v`

Expected: failure because the integration module does not exist.
- [ ] **Step 3: Implement the narrow pipeline.** The only order is `SourceCollection -> normalize_collection() -> AcademicSyncService.sync()`. Apply normal migrations to a caller-provided disposable path; do not alter Phase 1 migration files or create a Moodle table.
- [ ] **Step 4: Test normal-database authorization as a separate seam.** The isolated acceptance helper must not call or mutate the configured normal database; a normal sync requires a distinct explicit caller invocation.
- [ ] **Step 5: Run focused tests, Ruff, and mypy.**

Run: `uv run pytest tests/test_moodle_integration.py tests/test_moodle_isolated_sync.py -v`; `uv run ruff check src/aaos/moodle/integration.py tests/test_moodle_integration.py tests/test_moodle_isolated_sync.py`; `uv run mypy src/aaos/moodle/integration.py tests/test_moodle_integration.py tests/test_moodle_isolated_sync.py`

Expected: pass.
- [ ] **Step 6: Commit.**

```bash
git add src/aaos/moodle/integration.py tests/test_moodle_integration.py tests/test_moodle_isolated_sync.py
git commit -m "feat: integrate Moodle with Phase 1 sync"
```

## Task 7: Sanitized opt-in real-portal acceptance smoke

**Files:**
- Create: `tests/live/test_moodle_read_only_smoke.py`
- Create: `.superpowers/sdd/2026-08-14-phase-2-moodle-read-only-sync/phase-2-live-evidence-report.md` (ignored evidence; never store secrets or private content)

**Interfaces:**
- Uses only human-prepared `MoodleSettings`, the frozen `MoodleCapability`, and selected mode credentials/session state.
- Writes only to a fresh isolated temporary SQLite database during the smoke unless a separate explicit human authorization is recorded for normal-database sync.

**PRE-LIVE HUMAN AUTHORIZATION GATE:** After Tasks 3/4–6 are complete and
before any opt-in real-portal request, run the selected-mode focused suite,
normalization/integration tests, complete-snapshot failure tests,
credential/auth-path tests, redirect credential-forwarding tests for API mode or
dedicated profile/origin-binding tests for browser mode, read-only route tests,
generic-surface and mutation-request review, import-time I/O/network tests,
redaction review, `git ls-files data/auth data/credentials`, `git diff --check`,
and clean-worktree verification. A Terra high focused security review must pass
against the selected adapter. Then stop and present the selected mode,
capability summary, exact HEAD, deterministic results, review result, remaining
external prerequisites, and confirmation that no Moodle request has occurred.
Request explicit human authorization to run the real read-only smoke. Do not
create or prepare credentials/session state before that authorization.

- [ ] **Step 1: Add the opt-in guard and safe assertions.** Default behavior skips without touching Moodle; opt-in requires `AAOS_RUN_LIVE_MOODLE_SMOKE=1` and a prepared capability record. Never print the origin with sensitive query material, token, cookie, course content, HTML, screenshots, or traces.
- [ ] **Step 2: Run the default smoke test.**

Run: `uv run pytest tests/live/test_moodle_read_only_smoke.py -v`

Expected: one intentional skip and no network request.
- [ ] **Step 3: Only after explicit human authorization, with a human-prepared account, run staged acceptance.** Prove complete adapter read, normalization, isolated migration/sync, repeat-sync where feasible, and no mutation request. A one-course smoke uses a distinct smoke namespace and isolated database.
- [ ] **Step 4: Record only safe evidence.** Include mode, fixed result categories, counts, normalization/sync outcomes, idempotence outcome, and any blocker. Do not include private records, credentials, cookies, token-bearing URLs, raw payloads, or browser artifacts.
- [ ] **Step 5: Commit only the smoke test.**

```bash
git add tests/live/test_moodle_read_only_smoke.py
git commit -m "test: add opt-in Moodle read-only smoke"
```

## Task 8: Security verification and human handoff

**Files:**
- Create: `.superpowers/sdd/2026-08-14-phase-2-moodle-read-only-sync/phase-2-evidence-report.md` (ignored evidence)
- Modify: none unless a narrowly scoped regression fix is required by review.

The final deterministic verification HEAD, final whole-Phase-2 reviewed HEAD,
and candidate human-accepted HEAD must be the same exact committed SHA. Any
post-review fix creates a new candidate and requires the complete gate and final
review again.

- [ ] **Step 1: After the live smoke commit and all tracked implementation work, confirm the worktree is clean and capture `git rev-parse HEAD` as the candidate SHA.** Do not reuse evidence from a superseded commit.
- [ ] **Step 2: Run the complete deterministic gate against that exact candidate HEAD.**

Run: `uv lock --check`; `uv sync --locked --all-groups`; `uv run ruff format --check .`; `uv run ruff check .`; `uv run mypy src tests`; `uv run pytest -v`; `git diff --check`; `git ls-files data/auth data/credentials`.

Expected: all deterministic checks pass, locked sync causes no tracked mutation, and credential/auth paths are absent from tracked files.
- [ ] **Step 3: Confirm locked sync caused no tracked mutation, then run focused security checks.** Prove no import-time network or auth-state I/O, no raw secrets/private content in errors/logging/evidence, no generic API/browser surface, no unapproved redirect credential forwarding, dedicated profile/origin binding, no mutation request, complete-snapshot fail-closed behavior, and no normal-database mutation during isolated smoke.
- [ ] **Step 4: Dispatch one final whole-Phase-2 security/architecture review against the exact candidate SHA using Sol high.** If it finds a blocking issue, fix it test-first, commit the fix, capture the new SHA, rerun the complete deterministic gate, and rerun this final review.
- [ ] **Step 5: Assemble the evidence report.** Record the exact candidate SHA, deterministic results, live results, external blockers, private-data limitations, and the explicit recommendation for human Phase 2 acceptance. Do not claim acceptance or create a phase tag.
- [ ] **Step 6: Stop for explicit human Phase 2 acceptance.** Normal-database synchronization, if authorized at all, must already be separately approved by the human; no Phase 3 work starts in this plan.

## Plan self-review checklist

- Capability preparation is a hard gate before adapter selection.
- Task 2 always stops for explicit human capability approval before Task 3/4.
- API mode has no assumed Moodle endpoint names before deployment evidence.
- Production API secret reads use the bounded application-owned reader; live smoke has no filesystem shortcut.
- Executable capability fields use closed typed values; descriptive evidence notes are not interpreted.
- Browser mode remains conditional and does not force a Playwright dependency.
- Redirect policy prevents credential forwarding to unexpected origins.
- Dedicated AAOS browser state cannot use ordinary user profiles.
- Complete-snapshot authority prevents partial-smoke deactivation.
- Isolated sync precedes separately authorized normal-database sync.
- Pre-live Terra high review and explicit human authorization precede the first portal request.
- Final evidence, complete gates, and Sol whole-phase review use one exact committed HEAD.
- No password automation, generic transport/browser interface, migration, Phase 3 feature, or credential fixture is planned.
- Every code task has focused RED/GREEN checks and an explicit commit scope.

STOP after writing this plan and wait for HUMAN PLAN APPROVAL.
