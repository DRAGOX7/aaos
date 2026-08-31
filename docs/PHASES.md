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

- **PLANNED** — Scope is documented; implementation has not begun.
- **IN PROGRESS** — Implementation or acceptance work is actively underway.
- **IMPLEMENTED** — Code and deterministic verification are complete, but explicit human acceptance is not complete.
- **ACCEPTED** — Required verification is complete and explicit human acceptance is recorded.
- **FROZEN** — An accepted state is identified by a named immutable Git tag targeting the accepted SHA.

**FROZEN** implies **ACCEPTED**.

## Current roadmap status

- Phase 0 — **ACCEPTANCE UNRESOLVED**.
- Phase 1 — **FROZEN** at `phase-01-passed` / `8728aee00f4d6cddc8d1cc9b8f1d41a1c4684fef`.
- Phase 2 — **ACCEPTED, NOT FROZEN** at `736c8498ab1fe2c1d4d425ba4625774eaf791e93`.
- Phase 3 — **FROZEN** at `phase-03-passed` / `12a27f7a77ed79a3e638d9fc6bf4509840a9b326`.
- Phase 4 — **IN PROGRESS**.
- Phases 5–9 — **PLANNED / NOT STARTED**.

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

**Current status: IN PROGRESS.**

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

**Current status: PLANNED / NOT STARTED.**

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

## PHASE 6 — Approval Engine, Google Calendar, and GitHub MCP

**Current status: PLANNED / NOT STARTED.**

### Purpose

Introduce carefully controlled external writes.

### Core work

- Prepare, approve, and commit action lifecycle.
- Immutable action payload.
- Payload hashing.
- Proposal expiry.
- Remote-state revalidation.
- Idempotency.
- Google Calendar official API.
- Official GitHub MCP integration.
- GitHub starts read-only.
- Narrowly scoped write tools only after approval.

### Acceptance gate

- No external write occurs without approval.
- An approved payload cannot change.
- Expired proposals cannot execute.
- Duplicate Calendar events are prevented.
- GitHub begins read-only.
- Credentials are never exposed to the model or frontend.
- Retries do not duplicate completed writes.

## PHASE 7 — Next.js Dashboard and Telemetry

**Current status: PLANNED / NOT STARTED.**

### Purpose

Build the user interface only after the backend workflows are proven.

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

## PHASE 8 — Restricted IPython REPL and Multimodal Vision

**Current status: PLANNED / NOT STARTED.**

### Purpose

Add advanced dynamic computation and visual-document understanding after the controlled agent architecture is stable.

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

## PHASE 9 — Hardening, Evaluation, and Portfolio Release

**Current status: PLANNED / NOT STARTED.**

### Purpose

Turn the working system into a defensible, portfolio-quality engineering project.

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
