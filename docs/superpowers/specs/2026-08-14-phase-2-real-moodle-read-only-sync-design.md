# Phase 2 — Real Moodle Authentication and Read-Only Sync Design

## 1. Purpose and scope

Phase 2 adapts one real Moodle installation to the accepted Phase 1 academic
source contract. It collects Course, Assignment, Announcement, and RemoteFile
metadata without any Moodle write, normalizes the result through the existing
Phase 1 boundary, and then uses the existing atomic synchronization service.

```text
Approved Moodle mode (API or browser)
        -> Moodle source adapter
        -> SourceCollection
        -> normalize_collection()
        -> CanonicalSnapshot
        -> AcademicSyncService.sync()
        -> local SQLite
```

This phase is read-only. It does not download RemoteFiles, ingest documents,
send Moodle content to the LLM, expose a generic browser or API tool, or add a
parallel persistence path. Phase 0 and Phase 1 behavior remains frozen at
`phase-00-passed` and `phase-01-passed` respectively.

## 2. Frozen Phase 1 foundation

Phase 2 preserves these contracts:

- the four canonical entities and their Phase 1 persistence schema;
- opaque provider identity behind an exact non-secret `source_system` namespace
  and `(source_system, source_id)` uniqueness per entity type;
- only a `CompleteSourceSnapshot` may reach `normalize_collection()`;
- a partial, failed, or unknown-completeness collection never reaches sync;
- duplicate source identities and invalid parent references fail before writes;
- canonical datetimes are aware UTC, and naive timestamps are rejected; and
- exact-namespace deactivation, upsert, reactivation, and no hard deletion run
  in one transaction.

The Moodle adapter is an `AcademicSource` implementation. It must not create
ORM rows, call `AcademicSyncService.sync()` directly, or bypass normalization.

## 3. Threat and scope boundary

The relevant threats are accidental disclosure of Moodle credentials, cookies,
or private academic metadata; accidental remote mutation; accepting an
incomplete collection as authoritative; and silently changing collection
mechanisms or timestamp interpretation. OS compromise is outside this phase's
threat model. Accidental Git, log, evidence-report, frontend, or model-context
exposure is in scope.

Moodle HTML, API payloads, redirects, provider IDs, URLs, and timestamps are
untrusted observations. The adapter validates and maps them using
application-owned parsers and allowlists. The local LLM has no role in Phase 2
collection and receives neither Moodle content nor authentication material.

## 4. Architecture options

| Option | Benefits | Costs and risks |
| --- | --- | --- |
| API only | Narrow typed requests, simpler pagination and UTC epoch handling, no browser session artifacts. | Many Moodle sites do not expose a suitable token or complete read capability; site-specific External Services differ. |
| Browser extraction only | Works when no approved API capability exists and can reflect the user's portal view. | Higher selector/version drift, session-state sensitivity, harder pagination/completeness proof, and stronger private-artifact controls. |
| Explicit hybrid (recommended) | Uses the API where a human-approved capability proves the required complete read scope; browser remains available for a separately approved capability configuration. | Requires two narrowly scoped adapters and test fixtures, but does not hide a mode change. |

The chosen architecture is explicit hybrid, not automatic fallback. A configured
`api` mode uses only the approved API capability set. A configured `browser`
mode uses only the approved browser extraction path. Capability discovery is a
separate, read-only preparation step that records the selected mode and exact
supported entity scope. An API authentication, permission, network, parsing,
or completeness failure is a failure in API mode; it never switches to browser
mode. The inverse is also true.

## 5. Source namespace and configured scope

Each Moodle source has a configured stable, non-secret namespace such as
`moodle:university-primary:student-all-courses`. The string is an explicit local
alias for one Moodle installation, one user-owned account namespace, and one
complete collection scope; it is not the generic literal `moodle`, a title, a
cookie, a token, a password, or an account identifier supplied by the portal.
For JUST, `/current/` and the observed semester label (`JUST LMS Summer 2026`)
are provisional clues, not a finalized namespace. Authenticated safe site
metadata plus configured operational context must establish an instance
identity before the namespace is frozen; it must not be finalized as
`moodle:just:current`.
Changing the alias creates a different Phase 1 namespace and therefore needs a
human-reviewed migration or operational decision rather than an implicit rename.

Phase 2's initial scope is every course the configured approved mechanism can
prove belongs to that namespace. A course, term, or visibility filter is not
silently applied. A later bounded scope needs its own stable namespace and
completeness definition before it can synchronize. Provider IDs remain opaque
strings. If Moodle exposes only a parent-scoped child ID, the adapter composes a
stable scoped ID before the canonical boundary; titles, URLs, timestamps, and
list position never identify a record.

## 6. Authentication and sensitive session boundary

AAOS never stores or automates entry of a Moodle password. The supported
preparations are a user-provisioned Moodle External Services/API token after an
appropriate read capability is exposed, or a user-controlled interactive
browser/SSO/MFA login that creates local browser session state for browser mode.

Sensitive material is local runtime state, not application configuration. The
designated ignored locations are `data/credentials/moodle/<source-alias>/` for
an API token and `data/auth/moodle/<source-alias>/` for browser storage state or
a dedicated per-source AAOS browser profile. The alias is a sanitized
filesystem-safe form of the configured source namespace; the implementation
must not derive a path from unvalidated portal data. These roots are already
Git-ignored and must not be traversed, printed, uploaded, or placed in fixtures.

The dedicated AAOS profile or storage-state artifact is created by a
user-controlled interactive Moodle/SSO/MFA login for the approved source. The
adapter must not attach to, copy, enumerate, or reuse the user's ordinary
Chrome, Edge, or Firefox profiles, unrelated cookies/accounts/history/local
storage, or another AAOS source's state. Loading state is bound to both the
approved source namespace and Moodle origin; an unexpected origin or unrelated
state is a typed failure.

The user owns initial creation and revocation. The adapter may read only the
selected mode's required local state while collecting; it must not enumerate,
copy, or render it. A 401/403, login page/redirect, SSO/MFA challenge, or
explicit session-expiry signal invalidates the current attempt and yields a
sanitized typed collection failure. It does not delete state automatically or
try a password login. Explicit user-directed local cleanup is the only removal
path proposed for this phase. Token-bearing requests and storage state have
redacted representations only; native URLs, headers, bodies, exceptions, and
traces never reach CLI output, normal logs, evidence, or model context.

### Narrow authentication-bootstrap amendment

JUST deployment evidence shows that a student can authenticate through the
official Moodle app, the Moodle browser login, and Microsoft SSO/MFA, then
return to the app with enrolled courses available. It does not prove that this
user can self-issue a token from Moodle Preferences or that every required API
function is enabled. Moodle documents browser-window SSO for mobile
authentication and a mobile web-service token handoff; these facts make a
user-controlled bootstrap plausible, not approved for execution.

The Phase 2 data-collection mode remains API when its complete read capability
is approved. Authentication preparation is a separate, one-time boundary. The
current Moodle mobile launch protocol is not a generic OAuth callback: its
`admin/tool/mobile/launch.php` flow accepts fixed `service` and `passport`
values plus an optional `urlscheme`, then returns a custom URI of the form
`<scheme>://token=<encoded-app-token>`. A non-empty site `forcedurlscheme`
overrides the requested scheme. Therefore the current design approves only a
registered URI callback, not a loopback HTTP listener:

| Bootstrap option | Advantages | Risks / decision |
| --- | --- | --- |
| A. User-controlled system browser plus an AAOS-owned registered URI callback | No browser automation or new browser dependency; credentials and MFA stay on the trusted SSO page; the callback can hand one bounded app-token envelope directly to the existing credential path. | Must prove that JUST honors the requested AAOS scheme. AAOS never attaches to, copies, or reads a normal browser profile. Recommended if protocol compatibility is proven. |
| B. Dedicated ephemeral Playwright browser for SSO/token callback | Fully controlled ephemeral context and deterministic callback observation. | Adds a dependency and browser security surface, and must not become data scraping. Consider only if option A is technically incompatible and receive separate Playwright approval. |
| C. Another documented Moodle-supported user-controlled mechanism | May avoid custom callback work. | Accept only if repository and official Moodle evidence proves it avoids password automation, private-app storage extraction, and profile reuse. |

Option A is the recommended candidate, conditional on a protocol-compatibility
check. Option B is not currently required. No loopback HTTP callback is
approved for this Moodle flow, and no option may automate password entry, call
`token.php` with credentials, extract a token from the installed Moodle app,
attach to a normal browser profile, or silently turn bootstrap into browser
scraping. If JUST forces `moodlemobile` or any other non-AAOS scheme, bootstrap
is blocked for human review; AAOS must not hijack or register Moodle's official
scheme and must not substitute another mechanism implicitly.

Windows URI-scheme registration for an AAOS-owned scheme is an explicit,
human-controlled installation/setup action. AAOS runtime must not modify the
Windows registry or take over another application's scheme.

### Server-dependency checkpoint

The tested Windows registration and receiver are healthy: a human-initiated
Chrome `aaos://` link presents the external-app prompt and invokes the receiver.
The corresponding delayed automatic navigation from a local page does not
present the prompt or invoke the receiver. Therefore WebView2 remains a tested
experimental host but is frozen for real JUST authentication, and explicit
system-browser authentication is the selected live path.

The system-browser path is currently blocked on two server facts that AAOS
cannot establish or override client-side: `forcedurlscheme` must be blank so
the requested `aaos` scheme is retained, and the JUST Microsoft SSO integration
must preserve Moodle's original `wantsurl` back to `admin/tool/mobile/launch.php`.
Neither fact is known from the accepted public configuration response.

If JUST confirms both facts, the supported conditional workaround is a fixed
`confirmed=1` mobile-launch parameter. Moodle then renders a user-visible
launch-app link after the SSO return; the human explicitly clicks that link to
dispatch `aaos://`. This is intentionally not implemented until confirmation.
It does not authorize MSAL/direct Microsoft OAuth, browser scraping, token
copy/paste, DOM/cookie/network interception, or a loopback callback.

The proposed bootstrap contract for separate human approval is deliberately
narrow:

1. The user initiates the fixed Moodle mobile launch URL for the approved
   origin in a trusted system browser (or, only after separate approval, a
   dedicated ephemeral context). AAOS generates a cryptographically strong
   one-time `passport`, retains it only for this attempt, and supplies the
   module-owned `service`, `passport`, and requested `urlscheme` values. The
   user enters Microsoft credentials and completes MFA directly; AAOS never
   sees password fields or SSO content.
2. The application-owned callback accepts only the exact registered-URI grammar
   `<approved-scheme>://token=<encoded-app-token>`: exact scheme, no userinfo,
   host, port, path, query, fragment, or extra structure, and exactly one
   non-empty token payload. Unexpected scheme, malformed URI shape, duplicate
   token payloads, empty payload, oversized payload, or invalid encoding fails
   closed. The parser does not treat the token as a query parameter.
3. Before decoding, the encoded app-token is bounded to the approved maximum.
   AAOS then performs strict base64 decoding, applies a decoded-size bound, and
   accepts exactly two or three `:::`-separated components:
   `siteid ::: web_service_token` with an optional `private_token`. It computes
   the protocol-defined `MD5(canonical_wwwroot + passport)` site identity from
   the approved canonical Moodle wwwroot and compares it safely with the
   received `siteid`. The MD5 operation is a Moodle compatibility rule, not a
   substitute for a security primitive. Any mismatch, malformed component,
   empty web-service token, or wrong component count fails closed.
4. AAOS extracts only the web-service token, explicitly discards an optional
   private token, and writes only the extracted token to the exact per-source
   `AuthPaths.api_token_path`. It never stores the encoded envelope, siteid,
   private token, raw callback URI, Microsoft session material, or passport
   beyond the attempt. No sensitive value is returned, printed, represented,
   logged, placed in evidence, or sent to the model.
5. Temporary callback/passport state is cleared deterministically on success,
   timeout, cancellation, or validation failure. No academic collection occurs
   during bootstrap. No screenshots, video, tracing, HAR, HTML dumps, console
   capture, or password instrumentation is enabled.

Authentication capability is deliberately split from academic collection:

```text
OFFLINE Task 3A contracts + synthetic tests
    -> pre-auth security review
    -> human authorization for bootstrap + site-info probe
    -> user-controlled Microsoft SSO/MFA and registered-URI callback
    -> extract/store web-service token
    -> exactly one authenticated core_webservice_get_site_info request
    -> safe capability report
    -> human approval of exact entity functions and source namespace
    -> later Task 3B entity adapter
```

The first authenticated probe is limited to the fixed REST
`core_webservice_get_site_info` function. It retains only safe canonical
wwwroot/base identity, release/version when compatibility requires it,
available web-service function names, whether the requested scheme was honored
or overridden, and other non-secret site identity facts. It does not retain
usernames, user IDs unless later justified, names, email, images, keys, tokens,
course content, raw callback URIs, or raw response bodies. No Course,
Assignment, Announcement, or RemoteFile function may run before the separate
human API-function/source-namespace approval.

The protocol decision must be checked against the deployment and the official
Moodle documentation, including [Moodle mobile authentication guidance](https://docs.moodle.org/502/en/Moodle_Mobile_guide_for_admins),
[Mobile Open Auth](https://docs.moodle.org/dev/Mobile_Open_Auth), and [Mobile web services](https://docs.moodle.org/502/en/Mobile_web_services).

### Human-approved capability record

The capability record is the non-secret execution contract between human
preparation and adapter execution. For one configured source it binds the exact
source namespace, approved Moodle base origin, selected mode (`api` or
`browser`), complete collection scope, required Course/Assignment/
Announcement/RemoteFile-metadata scope, approved API functions/endpoints or
browser routes/selectors, pagination/completeness rules, redirect/origin
policy, provider identity mapping, timestamp/timezone strategy, and relevant
capability/version assumptions.

It contains no password, API token, cookie, session identifier, authorization
header, private course content, browser storage state, or credential-bearing
URL. Runtime execution cannot silently expand it. If the actual origin, routes,
capabilities, timezone strategy, or completeness semantics contradict the
record, collection fails closed for human review. This is a small immutable
configuration contract, not a generic policy engine.

## 7. API adapter boundary

Task 3A exposes only the fixed `core_webservice_get_site_info` operation after
the separate human bootstrap-and-probe authorization. It has no entity
operations, guessed Moodle function names, or collection behavior. The probe
receives an injected one-shot authorization capability from the human-gated
caller; Task 3A does not mint that capability itself. It returns only the safe
capability facts listed above and is followed by a stop for human review.

Task 3B is created only after that review approves the exact available Moodle
function names, request schemas, source namespace, and complete read scope. It
then exposes typed, application-owned calls for only those approved functions
for Course, Assignment, Announcement, and RemoteFile metadata. It has no
generic `call(function_name, params)` interface, caller-supplied Moodle
function name, arbitrary URL, or raw response return value. API tokens are
introduced only at the private transport boundary.

The API transport disables automatic redirect following by default. If an
approved capability genuinely requires a redirect, the adapter validates the
target origin and path against the capability record before following it. It
never forwards credentials or tokens to an unapproved origin, and an unexpected
redirect is a sanitized typed failure. Redirect URLs, bodies, and sensitive
query material are not logged or returned.

Every paginated endpoint has explicit termination and expected-total rules.
Missing pages, inconsistent totals, duplicate provider identities, denied
access, unexpected redirect/content type, malformed payload, or an unavailable
required entity capability yields a typed failure. No retry or browser fallback
is implicit. If a bounded retry is later justified, it must be explicitly
designed and cannot change mode or make a failed collection appear complete.

## 8. Browser adapter boundary

Browser mode is a narrowly scoped Playwright adapter for an approved Moodle
site/version and prepared user session. It owns fixed origin allowlists,
application-owned routes/selectors, deterministic navigation/extraction, and
browser cleanup. It exposes no generic click, type, execute-JavaScript, route,
or browser-control API to callers or future agents. Synchronization never fills
credentials, submits a form, clicks mutating controls, or navigates through
actions with write intent.

The adapter allows only approved same-origin pages and validated static assets.
It blocks non-GET/HEAD request methods where interception can reliably do so,
treats any attempted non-GET/HEAD portal request as a typed failure, and
validates the expected origin/path before parsing. HTTP verb alone does not
prove semantic safety, so the stronger boundary is fixed read-oriented routes
plus no action controls or form submission. If the portal cannot be observed
under these constraints, browser mode is unavailable pending human review.

Screenshots, video, traces, HTML dumps, and browser downloads are disabled by
default. Parser functions receive only the DOM text/attributes needed for source
records and remain separate from navigation where practical. A login page, an
SSO/MFA redirect, a changed required page shape, an unexpected origin/redirect,
or a missing page is a sanitized failure rather than a partial collection.

## 9. Read-only enforcement

The API and browser boundaries independently fail closed. They forbid
assignment/quiz/form submission, forum posts, messages, settings/profile
changes, completion marking, enrollment changes, grade changes, uploads,
deletions, calendar creation, and every other externally visible mutation.

Permitted API operations and browser routes are a small static allowlist. No
model, caller, retrieved content, or configuration value can expand it. An
unsupported required operation is a human-review blocker, not an excuse for a
generic API or automation surface. Phase 2 has no external-write approval path
because it has no external writes.

## 10. Entity mapping and safe locators

| Canonical entity | Required Moodle observation | Optional canonical fields |
| --- | --- | --- |
| Course | stable provider ID and title | code, description, source update time |
| Assignment | stable provider ID, parent course ID, title | description, due time, source update time |
| Announcement | stable provider ID, parent course ID, title | body, publication time, source update time |
| RemoteFile | stable provider ID, parent course ID, display name | safe locator metadata, source update time |

Announcements vary by Moodle site. The preparation record must prove that the
selected source is complete for the namespace. Unsupported announcement
semantics make the requested full collection unavailable; they cannot be
silently omitted from a complete snapshot.

RemoteFile is metadata only. A locator is stored only when it is a safe generic
HTTP(S) locator without userinfo, tokens, signed query material, cookies, or
session-bound parameters. The adapter removes unsafe locator data; if a stable
safe locator cannot be formed, it emits `None`. It never downloads a file.

## 11. Complete-snapshot state machine

```text
PREPARED MODE + usable local auth state
        -> authenticate/session check
        -> enumerate the entire configured course scope
        -> collect required entity scope for every discovered course
        -> validate identities, parents, pagination, timestamps, routes, and parsing
        -> build CompleteSourceSnapshot
        -> normalize_collection()
        -> AcademicSyncService.sync()
```

`CompleteSourceSnapshot` may be built only after every required collection step
has succeeded and enumeration is proven complete. A genuinely complete empty
result is valid only when the mechanism positively proves the exact namespace
has no in-scope courses.

Expired/missing auth, login/SSO/MFA redirect, rejected token, denied access,
failed course, failed entity page/endpoint, network error, pagination
inconsistency, duplicate identity, unsupported page shape, unsafe/ambiguous
timestamp, unsafe locator, unexpected redirect, and unknown completeness all
terminate with `SourceCollectionFailure` (or equivalent pre-normalization typed
failure). No failure becomes empty success. The database is unchanged because
normalization is before the existing one transaction.

## 12. Time and timezone handling

The adapter passes only explicit aware timestamps to Phase 1 normalization.
Moodle epoch timestamps map directly to UTC. ISO timestamps require an offset
or `Z`. For browser-rendered date text, the adapter must obtain an authoritative
portal/user timezone from an approved documented Moodle observation and convert
it deterministically, or reject the present timestamp as unsupported. It never
infers the local machine, Jordan, or browser-default timezone.

An actually absent optional field maps to `None`. A present but ambiguous,
malformed, or timezone-less date is not treated as absent; it is a collection
failure because it would silently misrepresent academic time.

## 13. Private-data, logging, and evidence rules

Real Moodle content remains local and is never automatically offered to the
local LLM. Logs and typed errors contain only fixed safe category/capability
information, never course titles, descriptions, announcement bodies, file
names, URLs, raw HTML/JSON, screenshots, tokens, cookies, authorization
headers, or credential paths. Debug artifacts are disabled by default.

Tests and committed fixtures are synthetic. Live-smoke evidence may state mode,
outcome, counts, and redacted identifiers needed to prove behavior, but never
portal content or secrets. Real private academic data persists only through the
existing local SQLite boundary.

## 14. Failure, lifecycle, and cleanup semantics

The source layer uses a finite typed failure vocabulary distinguishing invalid
authentication/session, permission denial, unsupported capability,
network/navigation failure, pagination incompleteness, malformed provider data,
unsafe data, and internal adapter failure. Messages are fixed and redacted.

An attempt owns its temporary API transport or browser context. It closes it
deterministically on success, typed failure, parser failure, and cancellation;
cleanup is idempotent. Browser state is not a success signal: login-page
content and redirect-to-login must be detected even when final HTTP status is
200. A failed attempt never syncs, retries silently, switches mechanisms, or
changes remote Moodle state.

## 15. Testing and live verification strategy

### Offline unit tests

Use synthetic callback URIs, app-token envelopes, API payloads, and static
sanitized HTML. Cover passport/siteid validation, strict base64 and component
counts, encoded/decoded bounds, private-token discard, source IDs and parents,
namespace handling, pagination termination/totals, complete-empty proof,
duplicate IDs, timestamp conversion/rejection, safe URL mapping, typed
failure/redaction, login-page detection including HTTP 200, unexpected
redirects, and API/browser allowlists. Prove that Task 3A performs no entity
request and that no generic API invocation, generic browser control, raw
secret, or private content crosses an adapter boundary.

### Controlled adapter integration tests

Use mocked transport and browser fixtures. Exercise a complete multi-course
collection through `normalize_collection()` and the existing sync service, then
repeat it to prove idempotence. Exercise one-course failure, pagination failure,
expired session, malformed page, and parser failure to prove the database is
unchanged. No university credential, portal, or actual browser login is needed.

### Opt-in real portal smoke

Later acceptance requires explicit human authorization for the registered-URI
bootstrap plus site-info probe, or a separately approved dedicated AAOS browser
session. The first real-portal acceptance is staged:

1. **Real adapter read:** run the selected approved mode and prove complete
   collection for the capability record's exact scope, producing a
   `SourceCollection`. No SQLite synchronization is required merely to prove
   this boundary.
2. **Normalization:** pass that collection through `normalize_collection()` to
   obtain a `CanonicalSnapshot`.
3. **Isolated acceptance database:** apply normal migrations to a fresh,
   disposable SQLite database, synchronize the canonical snapshot through
   `AcademicSyncService`, and repeat it where safe and feasible to prove
   idempotence. This must not alter normal AAOS academic state.
4. **Normal database:** only after the preceding stages pass may the same
   approved complete namespace be synchronized to the normal AAOS database as a
   separately explicit human-authorized acceptance action. No automatic
   promotion from isolated smoke is allowed.

"Minimum scope" means minimum necessary private-data exposure while still
satisfying the exact completeness contract. A deliberately smaller one-course
smoke must use a distinct smoke namespace and isolated database; it can never be
interpreted as a complete result for an all-courses namespace. Evidence contains
only safe summaries. This design task performs no live auth or smoke.

## 16. Proposed implementation files

The later approved plan may add only focused Phase 2 files:

- `src/aaos/config.py` — non-secret Moodle namespace/mode/base-origin settings;
- `src/aaos/moodle/models.py` — typed capability and safe outcome contracts;
- `src/aaos/moodle/auth.py` — private sensitive-state access and typed auth
  outcomes;
- `src/aaos/moodle/bootstrap.py` — registered-URI Moodle mobile bootstrap,
  app-token envelope validation, and token extraction;
- `src/aaos/moodle/api.py` — Task 3A site-info probe, then later fixed
  capability-approved read-only API adapter;
- `src/aaos/moodle/browser.py` — fixed Playwright adapter only if browser mode
  is separately approved;
- `src/aaos/moodle/normalization.py` — Moodle-to-Phase-1 raw-record mapping;
- focused synthetic `tests/test_moodle_*.py` and an opt-in sanitized live smoke.

No migration, academic ORM model, download module, frontend route, generic tool
interface, or Phase 3 scaffolding is proposed. A later plan decides whether
Playwright is a runtime dependency only after preparation proves browser mode is
necessary; API mode alone does not justify installing it.

## 17. Acceptance criteria

Phase 2 is ready for human acceptance only when:

1. the selected API or browser mode is explicitly configured from a
   human-approved capability record; no automatic fallback exists;
2. the registered-URI bootstrap validates the exact Moodle callback grammar,
   passport/siteid app-token envelope, size bounds, and safe token extraction;
   the site-info probe runs only after explicit human authorization and makes
   no entity request;
3. session expiry, token rejection, login-page redirects including HTTP 200,
   SSO/MFA, permission denial, parser failure, course failure, and pagination
   incompleteness all fail closed with no sync;
4. only fixed read operations/routes execute, with no arbitrary Moodle API or
   browser-control surface and no portal mutation request;
5. a complete result normalizes through the frozen Phase 1 contract and syncs
   atomically and idempotently;
6. first real-portal acceptance proves the staged adapter-read, normalization,
   isolated-database sync, and (where feasible) repeat-sync sequence; normal-
   database sync requires separate explicit human authorization;
7. manual samples confirm all four entity types match the approved real scope;
8. timestamps are UTC-safe, unsafe locators never persist, and secrets/private
   content never reach Git, logs, CLI, fixtures, evidence, or model context;
9. API redirects are disabled or target-validated without credential
   forwarding, browser state is dedicated and source/origin-bound, and the
   capability record cannot expand at runtime;
10. the opt-in live smoke uses human-prepared auth, is read-only, and records
   only safe summaries; and
11. all relevant deterministic tests, formatting, lint, type, lock, diff, and
   credential/auth-path hygiene checks pass without weakening prior gates.

## 18. Explicit exclusions

Phase 2 excludes password automation/storage, Moodle write APIs, file download
or ingestion, RAG, embeddings/vector storage, LLM reasoning over Moodle
content, agents, MCP, Calendar, GitHub, dashboards, REPL, vision, and every
Phase 3+ scaffold. It does not assume an API exists, require a university
account change, or introduce enterprise credential management or a process
sandbox subsystem.

## 19. Open decisions requiring human approval

Implementation must not begin until safe evidence resolves:

1. the real Moodle base origin and non-secret stable source-namespace alias;
2. whether an approved API token and complete read capability set exists;
3. if API capability is incomplete, whether browser mode is approved and its
   exact allowed routes/selectors, SSO/MFA preparation, and authoritative
   timezone observation for the portal version;
4. the exact complete collection scope and pagination evidence for the chosen
   mode; and
5. whether browser mode justifies a Playwright dependency after a separate
   dependency and supply-chain decision.

Until these decisions are recorded, Phase 2 remains design-only and no real
account, credential, session state, or Moodle request is used.
