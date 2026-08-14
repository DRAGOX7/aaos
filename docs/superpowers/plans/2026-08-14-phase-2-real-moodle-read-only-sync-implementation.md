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
- Task 3A uses only Moodle's registered-URI mobile callback; no loopback HTTP callback, OAuth callback, arbitrary scheme, or URI-scheme registry mutation is permitted.
- Task 3A's only authenticated Moodle operation is `core_webservice_get_site_info`; entity functions require a later human API-function approval and Task 3B amendment.
- Task 3A implementation and tests are offline-only; no real SSO/bootstrap, token creation, or authenticated site-info request occurs before its PRE-AUTH SECURITY GATE and explicit human authorization.
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

Expected: pass. Record the real deployment capability decision as a safe, non-secret handoff before Task 3A or Task 4 begins.
- [ ] **Step 5: Commit.**

```bash
git add src/aaos/moodle/preparation.py tests/test_moodle_preparation.py
git commit -m "feat: gate Moodle capability preparation"
```

**HUMAN CAPABILITY GATE:** After Task 2 is implemented, tested, and committed, stop unconditionally. Present a safe non-secret handoff containing the provisional namespace/base-origin evidence, mode candidate, complete scope, four-entity availability, known/unknown capabilities, pagination/completeness evidence, identity strategy, timestamp/timezone strategy, redirect/origin policy, and the proposed authentication-bootstrap mechanism/protocol compatibility. Do not finalize a moving `/current/` alias as the source namespace. Do not include passwords, tokens, cookies, session state, private content, or credential-bearing URLs. Task 3A may begin only after explicit human approval of API mode, its capability record, and the registered-URI bootstrap protocol. Task 4 may begin only after explicit human approval of browser data-collection mode; Playwright installation requires separate approval. No Moodle request may occur to make Task 2 pass.

## Task 3A: Moodle mobile authentication bootstrap and site-info capability probe (conditional on Task 2 API readiness)

**Files:**
- Create: `src/aaos/moodle/bootstrap.py`
- Create: `tests/test_moodle_bootstrap.py`
- Modify: `src/aaos/moodle/auth.py`
- Create: `tests/test_moodle_site_info.py`
- Modify: `tests/test_moodle_auth_paths.py`
- Create: `src/aaos/moodle/api.py`
- Create: `src/aaos/moodle/api_parsing.py`
- Create: `tests/test_moodle_api_transport.py`
- Create: `tests/test_moodle_api_parsing.py`

**Interfaces:**
- Consumes the `ready` API `MoodleCapability` from Task 2.
- Produces frozen `MoodleBootstrapConfig(source_system: str, canonical_wwwroot: str, callback_kind: Literal["registered_uri"], callback_scheme: str, timeout_seconds: int)`; `canonical_wwwroot` is the approved Moodle wwwroot used for the protocol site identity and fixed launch URL. The config contains no secret, arbitrary callback URL, loopback callback, or caller-selected service value.
- Produces `bootstrap_token(config: MoodleBootstrapConfig, paths: AuthPaths, launcher: SystemBrowserLauncher, receiver: RegisteredUriReceiver) -> None`. The launcher opens only the application-built fixed `admin/tool/mobile/launch.php` URL with module-owned `service`, one-time `passport`, and requested `urlscheme`; the receiver supplies one raw callback URI only inside the bootstrap attempt. The bootstrap validates the URI and app-token envelope, writes only the extracted web-service token to `AuthPaths.api_token_path`, and returns no token.
- `SystemBrowserLauncher` is an application-owned protocol with `open_fixed_auth_url(url: str) -> None`; `RegisteredUriReceiver.wait_for_callback(timeout_seconds: int) -> str` is an injected testable callback boundary, not a generic browser-control interface. Only `registered_uri` is supported. Windows URI-scheme registration is a separate human-controlled setup action; runtime code must not modify the registry or claim `moodlemobile`.
- The callback parser accepts exactly `<approved-scheme>://token=<encoded-app-token>` with no userinfo, host, port, path, query, fragment, duplicate token, or extra structure. It strictly bounds the encoded payload before decoding, strictly decodes URI/base64 data, bounds the decoded envelope, accepts exactly two or three `:::` components (`siteid:::web_service_token` or `siteid:::web_service_token:::private_token`), computes the protocol-defined `MD5(canonical_wwwroot + passport)` site identity, compares it safely, extracts only the web-service token, and discards the optional private token.
- `src/aaos/moodle/auth.py` gains the application-owned `write_api_token(paths: AuthPaths, token: str) -> None`; it writes only the validated extracted web-service token to the exact ignored credential path. No encoded envelope, siteid, private token, callback URI, passport, or Microsoft session material is persisted or exposed.
- Produces private `SiteInfoProbeAuthorization.consume() -> None` as an injected protocol supplied by the external human-gated caller; Task 3A exposes no public constructor or factory that can mint authorization. The capability is consumed once globally before the request, so it cannot be reused across transports.
- Produces private `MoodleApiTransport(..., authorization: SiteInfoProbeAuthorization)` with exactly `get_site_info() -> ApiResponse`; it has no entity methods, caller-provided URL, function name, or generic request method.
- Produces a frozen safe site-info result containing only canonical safe base identity, optional release/version, available function names, site identity facts, and whether the requested URI scheme was honored or overridden. It never returns raw payloads or usernames, names, email, keys, tokens, or course content.

- [ ] **Step 1: Add RED tests for the registered-URI callback and app-token envelope.** Cover exact `aaos://token=<encoded-app-token>` grammar, unexpected scheme, malformed URI, extra structure, duplicate token payloads, empty/oversized/invalid encoding, one-time passport generation/cleanup, correct two- and three-component envelopes, wrong passport/siteid, malformed base64, wrong component count, empty web-service token, encoded/decoded size bounds, private-token discard, direct credential-path write, and absence of sensitive values from repr/errors/logs/return values. Tests use synthetic callback data and fake launcher/receiver objects; they do not authenticate or start a real listener.
- [ ] **Step 2: Run the bootstrap tests to verify RED.**

Run: `uv run pytest tests/test_moodle_bootstrap.py -v`

Expected: failure because the bootstrap module does not exist.

Expected: failures because the bootstrap and site-info modules do not exist.
- [ ] **Step 3: Implement the smallest application-owned registered-URI bootstrap for offline invocation only.** Generate the cryptographically strong passport with the standard library, retain it only for the attempt, build the fixed Moodle mobile launch URL, and let the user enter Microsoft credentials and MFA directly when the separately authorized flow is later invoked. Validate the exact URI grammar and app-token envelope before writing only the extracted web-service token. Do not perform real SSO, create a real token, or contact Moodle during Task 3A implementation/tests. Do not use a loopback listener, automate passwords, inspect app storage, attach to a normal browser profile, enable artifacts, collect academic data, or modify the Windows registry. If the site forces a non-AAOS scheme, return a fixed compatibility failure for human review.
- [ ] **Step 4: Add RED tests for the site-info-only API boundary.** Cover exactly one `core_webservice_get_site_info` operation, application-owned token reading, fixed base origin, redirects disabled by default, approved redirect origin/path validation, unapproved cross-origin rejection, bounded response parsing, safe identity/release/function-name mapping, and absence of usernames, names, email, tokens, raw JSON, or course content. Assert that no entity function or generic request method exists.
- [ ] **Step 5: Run the site-info tests to verify RED.**

Run: `uv run pytest tests/test_moodle_site_info.py tests/test_moodle_api_transport.py tests/test_moodle_api_parsing.py -v`

Expected: failures because the site-info transport/parser do not exist.
- [ ] **Step 6: Implement only the fixed site-info operation.** Use the existing `httpx2` seam; read the token only through the Task 1 application-owned reader; disable redirects by default; validate any approved redirect before sending the token; bind the sole operation to the module-owned `core_webservice_get_site_info` function constant; bound response sizes; decode and validate the provider payload; map only safe fields; use fixed redacted errors; and close the transport deterministically. Do not invoke Course, Assignment, Announcement, or RemoteFile functions.
- [ ] **Step 7: Run focused Task 3A tests and static checks.**

Run: `uv run pytest tests/test_moodle_bootstrap.py tests/test_moodle_site_info.py tests/test_moodle_api_transport.py tests/test_moodle_api_parsing.py -v`; `uv run ruff check src/aaos/moodle/bootstrap.py src/aaos/moodle/auth.py src/aaos/moodle/api.py src/aaos/moodle/api_parsing.py tests/test_moodle_bootstrap.py tests/test_moodle_site_info.py tests/test_moodle_auth_paths.py tests/test_moodle_api_transport.py tests/test_moodle_api_parsing.py`; `uv run mypy src/aaos/moodle/bootstrap.py src/aaos/moodle/auth.py src/aaos/moodle/api.py src/aaos/moodle/api_parsing.py tests/test_moodle_bootstrap.py tests/test_moodle_site_info.py tests/test_moodle_auth_paths.py tests/test_moodle_api_transport.py tests/test_moodle_api_parsing.py`

Expected: pass, including callback, envelope, redirect, site-info, and redaction cases.
- [ ] **Step 8: Commit the offline Task 3A implementation.**

```bash
git add src/aaos/moodle/bootstrap.py src/aaos/moodle/auth.py src/aaos/moodle/api.py src/aaos/moodle/api_parsing.py tests/test_moodle_bootstrap.py tests/test_moodle_site_info.py tests/test_moodle_auth_paths.py tests/test_moodle_api_transport.py tests/test_moodle_api_parsing.py
git commit -m "feat: add Moodle SSO bootstrap and site-info probe"
```

**PRE-AUTH SECURITY GATE:** After the offline Task 3A commit, run the focused
tests and a Terra-high review of callback grammar, passport/siteid validation,
credential-path handling, token redaction, import/network safety, and the
site-info-only operation. Stop and present the exact HEAD, results, and
confirmation that no real authentication, token creation, or authenticated
Moodle request occurred. Request explicit human authorization for the
user-controlled SSO/bootstrap plus the single `core_webservice_get_site_info`
probe. That authorization does not permit entity requests or synchronization.

## Task 3B: Capability-approved Moodle entity read adapter (created only after human approval)

Task 3B is not implementation-authorized by this plan section. It is created
only after the human reviews the safe Task 3A site-info report and approves the
exact available Moodle function names, request schemas, provider identities,
pagination/completeness behavior, and stable source namespace. No production
entity operation names or request mappings are invented before that evidence.

**Files after the gate:**
- Modify: `src/aaos/moodle/api.py`
- Modify: `src/aaos/moodle/api_parsing.py`
- Create or modify: focused entity adapter tests named by the approved operation set

**Interfaces after the gate:**
- Extends the private transport only with application-owned methods whose
  exact names and schemas are copied from the human-approved capability
  report; there is no generic function-name or parameter escape hatch.
- Produces the existing `MoodleApiSource(...).collect(name: str) -> SourceCollection`
  only after all four approved entity scopes and completeness rules are bound.

- [ ] **Step 1: Record the approved function/mapping contract as a plan amendment.** Include the exact Moodle function names, request fields, response schemas, provider-ID mapping, timestamp strategy, and pagination proof. Do not proceed with a guessed mapping.
- [ ] **Step 2: Add RED tests for only that approved operation set.** Cover complete multi-course reads, all four entity types, denial/malformed payloads, pagination failure, safe errors, and no mutation requests.
- [ ] **Step 3: Implement the smallest fixed entity adapter and parser set.** Keep all Task 3A boundaries unchanged; no generic transport, browser fallback, download, or synchronization path is added.
- [ ] **Step 4: Run focused tests, static checks, independent review, and commit only the approved entity files.**

Task 4, Task 5, and Task 6 remain blocked until Task 3B is either completed
from an approved API capability or the separately approved browser route is
selected.

## Task 4: Browser adapter (conditional on Task 2 API insufficiency and explicit Playwright approval)

**Files:**
- Create: `src/aaos/moodle/browser.py`
- Create: `src/aaos/moodle/browser_parsing.py`
- Create: `tests/test_moodle_browser.py`
- Modify: `pyproject.toml` and `uv.lock` only if browser mode is explicitly approved and Playwright is accepted as a runtime dependency.

**Interfaces:**
- Consumes a `ready` browser `MoodleCapability`, `MoodleSettings`, and `AuthPaths.browser_state_dir`.
- Consumes `MoodleBrowserStateLoader.load_state_path()` from Task 1; no ad-hoc profile path or browser-state reader is permitted.
- If API capability is incomplete and the human separately approves Playwright
  for browser data collection, this task may consume a browser state prepared
  by the separately approved authentication-bootstrap path. That bootstrap
  context is distinct from collection: it must not be implemented as hidden
  scraping, and this task must not invent another token or profile-extraction
  mechanism. Task 4 does not implement the optional Playwright token-bootstrap;
  if the system-browser callback is incompatible, that bootstrap requires its
  own plan amendment and human approval before any Playwright code or
  dependency is introduced.
- Produces `MoodleBrowserSource(...).collect(name: str) -> SourceCollection` implementing `AcademicSource`.
- Browser state is loaded only from the dedicated source/origin-bound AAOS path; no ordinary browser profile path is accepted.

- [ ] **Step 1: Write tests against static sanitized HTML and a fake browser seam.** Cover dedicated-profile path enforcement, origin binding, login-page detection with HTTP 200, SSO/MFA redirects, fixed route/selector allowlists, no generic click/type/evaluate methods, no non-GET/HEAD portal requests, DOM mapping, and sanitized failures.
- [ ] **Step 2: Run focused tests to verify RED.**

Run: `uv run pytest tests/test_moodle_browser.py -v`

Expected: failure because browser modules are absent.
- [ ] **Step 3: Before implementation, verify Task 3A site-info evidence and Task 2 did not produce a complete API capability.** If the approved API function set is complete, mark this task not applicable; do not install Playwright. Task 4 never implements the registered-URI token bootstrap; that remains a separately approved Task 3A boundary.
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
- API mode consumes the Task 3B entity adapter only after the exact Moodle
  function set has been human-approved; Task 3A site-info capability alone is
  insufficient for collection.

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
- Uses only human-prepared `MoodleSettings`, the frozen `MoodleCapability`, and
  credentials/session state produced by the explicitly approved bootstrap
  boundary for the selected mode; it has no live-auth shortcut or filesystem
  token/profile discovery.
- Writes only to a fresh isolated temporary SQLite database during the smoke unless a separate explicit human authorization is recorded for normal-database sync.

**PRE-LIVE HUMAN AUTHORIZATION GATE:** After Task 3B (or the approved browser
adapter) and Tasks 4–6 are complete, and before any academic entity request or
opt-in real-portal collection, run the selected-mode focused suite,
normalization/integration tests, complete-snapshot failure tests,
credential/auth-path tests, redirect credential-forwarding tests for API mode or
dedicated profile/origin-binding tests for browser mode, read-only route tests,
generic-surface and mutation-request review, import-time I/O/network tests,
redaction review, `git ls-files data/auth data/credentials`, `git diff --check`,
and clean-worktree verification. A Terra high focused security review must pass
against the selected adapter. Then stop and present the selected mode,
capability summary, exact HEAD, deterministic results, review result, remaining
external prerequisites, and confirmation that no academic entity request has
occurred. The separate Task 3A human authorization may already have permitted
the registered-URI bootstrap and the single site-info probe; it does not permit
entity requests or synchronization. Request explicit human authorization to
run the real read-only academic smoke. Do not create or prepare collection
credentials/session state before the applicable authorization.

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
- Task 2 always stops for explicit human capability approval before Task 3A/4.
- Task 3A stops after offline review and requires separate human authorization
  before registered-URI bootstrap and the single site-info request.
- Task 3B is created only after safe site-info evidence and exact API-function
  approval; no entity function names are guessed in advance.
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
