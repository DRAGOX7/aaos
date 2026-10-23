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
- Phase 2 — **FROZEN** at `phase-02-passed` / `736c8498ab1fe2c1d4d425ba4625774eaf791e93`.
- Phase 3 — **FROZEN** at `phase-03-passed` / `12a27f7a77ed79a3e638d9fc6bf4509840a9b326`.
- Phase 4 — **FROZEN** at `phase-04-passed` / `e99c494628952e0a1ebcafa2450c87611b4dc6c8`.
- Phase 5 — **PARTIALLY IMPLEMENTED / CORRECTIVE REVIEW REQUIRED**; Slices 1–5 human approved, Slice 6 not yet approved.
- Phase 6A — **IMPLEMENTED / VERIFIED** as the durable approval/execution prerequisite consumed by Phase 6B; no separate freeze tag is recorded here.
- Phase 6B — **FROZEN** at `phase-06b-passed` / `40ff9d3ef6e0e3010982dabfb37cb597aceeb6ed`.
- Phase 6C — **OPTIONAL / NOT PURSUED**.
- Phase 7 — **PASSED / FROZEN** by whole-phase human acceptance; the annotated `phase-07-passed` tag targets the final governance commit. Slices 7A–7G are **HUMAN_ACCEPTED**; no Phase 8 authority follows.
- Phase 8 — **PASSED / FROZEN** by whole-phase human acceptance; Tasks 0–10 are **HUMAN_ACCEPTED** and the annotated `phase-08-passed` tag targets the final governance commit.
- Phase 9 — **PASSED / FROZEN** by whole-phase human acceptance; Tasks 0–9 are **HUMAN_ACCEPTED** and the annotated `phase-09-passed` tag targets the final governance commit.
- Phase 10 — **PASSED / FROZEN** by whole-phase human acceptance on 2026-10-23; the annotated `phase-10-passed` tag targets the acceptance commit `2dfb0fac46c0f1e341556d90759dc9ebf39a5ade`: Moodle change notifications via Telegram. Authorized by the owner on 2026-10-02 with the Phase 0 and Phase 5 gates explicitly deferred. Accepted with two known limitations recorded in the ledger: no real-history replay (10E), which is still open, and no test re-run on the acceptance day, which was closed by the 2026-10-23 re-run (62 notification tests passed). Plan: `docs/superpowers/plans/2026-10-02-phase-10-moodle-change-notifications.md`.
- Phase 11 — **PASSED / FROZEN** by whole-phase human acceptance on 2026-10-23; the annotated `phase-11-passed` tag targets the acceptance commit `6d49a0effb35046d412bcea8f94a1f93b6337fa1`: optional ChatGPT-plan cloud model through Sign in with ChatGPT. Authorized and spec approved on 2026-10-23 (defaults D1–D7), with the Phase 0 and Phase 5 gates still deferred. Evaluation: 86/96 against the local model's 78/96, unanswerable 8/8 (`docs/evaluations/phase11-chatgpt-plan-evaluation-2026-10-23.md`). The local model stays the default. Design note: `docs/superpowers/specs/2026-10-23-chatgpt-plan-model-gateway-design.md`.
- Phase 12 — **AUTHORIZED / SPEC APPROVED; IMPLEMENTATION NOT STARTED**: everyday Calendar proposals and approvals (opt-in, off by default). Authorized and spec approved by the owner on 2026-10-23 (defaults D1–D6, `docs/superpowers/specs/2026-10-23-phase-12-everyday-calendar-approvals.md`), with the Phase 0 and Phase 5 gates still deferred. Coding waits for the owner's go-ahead; D2 is settled on a new dedicated `AAOS` calendar (spec §11.2).

Outside the phase sequence:

- Optional Gemini cloud model — **IMPLEMENTED; HUMAN_APPROVED; VERIFIED** on 2026-10-23 (as built; mock-transport tests re-run, not verified against the live Gemini API). On `main` since 2026-10-04; off by default. Its changes to frozen Phase 4 and Phase 7 code are owner-approved amendments; the freeze tags are not moved. See the 2026-10-23 retroactive record in [GOVERNANCE_LEDGER.md](GOVERNANCE_LEDGER.md).

### Historical status notes

An existing `phase-00-passed` tag points to `55d2293`, a Phase 0
final-acceptance planning commit. Repository evidence does not establish
completed Phase 0G/0H acceptance, so Phase 0 acceptance remains unresolved.
The historical tag is preserved pending explicit governance action.

Phase 2 was accepted at `736c8498ab1fe2c1d4d425ba4625774eaf791e93`. The
`phase-02-passed` freeze tag was added to that revision at project-owner
request on 2026-09-30, so Phase 2 is **FROZEN**.

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

**Current status: FROZEN** at `phase-02-passed` /
`736c8498ab1fe2c1d4d425ba4625774eaf791e93` (freeze tag added at
project-owner request on 2026-09-30).

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

**Current status: PASSED / FROZEN by whole-phase human acceptance; 7A–7G HUMAN_ACCEPTED; the annotated `phase-07-passed` tag targets the final governance commit.**

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
was introduced, and no Phase 7G functionality was added. This accepts 7F
only; Phase 7 remains IN_PROGRESS.

Phase 7G (final integration) records `PHASE_7G=HUMAN_ACCEPTED` at
`ac8cd6b75d4bb6809b3a4bf20e21252c45e31a64`. The accepted boundary closes the
Settings and favicon carryovers without introducing a Settings feature,
migration, provider integration, authority change, model/RAG call, or
external write. Route, mutation, same-origin, import-inert, privacy,
security, accessibility, responsive, canonical-test, and production-browser
evidence passed; the disposable synthetic acceptance fixture was removed
after capture and the tracked worktree was clean. Phase 7 remains IN_PROGRESS
and is not PASSED/FROZEN.

Whole Phase 7 was subsequently HUMAN_ACCEPTED and recorded as
`PHASE_7=PASSED`. The accepted 7G implementation/evidence boundary is
`ac8cd6b75d4bb6809b3a4bf20e21252c45e31a64`, and the preceding 7G governance
checkpoint was `c4058d84e112658acb95044e034d2816b431e112`. Whole-phase
verification recorded 3,044 backend tests passed (5 skipped, 1 warning),
10 import-inert/doctor checks passed, 173 frontend tests passed, and PASS for
Ruff, both canonical mypy gates, uv lock, diff-check, typecheck, lint,
production build, API drift, browser/security audits, and responsive
production acceptance. No unresolved carryovers remain: the Settings route
and favicon gaps were closed in 7G. No real external writes occurred, and
independent review found no Critical or Important findings. The annotated
`phase-07-passed` tag targets the final governance commit; Phase 8 was not
started.

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

**Current status: FROZEN** at `phase-08-passed` / final governance commit.

Whole Phase 8 was human-accepted after Tasks 0–10 verification. The frozen
boundary contains qualified OS-enforced Docker computation isolation,
trusted-only bounded computation, deterministic visual selection/rendering,
typed multimodal vision, immutable visual provenance at Alembic head
`0007_phase8_visual_provenance`, closed passive observability, and deterministic
synthetic evaluation. `DERIVED_VISUAL_EVIDENCE` remains distinct from
`EMBEDDED_PDF`; existing grounding/NLI safeguards remain authoritative. No
Phase 5 authority widening, Phase 6 bypass, provider authority, real external
write, real VLM download, model/provider call, RLM work, or Phase 9 work is part
of this freeze.

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

**Current status: PASSED / FROZEN** at `phase-09-passed` / final governance
commit.

Whole Phase 9 was human-accepted after Tasks 0–9 verification. The accepted
implementation/evidence boundary is `bd31a6089d6409bbb31d9781699eac641a6ec40d`;
the final governance commit is the annotated tag target. Canonical verification
recorded 3,202 backend tests passed, 6 skips, one warning, 43 frontend files
with 173 tests passed, Alembic head `0007_phase8_visual_provenance`, a passing
public-release/security audit, and independent review
`PASS_NO_CRITICAL_OR_IMPORTANT_FINDINGS`. Real model downloads, provider calls,
external writes, and RLM work were zero. The stale `3,044` documentation count
was corrected; `3,202` is the canonical final backend result. Retained Ruff,
mypy, planning-path, `.gitignore`, synthetic/fake, VLM, and frontend-warning
limitations remain explicitly non-blocking. Phase 10 and RLM were not started.

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

## PHASE 10 — Moodle Change Notifications (Telegram)

**Current status: PASSED / FROZEN** (human-accepted 2026-10-23; tag `phase-10-passed`). The owner authorized Phase 10 on 2026-10-02 and explicitly deferred the open Phase 0 and Phase 5 gates. Known limitations are recorded in [GOVERNANCE_LEDGER.md](GOVERNANCE_LEDGER.md).

Plan: `docs/superpowers/plans/2026-10-02-phase-10-moodle-change-notifications.md`.

### Purpose

Notify the owner on Telegram, within a configured interval, when the read-only Moodle sync detects a meaningful change.

### Core work

- **Event derivation:** from the existing COMPLETE→COMPLETE snapshot diff, with stable event keys.
- **Notification service:** outbox, de-duplication, baseline at enablement, digest, escaping, retry.
- **Telegram adapter:** send-only, one allow-listed chat, token outside Git, logs and model context.
- **Manual "Sync Now",** then a scheduled `aaos monitor run-once` that reuses the same service.
- **Operations:** sync and notification history, plus one rate-limited `AUTH_REQUIRED` alert.

### Acceptance gate

- No Moodle writes. No sends outside the linked chat and the closed event set.
- No secret in logs, frontend, model context, Git or Telegram.
- On a replay of real snapshot history:
  - every notification matches a real change;
  - every in-scope change is notified exactly once.
- Unchanged data sends nothing. Failed or incomplete syncs send nothing except one auth alert.
- All earlier gates still pass.

## PHASE 11 — Optional ChatGPT-Plan Cloud Model (Sign in with ChatGPT)

**Current status: PASSED / FROZEN** (human-accepted 2026-10-23; tag `phase-11-passed`). Spec: `docs/superpowers/specs/2026-10-23-phase-11-chatgpt-plan-model.md`, approved with the recommended defaults D1–D7. The local model stays the default. Known limitations are recorded in [GOVERNANCE_LEDGER.md](GOVERNANCE_LEDGER.md).

Design note: `docs/superpowers/specs/2026-10-23-chatgpt-plan-model-gateway-design.md`.

### Purpose

Let the owner's ChatGPT Plus/Pro plan answer AAOS model requests through OpenAI's official Sign in with ChatGPT plan usage. It is an opt-in cloud model behind the existing `ModelGateway` boundary, and the local model stays the default.

### Core work

- **Probe first:** one owner-present sign-in and a few requests in the formats AAOS uses, to settle the preview's open questions before code. These are structured output, the reasoning setting, available models and the host-ID rule.
- **Sign-in flow:** owner-started PKCE sign-in through a one-shot listener on `127.0.0.1`, with ID-token and `chatgpt.tokens.use.direct` scope validation.
- **Credential store:** outside the repository, readable only by the owner, written atomically, and refreshed by a single writer, because refresh tokens are single-use.
- **`ChatGPTPlanGateway`:** fixed endpoints, streamed Responses API calls with `store=false` and no tools. AAOS enforces its own output cap, retries with less on HTTP 400, and maps errors to safe categories.
- **Selection:** a `chatgpt_plan` provider with the same cloud confirmation, `CLOUD MODEL ACTIVE` log and model badge as Gemini.

### Acceptance gate

- No token appears in Git, logs, the frontend, model context or evaluation output.
- No tools or agent features are ever requested. No Codex CLI or reused Codex login.
- The local model remains the default, and moving to the cloud model needs confirmation.
- Plan-limit and signed-out states fail clearly and never fall back to paid billing.
- The frozen Phase I4 answer evaluation runs at least three times on the ChatGPT-plan model, with the results recorded beside the local model's.
- All earlier gates still pass.

## PHASE 12 — Everyday Calendar Proposals and Approvals

**Current status: AUTHORIZED / SPEC APPROVED; IMPLEMENTATION NOT STARTED.** Spec: `docs/superpowers/specs/2026-10-23-phase-12-everyday-calendar-approvals.md`, approved by the owner on 2026-10-23 with the recommended defaults D1–D6. Coding waits for the owner's go-ahead.

Design note: `docs/superpowers/specs/2026-10-23-everyday-calendar-approvals-design.md`.

### Purpose

Let the app the owner runs every day propose Google Calendar events in Ask AAOS, and write each one only after the owner approves it in the dashboard. It reuses the frozen Phase 6A/6B approval and Calendar parts and the Phase 7E Approve and Reject controls without changing them.

### Core work

- **Configuration:** opt-in and off by default.
  - `AAOS_CALENDAR_PROPOSALS=on`.
  - The approver identity through `AAOS_LOCAL_APPROVER_IDENTITY` and `AAOS_LOCAL_APPROVER_CONTEXT_ID`, the names in the Phase 7E amendment.
  - The dedicated calendar ID, service identity and credential path.
  - A partial configuration fails closed at startup.
- **Credentials:** the existing Google service-account key, outside the repository and confined to the Calendar client factory. Writes go only to a dedicated calendar shared with that account, never `primary`.
- **Composition:** the served app builds the Calendar proposal service, the executor and the local principal provider only when the configuration is complete.
- **Flow:** proposal → owner approval → claim → one `CREATE_EVENT` → reconciliation, through the existing paths.
  - `UNKNOWN_OUTCOME` stays honest.
  - Reject writes nothing.

### Acceptance gate

- No Calendar write without a recorded human approval, and no standing or automatic approval.
- `CREATE_EVENT` only, and only to the configured dedicated calendar.
- No credential or approver value in Git, logs, the browser, model context or approval, action and event records.
- Off by default: with the switch unset, the served app behaves exactly as before.
- With the owner present: one proposal approved end to end into the dedicated calendar, and one rejected.
- All earlier gates still pass.
