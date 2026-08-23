# Phase 2 Restricted Moodle Browser Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add a typed, restricted Moodle browser source for JUST after its mobile-token API bootstrap has been recorded as server-policy blocked.

**Architecture:** JUST uses a dedicated persistent Microsoft Edge profile in two separate operations: human-controlled session provisioning and restricted collection. The collection adapter owns fixed routes, interception, parsers, completeness evidence, and cleanup behind the existing MoodleReadSource seam. Only collect_complete() can grant complete-snapshot authority.

**Tech Stack:** Python 3.11, existing dataclasses and protocols, pytest, Ruff, mypy, existing SQLAlchemy/Alembic unchanged, Playwright 1.62.0 only after Slice 5 approval, and installed stable Microsoft Edge through the Playwright msedge channel.

**Spec:** docs/superpowers/specs/2026-08-14-phase-2-real-moodle-read-only-sync-design.md. This is the focused browser-connector successor plan for that accepted Phase 2 design.

## Global Constraints

- JUST mobile-token bootstrap is SERVER_POLICY_BLOCKED because its effective return scheme is moodlemobile, not aaos. Do not register, emulate, inspect, or take over moodlemobile.
- The API connector remains a supported explicit option for other Moodle deployments. Browser mode is selected by a human-approved capability record; neither mode auto-falls back.
- Human users alone enter JUST/Microsoft credentials and MFA in a headed, AAOS-owned persistent Edge profile. AAOS never reads, fills, logs, exports, or otherwise inspects authentication fields, cookies, browser storage, tokens, screenshots, traces, or network bodies.
- Never use an ordinary Chrome/Edge profile, attach to a running ordinary browser, export storage_state.json, or copy browser state.
- Public application interfaces contain no Page, BrowserContext, generic URL, click, type, selector, evaluate-JavaScript, or arbitrary browser tool.
- Collection installs context-wide interception before page creation, blocks service workers, accepts downloads false, permits only GET/HEAD to module-owned routes/assets, and fails closed on every other request or browser event.
- Approved initial route classes are COURSE_INDEX, COURSE_DETAIL, ASSIGNMENT_LIST, ANNOUNCEMENT_LIST, and course-page RESOURCE_METADATA. Do not add an endpoint based on guessed JUST markup.
- Forbidden initial paths include assign/view.php, forum/discuss.php, resource/view.php, pluginfile.php, messages, notifications, preferences, grading, enrollment, logout, dashboards, and arbitrary AJAX endpoints.
- Moodle content is untrusted input. Parsers extract typed fields only; no Moodle content is sent to an LLM.
- My Courses may create a Moodle page-view event; course pages can create course-view events; resource views can trigger completion. Phase 2 claims no write-intent action or unapproved request, not zero remote state change.
- Identities are numeric course ID, assign-cm:<cmid>, forum discussion ID, and resource-cm:<cmid>. No title, position, CSS class, timestamp, or title hash fallback is allowed.
- Auth, session, redirect, policy, parser, pagination, parent, identity, timestamp, and completeness failures return SourceCollectionFailure before normalization or persistence.
- Profile state remains under ignored data/auth/moodle/<validated-alias>/. It never enters Git, logs, CLI output, model context, or SQLite.
- No file downloading, RAG, token workaround, WebView2 change, system-browser bootstrap change, entity API call, database sync, retry loop, or Phase 3 work is in scope.
- Leave the pre-existing untracked reflex path untouched.

## Planned File Structure

| Path | Responsibility |
| --- | --- |
| docs/superpowers/specs/2026-08-14-phase-2-real-moodle-read-only-sync-design.md | Record selected JUST browser path and accurate read-state caveat. |
| src/aaos/moodle/browser_models.py | Closed route, profile, event, request, and completeness-proof types. |
| src/aaos/moodle/browser_policy.py | Pure URL, asset, request, and redirect policy. |
| src/aaos/moodle/browser_parsing.py | Fixture-driven parsers with no Playwright import. |
| src/aaos/moodle/browser_navigation.py | Private navigator protocol and its Playwright binding. |
| src/aaos/moodle/browser_runtime.py | Safe msedge capability check. |
| src/aaos/moodle/browser_auth.py | Headed human session provisioner. |
| src/aaos/moodle/browser.py | MoodleBrowserSource and course-only smoke composition. |
| src/aaos/moodle/auth.py; models.py | Replace storage-state assumptions with confined persistent-profile paths. |
| tests/test_moodle_browser_*.py | Synthetic contract, parser, policy, runtime, auth, source, and integration tests. |
| tests/live/test_moodle_browser_course_discovery_smoke.py | Default-skipped human-authorized course-only smoke. |

---

### Slice 1: Freeze Phase 2 browser security contracts

**Goal:** Make the selected JUST browser path authoritative without runtime changes.

**Files:**

- Modify: docs/superpowers/specs/2026-08-14-phase-2-real-moodle-read-only-sync-design.md
- Modify: docs/superpowers/plans/2026-08-14-phase-2-real-moodle-read-only-sync-implementation.md
- Modify: this plan only when a human-approved correction is necessary

**Contracts:**

- API remains supported but JUST mobile bootstrap is SERVER_POLICY_BLOCKED.
- Browser mode is explicit; no automatic API-to-browser switch exists.
- First real browser work is course discovery only and cannot call collect_complete().

- [ ] **Step 1: Write the failing documentation checklist.** Require the diff to state SERVER_POLICY_BLOCKED, msedge, dedicated persistent profile, separate auth/collection operations, no storage-state export, route restrictions, and My Courses incidental view state.
- [ ] **Step 2: Perform the RED review.** Compare the old design/plan to that checklist and list every absent contract.
- [ ] **Step 3: Amend only those Phase 2 sections.** Preserve the API connector and existing WebView2/system-browser evidence; do not authorize moodlemobile takeover, cookies, token extraction, or generic browser automation.
- [ ] **Step 4: Verify.**

Run: git diff --check; git diff -- docs/superpowers/specs/2026-08-14-phase-2-real-moodle-read-only-sync-design.md docs/superpowers/plans/2026-08-14-phase-2-real-moodle-read-only-sync-implementation.md

Expected: documentation-only change; no dependency, source, test, credential, or reflex modification.

- [ ] **Step 5: Review gate.** Terra focused documentation/security review must pass.
- [ ] **Step 6: Commit.**

~~~
git add docs/superpowers/specs/2026-08-14-phase-2-real-moodle-read-only-sync-design.md docs/superpowers/plans/2026-08-14-phase-2-real-moodle-read-only-sync-implementation.md docs/superpowers/plans/2026-08-23-phase-2-restricted-moodle-browser-connector-implementation.md
git commit -m "docs: select restricted Moodle browser connector"
~~~

**Completion criteria:** The selected path and its exclusions are consistent across Phase 2 documents.

### Slice 2: Pure profile, request-policy, telemetry, and proof contracts

**Goal:** Create closed, pure-Python browser contracts before any browser runtime exists.

**Files:**

- Create: src/aaos/moodle/browser_models.py
- Create: src/aaos/moodle/browser_policy.py
- Modify: src/aaos/moodle/models.py
- Modify: src/aaos/moodle/auth.py
- Create: tests/test_moodle_browser_models.py
- Modify: tests/test_moodle_auth_paths.py

**Interfaces:**

~~~
BrowserRoute: TypeAlias = Literal[
    "COURSE_INDEX", "COURSE_DETAIL", "ASSIGNMENT_LIST",
    "ANNOUNCEMENT_LIST", "RESOURCE_METADATA",
]
BrowserEvent: TypeAlias = Literal[
    "BROWSER_AUTH_REQUIRED", "BROWSER_AUTHENTICATED",
    "COURSE_COLLECTION_STARTED", "COURSE_COLLECTION_COMPLETE",
    "ASSIGNMENT_COLLECTION_COMPLETE", "COLLECTION_FAILED", "SESSION_EXPIRED",
]

@dataclass(frozen=True, slots=True)
class BrowserProfilePaths:
    profile_dir: Path
    lock_path: Path
    binding_path: Path

@dataclass(frozen=True, slots=True)
class PaginationProof:
    route: BrowserRoute
    visited_page_numbers: tuple[int, ...]
    exhausted: bool
    expected_record_count: int | None
    observed_record_count: int

def resolve_browser_profile_paths(paths: AuthPaths, expected_origin: str) -> BrowserProfilePaths: ...
def acquire_browser_profile_lock(paths: BrowserProfilePaths) -> BrowserProfileLease: ...
def build_browser_url(route: BrowserRoute, *, base_origin: str, identifier: int | None = None) -> str: ...
def classify_browser_request(method: str, url: str, *, base_origin: str) -> BrowserRequestDecision: ...
def validate_pagination_proof(proof: PaginationProof) -> None: ...
~~~

The profile directory is exactly browser_state_dir / playwright-edge-profile. The non-secret binding marker contains only schema version, expected origin, and validated alias. Retire MoodleBrowserStateLoader.load_state_path() from browser collection: storage_state.json is forbidden.

- [ ] **Step 1: Write RED tests.**

~~~
def test_profile_paths_reject_parent_escape(tmp_path: Path) -> None: ...
def test_request_policy_blocks_post_and_unknown_ajax() -> None: ...
def test_pagination_proof_requires_contiguous_unique_pages_and_exhaustion() -> None: ...
~~~

Test a Windows reparse point/symlink escape, a foreign lock, a second profile owner, non-HTTPS origin, extra query parameter, non-decimal ID, unapproved asset, and unsafe event payload.

- [ ] **Step 2: Run RED.**

Run: uv run pytest tests/test_moodle_browser_models.py tests/test_moodle_auth_paths.py -v

Expected: FAIL because browser contracts are absent.

- [ ] **Step 3: Implement only the pure contracts.** Confine resolved profile, marker, and lock paths to browser_state_dir with explicit component checks. On Windows reject symlinks/reparse points. Acquire the exact lock atomically; a pre-existing live/unknown lock returns BrowserProfileBusyError and is never removed. The lease removes only the lock it created.
- [ ] **Step 4: Implement fixed URL/request rules.** COURSE_INDEX takes no ID; COURSE_DETAIL and ASSIGNMENT_LIST take one positive decimal course ID; ANNOUNCEMENT_LIST takes one positive decimal forum-module ID; RESOURCE_METADATA is non-navigable. Require exact HTTPS origin, /current application prefix, approved path/query schema, and fixed safe asset extensions. Return fixed codes only.
- [ ] **Step 5: Verify.**

Run: uv run pytest tests/test_moodle_browser_models.py tests/test_moodle_auth_paths.py -v; uv run ruff format --check src/aaos/moodle/browser_models.py src/aaos/moodle/browser_policy.py src/aaos/moodle/models.py src/aaos/moodle/auth.py tests/test_moodle_browser_models.py tests/test_moodle_auth_paths.py; uv run ruff check src/aaos/moodle/browser_models.py src/aaos/moodle/browser_policy.py src/aaos/moodle/models.py src/aaos/moodle/auth.py tests/test_moodle_browser_models.py tests/test_moodle_auth_paths.py; uv run mypy src/aaos/moodle/browser_models.py src/aaos/moodle/browser_policy.py src/aaos/moodle/models.py src/aaos/moodle/auth.py tests/test_moodle_browser_models.py tests/test_moodle_auth_paths.py

Expected: PASS without Playwright imports or non-test filesystem state.

- [ ] **Step 6: Review gate and commit.** Terra review covers path/reparse safety, profile-lock ownership, no enumeration, fixed errors, and no secrets. Commit with: feat: add Moodle browser safety contracts.

**Completion criteria:** No arbitrary route can be represented; profile confinement and completeness evidence are independently tested.

### Slice 3: Sanitized fixture parsers

**Goal:** Define identity and pagination parsing with synthetic fixtures before any navigation.

**Files:**

- Create: src/aaos/moodle/browser_parsing.py
- Create: tests/test_moodle_browser_parsing.py
- Create: tests/fixtures/moodle_browser/course_index_complete.html
- Create: tests/fixtures/moodle_browser/course_index_schema_drift.html
- Create: tests/fixtures/moodle_browser/assignment_list_complete.html
- Create: tests/fixtures/moodle_browser/announcement_list_complete.html
- Create: tests/fixtures/moodle_browser/resource_metadata_complete.html

**Interfaces:**

~~~
@dataclass(frozen=True, slots=True)
class ParsedPage(Generic[T]):
    records: tuple[T, ...]
    proof: PaginationProof

def parse_course_index(html: str, *, expected_origin: str) -> ParsedPage[RawCourse]: ...
def parse_assignment_list(html: str, *, course_source_id: str, expected_origin: str) -> ParsedPage[RawAssignment]: ...
def parse_announcement_list(html: str, *, course_source_id: str, expected_origin: str) -> ParsedPage[RawAnnouncement]: ...
def parse_resource_metadata(html: str, *, course_source_id: str, expected_origin: str) -> tuple[RawRemoteFile, ...]: ...
~~~

Use Generic and TypeVar, not Python 3.12 PEP 695 syntax. Parsers may use only the standard library or existing dependencies. No selector is claimed to match JUST until a sanitized fixture and later real smoke prove it.

- [ ] **Step 1: Write course-parser RED tests.** A semantic course URL with id=42 maps to source ID "42"; duplicate or missing numeric IDs fail; wrong origin fails; a page without terminal-pagination evidence is incomplete.
- [ ] **Step 2: Run RED.**

Run: uv run pytest tests/test_moodle_browser_parsing.py -k course -v

Expected: FAIL because parser functions are absent.

- [ ] **Step 3: Implement course parsing.** Use semantic links/data attributes from the fixture contract only. Do not identify by displayed title/order. Do not parse a present timestamp without an approved authoritative portal timezone.
- [ ] **Step 4: Add RED tests and parsers for the other entities.** Assignment IDs are assign-cm:<cmid>. Announcement IDs are discussion IDs from canonical discuss.php?d=<id> references, but only when a fixture proves the parent is an announcement forum. Resource IDs are resource-cm:<cmid> from course-page metadata, never from a downloaded target.
- [ ] **Step 5: Add failure tests.** Cover selector drift, duplicate child ID, wrong parent, title-only identity, malformed/non-decimal ID, unsafe pluginfile locator, ambiguous date, and unrecognized pagination. Each failure is fixed and contains no HTML excerpt.
- [ ] **Step 6: Verify.**

Run: uv run pytest tests/test_moodle_browser_parsing.py -v; uv run ruff format --check src/aaos/moodle/browser_parsing.py tests/test_moodle_browser_parsing.py; uv run ruff check src/aaos/moodle/browser_parsing.py tests/test_moodle_browser_parsing.py; uv run mypy src/aaos/moodle/browser_parsing.py tests/test_moodle_browser_parsing.py

Expected: PASS without Playwright, live Moodle, or private content.

- [ ] **Step 7: Review gate and commit.** Terra review validates fixture-only selectors, safe IDs, no title fallback, no private test data, and no raw-content errors. Commit with: feat: add Moodle browser fixture parsers.

**Completion criteria:** All four pure parser families fail closed and contain no navigation capability.

### Slice 4: Mock navigation and negative read-only tests

**Goal:** Prove policy enforcement with an injected navigator before adding Playwright.

**Files:**

- Create: src/aaos/moodle/browser_navigation.py
- Create: tests/test_moodle_browser_navigation.py

**Interfaces:**

~~~
class RestrictedBrowserNavigator(Protocol):
    def open(self, route: BrowserRoute, *, identifier: int | None = None) -> BrowserDocument: ...
    def close(self) -> None: ...

@dataclass(frozen=True, slots=True)
class BrowserDocument:
    route: BrowserRoute
    final_origin: str
    final_path: str
    content_type: str
    html: str
~~~

The fake navigator records only synthetic route enums, HTTP method, and fixed outcome code. It never records headers, cookies, raw URLs, or non-fixture bodies.

- [ ] **Step 1: Write RED tests.** Cover popup, POST, unlisted AJAX GET, login/SSO redirect, resource-view route, downloads, file chooser, WebSocket, beacon, extra query key, foreign origin, and wrong content type.
- [ ] **Step 2: Run RED.**

Run: uv run pytest tests/test_moodle_browser_navigation.py -v

Expected: FAIL because the restricted navigator is absent.

- [ ] **Step 3: Implement PolicyCheckedNavigator.** Validate route before delegation and final origin/path/content type after delegation. Map login/SSO states to BROWSER_AUTH_REQUIRED or SESSION_EXPIRED. It has no arbitrary URL/selector parameter.
- [ ] **Step 4: Verify.**

Run: uv run pytest tests/test_moodle_browser_models.py tests/test_moodle_browser_navigation.py -v; uv run ruff check src/aaos/moodle/browser_navigation.py tests/test_moodle_browser_navigation.py; uv run mypy src/aaos/moodle/browser_navigation.py tests/test_moodle_browser_navigation.py

Expected: PASS without a browser binary or network request.

- [ ] **Step 5: Review gate and commit.** Terra security review confirms no generic browser surface. Commit with: feat: enforce Moodle browser navigation policy.

**Completion criteria:** Every unsafe navigation outcome has a deterministic rejection before runtime integration.

### Slice 5: Playwright dependency and msedge capability gate

**Goal:** Add the approved runtime dependency only after the pure boundary is review-clean, then detect Edge safely.

**Files:**

- Modify: pyproject.toml
- Modify: uv.lock
- Create: src/aaos/moodle/browser_runtime.py
- Create: tests/test_moodle_browser_runtime.py

**Interfaces:**

~~~
@dataclass(frozen=True, slots=True)
class EdgeCapability:
    channel: Literal["msedge"]
    available: bool
    version: str | None
    code: Literal["READY", "EDGE_UNAVAILABLE", "EDGE_POLICY_BLOCKED"]

class BrowserRuntime(Protocol):
    def inspect_msedge(self) -> EdgeCapability: ...
~~~

- [ ] **Step 1: Dependency approval gate.** Confirm browser mode is selected and Slices 1-4 are review-clean. Without explicit approval, stop before touching dependency metadata.
- [ ] **Step 2: Write RED runtime tests using an injected Playwright factory.** Prove only msedge is representable, launch/version failure maps to a fixed code, and capability inspection does not create a page, profile, network request, or artifact.
- [ ] **Step 3: Add exactly playwright==1.62.0 as a runtime dependency.** Update uv.lock via the approved uv workflow and inspect its diff. Do not run playwright install or download bundled browsers: installed Edge is the selected browser.
- [ ] **Step 4: Implement inspection.** Open no page; obtain only safe Edge availability/version through the runtime seam; close deterministically; redact native exceptions.
- [ ] **Step 5: Verify.**

Run: uv lock --check; uv sync --locked --all-groups; uv run pytest tests/test_moodle_browser_runtime.py -v; uv run ruff format --check .; uv run ruff check .; uv run mypy src tests; git diff --check

Expected: reproducible environment; no profile or browser artifact.

- [ ] **Step 6: Review gate and commit.** Terra supply-chain/integration review verifies exact pin, Edge-only channel, and no bundled browser download. Commit with: build: add approved Playwright browser runtime.

**Completion criteria:** Edge capability is a safe typed prerequisite, not an authentication or collection action.

### Slice 6: Dedicated persistent-profile human authentication

**Goal:** Provision or refresh only the AAOS session without token handling or academic collection.

**Files:**

- Create: src/aaos/moodle/browser_auth.py
- Modify: src/aaos/moodle/auth.py
- Modify: src/aaos/cli.py
- Create: tests/test_moodle_browser_auth.py
- Modify: tests/test_moodle_auth_paths.py
- Modify: tests/test_doctor_cli.py

**Interfaces:**

~~~
class BrowserSessionProvisioner(Protocol):
    def provision(self, *, settings: MoodleSettings, capability: MoodleCapability) -> BrowserProvisionResult: ...

BrowserProvisionCode: TypeAlias = Literal[
    "BROWSER_AUTHENTICATED", "BROWSER_AUTH_REQUIRED", "USER_CANCELLED",
    "SESSION_EXPIRED", "HOST_FAILURE",
]

@dataclass(frozen=True, slots=True)
class BrowserProvisionResult:
    code: BrowserProvisionCode
~~~

The narrow command is aaos moodle browser-auth. It creates the dedicated headed Edge context, opens only the canonical Moodle login entry, waits for a validated COURSE_INDEX final state, writes the non-secret binding marker, and closes. It never uses a passport, receiver, token, callback, API, course parser, or storage-state export.

- [ ] **Step 1: Write RED fake-runtime tests.** Authenticated COURSE_INDEX returns BROWSER_AUTHENTICATED; login/SSO result returns BROWSER_AUTH_REQUIRED; close returns USER_CANCELLED; timeout returns SESSION_EXPIRED; host fault returns HOST_FAILURE. Assert no fill/type/evaluate/cookies/storage_state/screenshot/trace API appears or is called.
- [ ] **Step 2: Run RED.**

Run: uv run pytest tests/test_moodle_browser_auth.py tests/test_moodle_auth_paths.py tests/test_doctor_cli.py -v

Expected: FAIL because provisioner/CLI are absent.

- [ ] **Step 3: Implement bounded provisioner.** Acquire exact profile lock, launch headed persistent msedge at the confined directory with downloads disabled and artifacts disabled, navigate only to module-owned login entry, identify success only from validated final COURSE_INDEX plus a fixture-proven non-secret sentinel, close context, release owned lease. Never kill Edge by global process name.
- [ ] **Step 4: Verify.**

Run: uv run pytest tests/test_moodle_browser_auth.py tests/test_moodle_auth_paths.py tests/test_doctor_cli.py -v; uv run ruff check src/aaos/moodle/browser_auth.py src/aaos/moodle/auth.py src/aaos/cli.py tests/test_moodle_browser_auth.py tests/test_moodle_auth_paths.py tests/test_doctor_cli.py; uv run mypy src/aaos/moodle/browser_auth.py src/aaos/moodle/auth.py src/aaos/cli.py tests/test_moodle_browser_auth.py tests/test_moodle_auth_paths.py tests/test_doctor_cli.py

Expected: PASS with fake runtime; fixed-code-only output.

- [ ] **Step 5: Review gate and commit.** Terra-high review covers human-auth separation, profile confinement, lifecycle cleanup, and CLI privacy. Commit with: feat: add dedicated Moodle browser authentication.

**Completion criteria:** A human can prepare a dedicated profile with no credential automation, token operation, or course collection.

### Slice 7: Restricted Playwright collection navigator

**Goal:** Bind the pure policy to Playwright without expanding its public surface.

**Files:**

- Modify: src/aaos/moodle/browser_navigation.py
- Modify: src/aaos/moodle/browser_runtime.py
- Create: tests/test_moodle_browser_interception.py

**Interfaces:**

~~~
class PlaywrightRestrictedNavigator(RestrictedBrowserNavigator):
    def open(self, route: BrowserRoute, *, identifier: int | None = None) -> BrowserDocument: ...
    def close(self) -> None: ...
~~~

Before new_page(), create a persistent context with service_workers="block", accept_downloads=False, and a context-wide route handler. The handler consults only classify_browser_request: it continues approved document/static GET/HEAD and aborts every other request.

- [ ] **Step 1: Write RED interception tests with a fake context.** Assert route installation precedes page creation; approved COURSE_INDEX works; POST and unlisted AJAX GET are aborted; pluginfile is aborted; popup/download/file chooser/WebSocket/beacon fail; close affects only the owned context.
- [ ] **Step 2: Run RED.**

Run: uv run pytest tests/test_moodle_browser_interception.py tests/test_moodle_browser_navigation.py -v

Expected: FAIL because PlaywrightRestrictedNavigator is absent.

- [ ] **Step 3: Implement private binding.** Never call click, fill, type, evaluate, storage_state, cookies, or API-request helper. Use only page.goto() with route-builder output, bounded timeout, final URL/content-type validation, and page.content() handed directly to a parser without logging/persistence. Any unexpected redirect fails.
- [ ] **Step 4: Add failures.** Cover HTTP-200 login page, 401/403, timeout, context crash, page close, asset violation, and cleanup fault; none may expose synthetic raw URL/query/body text.
- [ ] **Step 5: Verify.**

Run: uv run pytest tests/test_moodle_browser_interception.py tests/test_moodle_browser_navigation.py tests/test_moodle_browser_runtime.py -v; uv run ruff check src/aaos/moodle/browser_navigation.py src/aaos/moodle/browser_runtime.py tests/test_moodle_browser_interception.py; uv run mypy src/aaos/moodle/browser_navigation.py src/aaos/moodle/browser_runtime.py tests/test_moodle_browser_interception.py

Expected: PASS with fake runtime only.

- [ ] **Step 6: Review gate and commit.** Terra-high review covers interception order, service-worker block, asset policy, popup/download handling, cleanup, and no generic capability. Commit with: feat: enforce restricted Moodle browser requests.

**Completion criteria:** Collection cannot produce an unapproved request through application-facing operations.

### Slice 8: Course-only MoodleBrowserSource

**Goal:** Implement only the source behavior needed for the first real smoke; make complete snapshots structurally impossible.

**Files:**

- Create: src/aaos/moodle/browser.py
- Create: tests/test_moodle_browser_source.py
- Modify: src/aaos/moodle/collection.py only if a generic proof-validation hook is required

**Interfaces:**

~~~
class MoodleBrowserSource(MoodleReadSource):
    def discover_courses(self) -> CourseDiscoveryResult: ...
    def collect_course_entities(self, course: RawCourse) -> EntityBundleResult: ...

def discover_courses_for_smoke(source: MoodleBrowserSource) -> CourseDiscoveryResult: ...
~~~

In this slice discover_courses() is implemented. collect_course_entities() always returns SourceCollectionFailure with code browser_entity_scope_not_approved. The smoke helper calls only discover_courses_for_smoke(), never collect_complete().

- [ ] **Step 1: Write RED source tests.** Complete course index produces CourseDiscovery(is_complete=True); login redirect, pagination failure, and duplicate IDs yield fixed failures; collect_complete(source, ...) cannot create a snapshot because entities are unapproved.
- [ ] **Step 2: Run RED.**

Run: uv run pytest tests/test_moodle_browser_source.py tests/test_moodle_collection.py -v

Expected: FAIL because MoodleBrowserSource is absent.

- [ ] **Step 3: Implement course-only composition.** Emit only fixed safe events and count. Do not emit titles, IDs, URLs, HTML, exceptions, session state, or profile paths.
- [ ] **Step 4: Verify.**

Run: uv run pytest tests/test_moodle_browser_source.py tests/test_moodle_collection.py tests/test_moodle_browser_parsing.py -v; uv run ruff check src/aaos/moodle/browser.py src/aaos/moodle/collection.py tests/test_moodle_browser_source.py; uv run mypy src/aaos/moodle/browser.py src/aaos/moodle/collection.py tests/test_moodle_browser_source.py

Expected: PASS; neither normal nor isolated SQLite is opened.

- [ ] **Step 5: Review gate and commit.** Terra review proves no entity, snapshot, normalization, sync, API, receiver, or token path. Commit with: feat: add course-only Moodle browser source.

**Completion criteria:** In-memory course discovery can be complete while entity collection and persistence remain blocked.

### Slice 9: Local mock-Moodle integration and security regressions

**Goal:** Test the selected path against local synthetic pages before real profile or Moodle activity.

**Files:**

- Create: tests/test_moodle_browser_integration.py
- Create: tests/fixtures/moodle_browser/mock_site/
- Modify: tests/test_moodle_browser_interception.py
- Modify: tests/test_moodle_browser_source.py

**Contracts:** The test-scoped server binds loopback only. It serves only synthetic approved route equivalents and assets; it simulates no Microsoft login, cookie, token, callback, or API.

- [ ] **Step 1: Write RED integration tests.** Complete paginated COURSE_INDEX succeeds and reports only count; injected POST/AJAX fails; HTTP-200 login shape fails; selector drift fails; every failure closes owned context and lease.
- [ ] **Step 2: Run RED.**

Run: uv run pytest tests/test_moodle_browser_integration.py -v

Expected: FAIL until synthetic site/composition exists.

- [ ] **Step 3: Implement test-local synthetic pages only.** Use fictional identifiers/titles; include no JUST URL, user, course, token, cookies, or actual Moodle HTML.
- [ ] **Step 4: Add cross-slice security tests.** Assert public source/CLI signatures contain no Page, BrowserContext, selector, arbitrary URL, click, type, or evaluate. Assert collection code has no storage_state, screenshots, traces, HAR, pluginfile, discuss, resource view, or assign view call. Assert no global Edge process termination.
- [ ] **Step 5: Run the browser integration gate.**

Run: uv run pytest tests/test_moodle_browser_models.py tests/test_moodle_browser_parsing.py tests/test_moodle_browser_navigation.py tests/test_moodle_browser_runtime.py tests/test_moodle_browser_auth.py tests/test_moodle_browser_interception.py tests/test_moodle_browser_source.py tests/test_moodle_browser_integration.py -v; uv run ruff format --check .; uv run ruff check .; uv run mypy src tests; uv run pytest -v; uv lock --check; git diff --check; git ls-files data/auth data/credentials

Expected: all deterministic checks pass with no real browser run and no tracked credential/auth path.

- [ ] **Step 6: Review gate and commit.** Terra-high security review must pass before profile provisioning or real Moodle smoke. Commit with: test: verify restricted Moodle browser connector.

**Completion criteria:** Local evidence proves policy, cleanup, drift, privacy, and course discovery without contacting JUST.

### Slice 10: Pre-live review and first course-discovery smoke

**Goal:** Perform one bounded real validation of session reuse plus COURSE_INDEX parsing, then stop.

**Files:**

- Create: tests/live/test_moodle_browser_course_discovery_smoke.py
- Create: .superpowers/sdd/2026-08-23-phase-2-moodle-browser-connector/phase-2-browser-course-smoke.md as ignored safe evidence
- Modify: src/aaos/cli.py only if an explicit aaos moodle browser-course-smoke command is needed
- Modify: focused files only for a review-proven defect

**Interfaces:**

~~~
BrowserCourseSmokeResult: TypeAlias = Literal[
    "COURSE_DISCOVERY_COMPLETE", "BROWSER_AUTH_REQUIRED", "SESSION_EXPIRED",
    "REQUEST_POLICY_BLOCKED", "PARSER_SCHEMA_DRIFT",
    "PAGINATION_INCOMPLETE", "HOST_FAILURE",
]
~~~

The opt-in smoke requires AAOS_RUN_LIVE_MOODLE_BROWSER_SMOKE=1 and explicit CLI invocation. Its output is fixed result, safe Edge capability code, and course count only.

- [ ] **Step 1: Write the default-skipped smoke test.** It must skip before importing/launching Playwright unless the opt-in variable is set and must prove the smoke cannot call collect_complete, entity routes, normalization, SQLite, receiver, API transport, system-browser host, or WebView2 host.
- [ ] **Step 2: Run default smoke.**

Run: uv run pytest tests/live/test_moodle_browser_course_discovery_smoke.py -v

Expected: one intentional skip and no browser/Moodle activity.

- [ ] **Step 3: Run full pre-live gates.**

Run: uv lock --check; uv sync --locked --all-groups; uv run ruff format --check .; uv run ruff check .; uv run mypy src tests; uv run pytest -v; git diff --check; git ls-files data/auth data/credentials; git status --short --branch

Expected: all gates pass; reflex remains untouched.

- [ ] **Step 4: Human authorization gate.** Present exact candidate commit, Edge capability, permitted route, My Courses view-event caveat, deterministic evidence, and no-entity/no-database proof. Stop until explicit authorization.
- [ ] **Step 5: Execute exactly one authorized smoke.** Human runs aaos moodle browser-auth, completes login/MFA, then separately invokes the smoke. Reopen only the dedicated profile and visit only COURSE_INDEX. On failure, close context, release only owned lock, report fixed result, and stop with no retry.
- [ ] **Step 6: Record safe evidence and stop.** Evidence contains result, Edge capability, count, and cleanup status; no academic content, identifiers, profile path, browser state, or raw errors. Request human approval before any entity-route design/implementation.

**Completion criteria:** The smoke proves only that human-provisioned session reuse and course-index parsing work under the fixed policy. It never proves full Phase 2 collection.

## Entity Expansion Gate

Assignments, announcements, and RemoteFile metadata are not implementation-authorized after this plan's course-only smoke. Each needs a new human-approved amendment with sanitized selector evidence, route/read-state analysis, exact pagination semantics, timestamp policy, identity proof, deterministic tests, and fresh security review. Failure of any required entity preserves the full browser collection blocker; no partial CompleteSourceSnapshot is allowed.

## Plan Self-Review

- Every frozen architecture constraint maps to a slice: documentation (1), confinement/policy/proofs (2), parsers (3), policy tests (4), dependency (5), human auth (6), interception (7), source boundary (8), synthetic integration (9), and the one real course-only smoke (10).
- All planned production examples use Python 3.11 syntax.
- No task adds a generic browser interface, Moodle token workaround, cookie export, normal-profile attachment, database write, download, LLM content access, or Phase 3 feature.
- Playwright is an exact runtime dependency only at Slice 5 after approval; installed Edge is used and no bundled browser download is authorized.
- No JUST selector is invented. Fixture/mock evidence must precede promotion to real JUST support.
- The first live path is intentionally unable to produce CompleteSourceSnapshot.

## Execution Handoff

This plan is ready for human review. Execute one slice at a time with its RED/GREEN verification and stated review gate. Slice 5 is the dependency boundary; Slice 10 is the first real browser boundary.
