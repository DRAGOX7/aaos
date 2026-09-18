# AAOS Phase Roadmap

A phase may not begin until every blocking acceptance criterion from the previous phase passes.

A later-phase feature must not be implemented early merely because it is convenient.

Every phase ends with:

1. automated tests;
2. a manual smoke test where appropriate;
3. an acceptance report;
4. a review of failures and limitations;
5. an explicit decision to proceed.

## Status vocabulary

The authoritative evidence-backed status/decision source is
[GOVERNANCE_LEDGER.md](GOVERNANCE_LEDGER.md). Summaries here do not grant
authorization. Its separate IMPLEMENTED, VERIFIED and HUMAN_APPROVED states
supersede the earlier combined vocabulary below; ACCEPTED remains historical
terminology, not evidence of a freeze tag.

- **PLANNED** — Scope is documented; implementation has not begun.
- **IN PROGRESS** — Implementation or acceptance work is actively underway.
- **IMPLEMENTED** — Implementation exists; verification and human approval are separate evidence.
- **ACCEPTED** — Required verification is complete and explicit human acceptance is recorded.
- **FROZEN** — An accepted state is identified by a named immutable Git tag targeting the accepted SHA.
- **AUTHORIZED / SPECIFICATION IN PROGRESS** — The phase is authorized for design and planning; production implementation has not begun.

**FROZEN** implies **ACCEPTED**.

## Current roadmap status

- Phase 0 — **ACCEPTANCE UNRESOLVED**.
- Phase 1 — **FROZEN** at `phase-01-passed` / `8728aee00f4d6cddc8d1cc9b8f1d41a1c4684fef`.
- Phase 2 — **ACCEPTED, NOT FROZEN** at `736c8498ab1fe2c1d4d425ba4625774eaf791e93`.
- Phase 3 — **FROZEN** at `phase-03-passed` / `12a27f7a77ed79a3e638d9fc6bf4509840a9b326`.
- Phase 4 — **FROZEN** at `phase-04-passed` / `e99c494628952e0a1ebcafa2450c87611b4dc6c8`.
- Phase 5 — **PARTIALLY IMPLEMENTED / CORRECTIVE REVIEW REQUIRED**; Slices 1–5 human approved, Slice 6 not yet approved.
- Phase 6A — **IMPLEMENTED / VERIFIED** as the durable approval/execution prerequisite consumed by Phase 6B; no separate freeze tag is recorded here.
- Phase 6B — **FROZEN** at `phase-06b-passed` / `40ff9d3ef6e0e3010982dabfb37cb597aceeb6ed`.
- Phase 6C — **OPTIONAL / NOT PURSUED**.
- Phase 7 — **AUTHORIZED / IMPLEMENTATION IN PROGRESS**; 7A **HUMAN_ACCEPTED** at `61313d0`; 7B **HUMAN_ACCEPTED** at `0f887a2f867648f95e69abe534978a1482f69df1`; 7C **HUMAN_ACCEPTED** at `26e517bb8967527de435ffba11a77ff1c00000a0`; 7D **HUMAN_ACCEPTED** at `ebae43800f6600ef61c07c355c9ad085b1f5f483`; 7E **HUMAN_ACCEPTED** at `459431a11f81d567642b3d531a2db4c2ad4976dc`; 7F **HUMAN_ACCEPTED** at `8f6691ecaeff64612da89187e57bbcf8ba84f16a`; 7G **NOT_STARTED**. Phase 7 remains not PASSED/FROZEN because the Settings route and favicon asset carryovers remain open.
- Phases 8–9 — **PLANNED / NOT STARTED**.

### Historical status notes

An existing `phase-00-passed` tag points to `55d2293`, a Phase 0
final-acceptance planning commit. Repository evidence does not establish
completed Phase 0G/0H acceptance, so Phase 0 acceptance remains unresolved.
The historical tag is preserved pending explicit governance action.

Phase 2 is accepted at `736c8498ab1fe2c1d4d425ba4625774eaf791e93`, but no
historical `phase-02-passed` tag is recorded. It is therefore classified
**ACCEPTED, NOT FROZEN**.

## PHASE 0 — Foundation and Local Runtime

**Current status: ACCEPTANCE UNRESOLVED.**

Phase 0 is decomposed into the following governed subphases:

- **0A — Preflight** — complete.
- **0B — Python + uv Tooling** — complete.
- **0C — FastAPI Foundation** — complete.
- **0D — SQLite + Alembic** — complete.
- **0E — Local Model Gateway** — accepted.
- **0F — Doctor / Hardware Diagnostics** — accepted.
- **0G — Security Baseline Verification** — acceptance evidence not established.
- **0H — Phase 0 Final Acceptance** — acceptance evidence not established.

### Purpose

Establish the reproducible development environment and verify the laptop, Docker, Python, SQLite, FastAPI, LM Studio connectivity, and security baseline.

### Core work

- Python project configuration.
- uv.
- Lint, type, and test tooling.
- FastAPI health endpoint.
- SQLite and Alembic baseline.
- Environment and configuration handling.
- LM Studio connectivity check.
- Hardware and resource inspection.
- Initial threat model.
- `aaos doctor` command.

### Overall Phase 0 acceptance gate

- The environment is reproducible from documented steps.
- The FastAPI health endpoint works.
- The database migration works from a clean state.
- LM Studio can be reached.
- The local model can return schema-valid structured output.
- Git contains no credentials or authentication state.
- Docker configuration validates if Docker is introduced in this phase.

### Phase 0G — Security Baseline Verification

0G is a verification gate, not a new security subsystem. It organizes, tests,
and evidences only security properties already stated in AAOS governance and
approved Phase 0 specifications:

- Git credential and authentication-state hygiene;
- safe logging and error boundaries;
- local-first and default-boundary verification;
- import and startup side-effect guarantees;
- dependency and lock reproducibility;
- validation of untrusted environment, tool, and model observations;
- verification of already-approved execution and security boundaries.

Security functionality owned by Moodle authentication, document ingestion,
RAG, agent approvals, external integrations, dashboards, or the restricted
REPL remains in its owning later phase.

**0G acceptance gate:** the existing Phase 0 security properties are
demonstrated by focused verification and an acceptance report, without adding
new security functionality or changing later-phase boundaries.

### Phase 0H — Phase 0 Final Acceptance

0H is the final integration and acceptance gate, not a feature-development
phase. It verifies all Phase 0 acceptance requirements, reruns deterministic
verification, performs required live/local smoke checks, verifies repository
cleanliness and reproducibility, reviews all Phase 0 evidence, performs the
final whole-Phase-0 review, and obtains explicit human acceptance.

**0H acceptance gate:** only after explicit human acceptance may the
`phase-00-passed` tag be created. Until then, overall Phase 0 remains
incomplete.

## PHASE 1 — Domain Model and Mock Portal

**Current status: FROZEN** at `phase-01-passed` /
`8728aee00f4d6cddc8d1cc9b8f1d41a1c4684fef`.

### Purpose

Build normalized academic domain models and a deterministic mock portal before touching the real university system.

### Core work

- `Course`.
- `Assignment`.
- `Announcement`.
- `RemoteFile`.
- Task and event foundations.
- SQLite repositories.
- `LearningPortalAdapter`.
- `MockPortalAdapter`.
- Sanitized fixtures.
- Idempotent synchronization.

### Acceptance gate

- Fixtures import correctly.
- Synchronization is idempotent.
- Updates generate the expected changes.
- Malformed records are rejected.
- The database matches the expected state.

## PHASE 2 — Real Moodle Authentication and Read-Only Synchronization

**Current status: ACCEPTED, NOT FROZEN** at
`736c8498ab1fe2c1d4d425ba4625774eaf791e93`. No historical
`phase-02-passed` tag is recorded.

### Purpose

Safely connect AAOS to the real Moodle portal without performing remote writes.

### Core work

- Playwright authentication.
- Protected storage state.
- Session validation.
- Investigation of Moodle External Services and API availability.
- Moodle API adapter when possible.
- Playwright or browser fallback.
- Normalized course, assignment, announcement, and file extraction.
- Read-only enforcement.

### Acceptance gate

- Login state works.
- Expired sessions are detected.
- Login-page redirects, including HTTP 200 cases, are detected.
- Extracted records match manual samples.
- No portal mutation requests occur.
- Logs contain no secrets, tokens, or cookies.

## PHASE 3 — File Downloader and Document Ingestion

**Current status: FROZEN** at `phase-03-passed` /
`12a27f7a77ed79a3e638d9fc6bf4509840a9b326`.

### Purpose

Safely ingest newly discovered academic files.

### Core work

- Approved-domain downloads.
- Filename sanitization.
- MIME and size validation.
- Atomic writes.
- SHA-256 deduplication.
- PDF extraction.
- Page metadata.
- Document version tracking.
- Course categorization.

### Acceptance gate

- New files download once.
- Duplicates are prevented.
- Revisions are versioned.
- Path traversal is blocked.
- Disguised or invalid files are rejected.
- Interrupted downloads do not leave corrupt final files.
- Page and source metadata are preserved.

## PHASE 4 — Local RAG

**Current status: FROZEN** at `phase-04-passed` /
`e99c494628952e0a1ebcafa2450c87611b4dc6c8`.

### Purpose

Build the local citation-grounded academic second brain.

### Core work

- Page- and heading-aware chunking.
- Multilingual embeddings.
- Dense retrieval.
- Lexical retrieval.
- Hybrid retrieval.
- Citation construction and validation.
- Arabic and English evaluation.

### Intended initial stack

- BGE-M3.
- Chroma.
- SQLite FTS5.

### Acceptance gate

- English retrieval works.
- Arabic retrieval works.
- Answers contain valid source and page citations.
- Unsupported questions are refused appropriately.
- Malicious instructions in documents remain untrusted data.
- Defined retrieval evaluation thresholds are reached.

## PHASE 5 — Local Agent Orchestrator and Typed Tools

**Current status: PARTIALLY IMPLEMENTED / CORRECTIVE REVIEW REQUIRED.**

Slice 6 is committed but NOT_YET_HUMAN_APPROVED. The Phase 5 plan's C1–C7
gate covers trusted outer task scope, final-output lineage, lifecycle,
sanitizer, effective policy, output contracts and safe backend events. Separate
authorization and human approval are required before Slice 7 or Slice 8
acceptance work. Slice 7 remains conditional/not authorized; absent a justified
approved operation it is NOT_IMPLEMENTED_BY_SCOPE. Phase 4 is not reopened.

### Purpose

Introduce the actual controlled agent loop only after the deterministic substrate and RAG work.

### Core work

- LM Studio client.
- Structured model output.
- Primary harness/orchestrator.
- Task state machine.
- Typed tool registry.
- Permission system.
- Read-only tools.
- Controlled local-write tools.
- Retry, timeout, and step limits.
- Loop detection.
- Prompt-injection boundaries.
- Agent evaluation dataset.

The production REPL remains disabled.

### Acceptance gate

- The defined structured-output validity target is reached.
- The defined tool-selection target is reached.
- Invalid arguments are rejected.
- Unknown tools are rejected.
- Loop limits work.
- Retrieved content cannot create tools or bypass policy.
- Arbitrary host-file access is prevented.

## PHASE 6 — Durable Approval Core and Gated External Integrations

**Current status: 6A IMPLEMENTED / VERIFIED; 6B FROZEN at
`phase-06b-passed` / `40ff9d3ef6e0e3010982dabfb37cb597aceeb6ed`;
6C OPTIONAL / NOT PURSUED.**

### Purpose

Introduce carefully controlled external writes.

The historical pre-Phase-6 sequencing gate is superseded for current status
by the authoritative governance ledger and the frozen Phase 6B evidence. This
does not manufacture Phase 5 acceptance or remove unresolved Phase 5
constraints from later consumers.

### Internal gates

- **6A:** durable approval/execution core, tested first with fake adapters.
- **6B:** one narrow approval-controlled Calendar workflow, after 6A passes.
- **6C:** optional GitHub read-only integration. GitHub writes require a
  separate explicit gate; no implicit write authority follows from read access.

Approval is orthogonal to ordinary capability permission: both must pass.
Neither model text nor observations can approve an action. MCP is transport
and interoperability, not a product capability or prerequisite for internal
tools. Calendar/GitHub transport choices do not bypass the approval core.

Before external writes, an application-owned action record binds actor,
target account/resource, action type, normalized exact arguments, tool/schema/
policy versions, expiry, applicable remote-state/precondition identity,
approval identity, atomic claim/consumption, stable operation/idempotency
identity and execution outcome. Outcomes are CONFIRMED_SUCCESS,
CONFIRMED_FAILURE or UNKNOWN_OUTCOME. Explicit tests/design must cover replay,
concurrent commits, crashes, stale approval and schema-version drift. Timeout
is not proof of failure; unknown outcomes require reconciliation before retry.

### Core work

- Prepare, approve, and commit action lifecycle.
- Immutable action payload.
- Payload hashing.
- Proposal expiry.
- Remote-state revalidation.
- Idempotency.
- Google Calendar official API.
- Optional official GitHub MCP integration if 6C is pursued.
- If 6C is pursued, GitHub starts read-only.
- Narrowly scoped write tools only after approval.

### Acceptance gate

- No external write occurs without approval.
- An approved payload cannot change.
- Expired proposals cannot execute.
- Duplicate Calendar events are prevented.
- If optional 6C is pursued, GitHub begins read-only; otherwise this criterion
  is non-blocking.
- Credentials are never exposed to the model or frontend.
- Retries do not duplicate completed writes.

## PHASE 7 — Dashboard and Operational Visibility

**Current status: AUTHORIZED / IMPLEMENTATION IN PROGRESS; 7A HUMAN_ACCEPTED at `61313d0`; 7B HUMAN_ACCEPTED at `0f887a2f867648f95e69abe534978a1482f69df1`; 7C HUMAN_ACCEPTED at `26e517bb8967527de435ffba11a77ff1c00000a0`; 7D HUMAN_ACCEPTED at `ebae43800f6600ef61c07c355c9ad085b1f5f483`; 7E HUMAN_ACCEPTED at `459431a11f81d567642b3d531a2db4c2ad4976dc`; 7F HUMAN_ACCEPTED at `8f6691ecaeff64612da89187e57bbcf8ba84f16a`; 7G NOT_STARTED.**

### Purpose

Build the user interface only after the backend workflows are proven.

The frozen Phase 6B baseline is the starting point for Phase 7. The exact
written Phase 7 design specification at commit `095979f` was explicitly
human-approved on 2026-09-16. Production implementation proceeds
slice-by-slice through reviewed plans. Optional Phase 6C is not a prerequisite
for this dashboard scope.

Phase 7A (browser/API foundation) passed its technical acceptance gate and was
human-accepted at `61313d0` on 2026-09-16. This does not mark Phase 7
as PASSED/FROZEN; 7B and later slices retain independent plan and acceptance
gates.

Phase 7B (frontend shell/design system and production browser acceptance) was
human-accepted at `0f887a2f867648f95e69abe534978a1482f69df1` on 2026-09-17
after its documented frontend, backend, static, runtime, responsive, and
browser evidence passed. This accepts 7B only; Phase 7 remains not
PASSED/FROZEN.

Phase 7C (academic read-only experience and production browser acceptance)
was human-accepted at `26e517bb8967527de435ffba11a77ff1c00000a0` on
2026-09-18 after its documented implementation, route-boundary correction,
runtime, responsive, and browser evidence passed. This accepts 7C only;
Phase 7 remains not PASSED/FROZEN and 7D is the next independently governed
slice.

Phase 7D (Ask AAOS / validated local RAG experience) was human-accepted at
`ebae43800f6600ef61c07c355c9ad085b1f5f483` on 2026-09-18 after the complete
repository/frontend verification gates, production responsive/browser/security
acceptance, and real local BGE-M3 retrieval, local Qwen3.5-4B generation
through LM Studio, and pinned mDeBERTa NLI validation passed. Grounded
retrieval returned HTTP 200 `ANSWER` with a trusted citation; unsupported
retrieval returned HTTP 200 `INSUFFICIENT_EVIDENCE` with zero citations. No
fabricated answers or citations, external writes, external-provider/network
requirements, or credential leakage occurred; the disposable database/index
were removed and LM Studio residency was cleaned. One Windows local subprocess
required `PYTHONUTF8=1` because the default CP1252 decoder could not decode
its output; this was an environment-only, non-blocking limitation with no
product-code change. This accepts 7D only; Phase 7 remains IN_PROGRESS and 7E
remains NOT_STARTED.

Phase 7E (tasks/approvals operational visibility) was human-accepted at
implementation boundary
`459431a11f81d567642b3d531a2db4c2ad4976dc`, with final acceptance
verification performed at `e3091f52e92cee6d4cbd3ab6d512cd32567b9c9d` on
2026-09-18. Durable `TaskRecord` persistence is present at Alembic head
`0006_phase7e_task_records`; Phase 6 remains authoritative for capability,
approval, claim, execution, reconciliation and `UNKNOWN_OUTCOME`. Trusted
approver context is derived server-side and cannot be supplied by the
browser. Production approve/reject workflows passed, refresh preserved
durable `APPROVED` and `REJECTED` state, duplicate/opposite terminal decisions
returned bounded `APPROVAL_NOT_ACTIONABLE`, and `UNKNOWN_OUTCOME` rendered
distinctly and honestly. Deterministic `FakeExternalAdapter` acceptance was
used; no automatic external-write retry or real external provider write
occurred. The earlier fixture-clock mismatch was an acceptance-fixture defect
and was closed using a new preflight-validated frozen fixture without
production-code changes. No Phase 7F functionality was introduced and the
final tracked worktree was clean. This accepts 7E only; Phase 7 remains
IN_PROGRESS and 7F remains NOT_STARTED.

Phase 7F (browser-safe Activity history and passive System Health) was
human-accepted at `8f6691ecaeff64612da89187e57bbcf8ba84f16a` on 2026-09-18
after the canonical backend/frontend, static, same-origin SSE, responsive,
security, and artifact checks passed. The accepted slice provides
deterministic Activity projection/order/cursors, reconnect/replay/dedup
behavior, exactly-once live rendering, bounded malformed-cursor handling,
heartbeat/disconnect cleanup, passive health reporting, and responsive
Activity/System Health views. No migration, duplicate Activity ledger,
Phase 7E authority change, model/RAG/provider side effect, or external write
was introduced, and no Phase 7G functionality was added. Two pre-existing
global integration carryovers remain for 7G: `CARRYOVER_7G_SETTINGS_ROUTE_GAP`
(Settings navigation points to `/settings` without a matching route; 7G must
implement the intended surface or remove/disable the dead item) and
`CARRYOVER_7G_FAVICON_ASSET_GAP` (the application has no favicon/icon asset and
`/favicon.ico` returns 404). Whole Phase 7 must not pass while either
carryover remains. This accepts 7F only; Phase 7 remains IN_PROGRESS and 7G
remains NOT_STARTED.

The UI consumes authoritative backend contracts and safe versioned events.
Event generation begins in Phase 5; durable approval/execution evidence is
required before Phase 6 writes. Policy must not exist only in React state.

### Core work

- Next.js and TypeScript frontend.
- Course overview.
- Assignments.
- Document browser.
- RAG interface.
- Tasks.
- Approvals.
- Structured telemetry and events.
- Model and system health.
- FastAPI event streaming.

### Acceptance gate

- The production frontend builds.
- Frontend state matches the backend.
- Telemetry reconnect and recovery behavior works.
- The approval UI works.
- Secrets never enter browser responses.
- Failures are visible and actionable.

## PHASE 8 — Optional Vision and Independently Isolated Computation

**Current status: PLANNED / NOT STARTED.**

### Purpose

Add advanced dynamic computation and visual-document understanding after the controlled agent architecture is stable.

### Independent internal tracks

- **8A:** optional read-only visual-document interpretation with provenance,
  selected-page evaluation and explicit model/resource controls.
- **8B:** optional isolated computation, justified only when domain-specific
  deterministic tools are insufficient. Requires separate authorization,
  isolation design and escape/resource/network/credential tests.

Vision does not require code authority. Python namespace restriction alone is
not adequate containment. The computation items below apply only to authorized
8B; vision items only to authorized 8A. Neither track implicitly enables the other.

### Core work

- Ephemeral restricted Python or IPython worker.
- No network by default.
- No credentials.
- Task-specific filesystem.
- CPU, RAM, time, and process quotas.
- Process destruction after each task.
- Image-heavy page detection.
- VLM integration.
- Model loading, unloading, and resource management.
- Diagram, equation, and visual-slide extraction.

### Acceptance gate

- The REPL cannot access the network or credentials.
- The REPL cannot escape the permitted filesystem.
- Quotas and timeouts work.
- The worker is destroyed after execution.
- The VLM works on selected evaluation pages.
- Text and VLM model swapping works within hardware constraints.
- Ordinary PDF pages do not unnecessarily invoke the VLM.

## PHASE 9 — Release Qualification and Portfolio Evidence

**Current status: PLANNED / NOT STARTED.**

### Purpose

Qualify the accepted system for clean setup, recovery demonstration and an
end-to-end accepted demo, with sanitized portfolio evidence, finalized
documentation and honest metrics/limitations. Security, recovery and evaluation
are continuous responsibilities in every owning phase, not work deferred to
Phase 9. The checks below consolidate/revalidate existing evidence rather than
introduce missing security or product capabilities late.

### Core work

- End-to-end tests.
- Portal extraction benchmarks.
- RAG evaluation.
- Agent and tool evaluation.
- Prompt-injection tests.
- Security tests.
- Resource benchmarks.
- Failure recovery.
- Threat-model finalization.
- Academic-integrity policy.
- Sanitized demo data.
- Setup documentation.
- Architecture documentation.
- Demo video and script.
- Portfolio metrics.

### Critical acceptance requirements

- External writes without approval equal zero.
- Secrets exposed in logs or the frontend equal zero.
- All earlier phase gates still pass.
- Clean setup succeeds.
- The end-to-end demo succeeds.
- Documented benchmark and evaluation results exist.
