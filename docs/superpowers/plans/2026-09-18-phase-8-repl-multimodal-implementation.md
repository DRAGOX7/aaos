# Phase 8 Restricted REPL and Multimodal Vision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an OS-isolated, read-only computation capability and selective visual-document provenance without weakening the frozen Phase 4–7 authority boundaries.

**Architecture:** Use a trusted AAOS composition root with closed immutable contracts, a fresh container/isolated-worker execution boundary, and typed fake adapters for deterministic tests. Visual analysis is a separate pipeline with deterministic page selection, bounded rendering, explicit model/revision hashes, and an additive provenance layer; it never masquerades as embedded text or external-write authority.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/Alembic, Pydantic/dataclasses following existing AAOS conventions, pytest, Ruff, mypy, LM Studio-compatible typed adapters, pypdfium2, and the reviewed container/WSL isolation runtime. No new model download is permitted by this plan.

**Spec:** `docs/superpowers/specs/2026-09-18-phase-8-repl-multimodal-architecture.md`

## Global Constraints

- Work only in `C:\Users\aljaf\.codex\worktrees\aaos-phase-08` on `phase-08-repl-multimodal`, based on tag `phase-07-passed` at `baca928d6b9e45aa2e26916f50f8f0a8dfc74db5`.
- Do not modify the Phase 7 worktree, frozen Phase 4/5/6 code, or the `phase-07-passed` tag.
- Do not implement RLM, Recursive Language Models, Deep Ask AAOS, multi-agent recursion, or arbitrary autonomous code generation.
- No real Calendar, GitHub, MCP, browser, network, credential, or other external writes.
- No model download. Existing local model inventory may be probed only after a separate human authorization and only through typed test fixtures in ordinary implementation work.
- The model, VLM, visual text, computation code, stdout/stderr, and retrieved text are untrusted data and cannot grant tool, scope, policy, approval, SQL, filesystem, network, credential, or external-write authority.
- The REPL requires a proven OS isolation boundary; in-process IPython, AST/import blacklists, and ordinary subprocesses are rejected for acceptance.
- One computation request creates one fresh worker and one execution attempt. Timeout, crash, or cancellation after execution may have begun is `UNKNOWN_OUTCOME` with no automatic retry.
- Visual-derived text never becomes an embedded-PDF `RagChunkORM.exact_text` or grounded citation without a separately reviewed provenance projection.
- Calibration and heldout fixtures are deterministic, independently authored, frozen before any model probe, and never selected from observed model failures.
- Every task follows RED test → minimal implementation → GREEN tests → static/regression checks → one focused commit.
- Phase 8 acceptance must report zero model downloads, zero external writes, zero normal/production database mutations, and zero Phase 6 approval/claim/execution calls.

---

## Repository map and ownership

Before implementation, the worker reads these files and preserves their
interfaces:

- `src/aaos/agent/contracts.py`, `proposal.py`, `registry.py`, `policy.py`,
  `orchestrator.py`, `observations.py`, `events.py`, and `runtime_policy.py`
  remain the trusted Phase 5 orchestration boundary.
- `src/aaos/documents/pdf.py`, `contracts.py`, and `repository.py` own PDF
  extraction and page geometry.
- `src/aaos/rag/chunking.py`, `contracts.py`, `generation.py`, `indexing.py`,
  and `runtime.py` own exact text spans, citations, and BGE/NLI lifecycle.
- `src/aaos/inference/models.py`, `gateway.py`, `lm_studio.py`, and
  `lm_studio_lifecycle.py` remain text-only unless Task 6’s explicit vision
  adapter is accepted.
- `src/aaos/persistence/database.py` and existing Alembic revisions are the
  only persistence conventions; no `create_all()` path is permitted.

New Phase 8 code is grouped under `src/aaos/phase8/` so computation and
vision contracts do not become accidental Phase 5 public API. The package
contains no browser route in this plan.

## Task 0: Qualify the isolation runtime before code execution

**Files:**
- Create: `tests/phase8/test_isolation_runtime_qualification.py`
- Create: `scripts/phase8/qualify_isolation_runtime.py`
- Test fixture: `tests/phase8/conftest.py`

**Interfaces:**
- Produces `IsolationRuntimeProbe` with fields
  `runtime_id: str`, `network_disabled: bool`, `read_only_root: bool`,
  `credentials_absent: bool`, `task_mount_only: bool`,
  `resource_limits_supported: bool`, and `available: bool`.
- Produces `qualify_isolation_runtime() -> IsolationRuntimeProbe`.
- A false `available` result is a hard implementation/acceptance stop; the
  code cannot select a host subprocess fallback.

- [ ] **Step 1: Write the failing test.** Assert a real probe reports every
  required property, and assert a fake probe with network or host mount access
  is rejected with a fixed `ISOLATION_RUNTIME_NOT_ACCEPTABLE` reason.
- [ ] **Step 2: Run the test to verify it fails.**
  Run `uv run pytest tests/phase8/test_isolation_runtime_qualification.py -q`.
  Expected: RED because the Phase 8 qualification module does not exist (and,
  in the current discovery environment, no accepted container daemon is
  available).
- [ ] **Step 3: Implement the minimal probe.** Detect only the explicitly
  configured container/WSL runtime, execute a harmless synthetic probe, and
  return the closed result. Do not execute user code or inspect credentials.
- [ ] **Step 4: Run the test to verify it passes.**
  Run the same command plus the fake negative cases. Expected: GREEN when the
  reviewed runtime is available; otherwise the suite records a blocked
  readiness result and no later task may proceed.
- [ ] **Step 5: Run static checks and commit.**
  Run `uv run ruff check scripts/phase8 tests/phase8` and
  `uv run mypy scripts/phase8 tests/phase8`; commit
  `feat(phase8): qualify isolated computation runtime`.

## Task 1: Define closed computation contracts and capability policy

**Files:**
- Create: `src/aaos/phase8/__init__.py`
- Create: `src/aaos/phase8/computation_contracts.py`
- Create: `src/aaos/phase8/computation_policy.py`
- Test: `tests/phase8/test_computation_contracts.py`
- Test: `tests/phase8/test_computation_policy.py`

**Interfaces:**
- `ComputationLimits`, `ComputationInput`, `ComputationRequest`, and
  `ComputationResult` match the spec’s exact field allowlists and closed
  status literals.
- `ComputationCapabilityPolicy.is_allowed(task_scope: TrustedTaskScope) -> bool`.
- `validate_computation_request(request: ComputationRequest, *, policy: ComputationCapabilityPolicy) -> None`.

- [ ] **Step 1: Write RED tests.** Prove frozen/strict contracts, bounded byte
  lengths, SHA-256 binding, closed media/status values, rejection of unknown
  fields, and rejection of model-provided paths, mounts, network modes,
  credentials, limits, model IDs, and approval fields. Prove policy denial is
  independent of code text.
- [ ] **Step 2: Run RED.**
  Run `uv run pytest tests/phase8/test_computation_contracts.py tests/phase8/test_computation_policy.py -q`.
  Expected: missing module/types.
- [ ] **Step 3: Implement the immutable contracts and pure policy validator.**
  Use existing strict/frozen repository conventions. The validator performs no
  adapter, database, filesystem, network, or clock I/O.
- [ ] **Step 4: Run GREEN and type checks.**
  Run the focused pytest command, `uv run ruff check src/aaos/phase8 tests/phase8`,
  and `uv run mypy src/aaos/phase8 tests/phase8`.
- [ ] **Step 5: Commit** `feat(phase8): define computation contracts`.

## Task 2: Add the versioned worker protocol and deterministic fake worker

**Files:**
- Create: `src/aaos/phase8/worker_protocol.py`
- Create: `src/aaos/phase8/fake_worker.py`
- Test: `tests/phase8/test_worker_protocol.py`
- Test: `tests/phase8/test_fake_worker.py`

**Interfaces:**
- `WORKER_PROTOCOL_VERSION: str`.
- `encode_worker_request(request: ComputationRequest) -> bytes`.
- `decode_worker_result(payload: bytes) -> ComputationResult`.
- `FakeWorker.execute(request: ComputationRequest) -> ComputationResult`.
- `FakeWorker.scenario` is a closed enum: `SUCCESS`, `REJECT_BEFORE_EXECUTION`,
  `TIMEOUT_AFTER_START`, `CRASH_AFTER_START`, `OVERSIZED_OUTPUT`.

- [ ] **Step 1: Write RED tests.** Prove deterministic canonical JSON, strict
  protocol versioning, rejection of unknown fields and oversized payloads,
  and exact fake outcomes including `UNKNOWN_OUTCOME`.
- [ ] **Step 2: Run RED.**
  Run `uv run pytest tests/phase8/test_worker_protocol.py tests/phase8/test_fake_worker.py -q`.
- [ ] **Step 3: Implement the protocol and fake worker.** Use length/byte
  checks before parsing and never include raw traceback, path, SQL, or secret
  details in result payloads.
- [ ] **Step 4: Run GREEN plus `uv run ruff check src/aaos/phase8 tests/phase8`.**
- [ ] **Step 5: Commit** `feat(phase8): add bounded worker protocol`.

## Task 3: Implement the isolated supervisor, quotas, and cleanup

**Files:**
- Create: `src/aaos/phase8/sandbox.py`
- Create: `src/aaos/phase8/sandbox_resources.py`
- Modify: `src/aaos/phase8/worker_protocol.py`
- Test: `tests/phase8/test_sandbox.py`
- Test: `tests/phase8/test_sandbox_cleanup.py`

**Interfaces:**
- `class SandboxExecutor(Protocol): execute(self, request: ComputationRequest) -> ComputationResult`.
- `class Clock(Protocol): monotonic(self) -> float` is injected by the
  supervisor so timeout tests never read the wall clock implicitly.
- `ContainerSandboxExecutor(runtime: IsolationRuntimeProbe, *, clock: Clock, temp_root: Path)`.
- `SandboxExecutor.execute()` creates one worker, applies the trusted limits,
  validates the result, and destroys all task resources before returning.
- `SandboxExecutionError` exposes only a closed `reason_code`.

- [ ] **Step 1: Write RED tests.** Using the fake runtime, prove network,
  credential, host-mount, device, process-count, memory, CPU, wall-time, and
  output limits; fresh worker per request; cleanup on success/failure/timeout/
  cancellation/crash; and no retry after `UNKNOWN_OUTCOME`.
- [ ] **Step 2: Run RED.**
  Run `uv run pytest tests/phase8/test_sandbox.py tests/phase8/test_sandbox_cleanup.py -q`.
- [ ] **Step 3: Implement container invocation.** Use allowlisted runtime
  arguments: network none, read-only root, no devices, dropped capabilities,
  non-root identity, task input mount read-only, bounded output mount, and
  CPU/memory/pids limits. Use a host Job Object only for reaping; never claim
  it supplies network/filesystem isolation. Do not fall back if runtime
  qualification fails.
- [ ] **Step 4: Run GREEN, Ruff, and mypy for changed files.**
- [ ] **Step 5: Commit** `feat(phase8): isolate computation workers`.

## Task 4: Expose a trusted computation service without widening the Phase 5 proposal

**Files:**
- Create: `src/aaos/phase8/computation_tool.py`
- Create: `src/aaos/phase8/computation_service.py`
- Test: `tests/phase8/test_computation_tool_boundary.py`
- Regression tests: the existing Phase 5 proposal, registry, orchestrator,
  observation, event, and security suites (read-only verification).

**Interfaces:**
- `ComputationToolAdapter.execute(request: ComputationRequest) -> ComputationResult`.
- `TrustedComputationService.run(request: ComputationRequest, *, policy: ComputationCapabilityPolicy) -> ComputationResult`.
- `project_computation_observation(result: ComputationResult) -> ToolObservation`.
- `ComputationPermission` is a closed trusted capability decision owned by the
  service; it is not model-selectable and does not imply Phase 6 approval.

- [ ] **Step 1: Write RED boundary tests.** Prove model input cannot inject
  host paths, SQL, limits, credentials, network, model identity, approval
  context, or a computation field into the current C8 provider envelope;
  `ToolRegistry` remains unchanged; observations/events are sanitized and
  bounded; and no Phase 6 action record is created.
- [ ] **Step 2: Run RED.**
  Run `uv run pytest tests/phase8/test_computation_tool_boundary.py -q` and
  the named Phase 5 regression files. Expected: missing service/adapter.
- [ ] **Step 3: Implement the smallest trusted service.** It receives a
  trusted, already-materialized `ComputationRequest`, applies the closed
  capability policy, invokes `SandboxExecutor`, and projects a bounded
  observation. Do not register a model-visible computation tool or modify
  `ProviderProposalEnvelope`, `ToolRegistry`, `AgentOrchestrator`, or the
  existing C1–C9 event/observation contracts.
- [ ] **Step 4: Run GREEN plus all Phase 5 agent/security tests, Ruff, and mypy.**
- [ ] **Step 5: Commit** `feat(phase8): add trusted computation tool boundary`.

## Task 5: Add deterministic visual page selection and bounded rendering

**Files:**
- Create: `src/aaos/phase8/vision_contracts.py`
- Create: `src/aaos/phase8/vision_selector.py`
- Create: `src/aaos/phase8/vision_renderer.py`
- Test: `tests/phase8/test_vision_selector.py`
- Test: `tests/phase8/test_vision_renderer.py`
- Regression tests: `tests/test_document_pdf.py`, `tests/test_rag_chunking.py`

**Interfaces:**
- `VisualPageCandidate(document_version_id: int, page_number: int, reason_code: str, estimated_bytes: int, selector_version: str)`.
- `VisualSelectorPolicy(max_pages: int, max_render_bytes: int, selector_version: str)`.
- `PageRenderer.render(document_version_id: int, page_number: int, *, max_bytes: int) -> RenderedPage`.
- `select_visual_pages(parsed_pdf: ParsedPdf, *, selector_policy: VisualSelectorPolicy) -> tuple[VisualPageCandidate, ...]`.
- `RenderedPage` matches the spec’s exact fields.
- `render_page(candidate: VisualPageCandidate, *, renderer: PageRenderer) -> RenderedPage`.

- [ ] **Step 1: Write RED tests.** Prove ordinary text pages are not selected,
  selection is deterministic and page-budgeted, only positive in-range pages
  are returned, render dimensions/bytes are bounded, and the source hash is
  stable. Include image-heavy pages, empty pages, and malformed page metadata.
- [ ] **Step 2: Run RED.**
  Run `uv run pytest tests/phase8/test_vision_selector.py tests/phase8/test_vision_renderer.py -q`.
- [ ] **Step 3: Implement pure selector and bounded renderer adapters.** Do not
  add VLM calls, persistence, or API routes. Rendering failures use fixed
  reason/status codes.
- [ ] **Step 4: Run GREEN plus document/RAG regressions, Ruff, and mypy.**
- [ ] **Step 5: Commit** `feat(phase8): select and render visual pages`.

## Task 6: Add the typed vision gateway and sequential model lifecycle

**Files:**
- Create: `src/aaos/phase8/vision_gateway.py`
- Create: `src/aaos/phase8/fake_vision.py`
- Modify: `src/aaos/inference/models.py`
- Create: `src/aaos/inference/vision_models.py`
- Modify: `src/aaos/rag/runtime.py`
- Test: `tests/phase8/test_vision_gateway.py`
- Test: `tests/phase8/test_vision_lifecycle.py`

**Interfaces:**
- `VisionModelGateway.generate(image: RenderedPage, *, request: VisionRequest) -> VisionResponse`.
- `VisionRequest(model_id: str, model_revision: str, prompt_revision: str, max_output_bytes: int)`.
- `VisionResponse(status: Literal["SUCCEEDED", "FAILED", "TIMED_OUT", "UNAVAILABLE"], payload: str | None)`.
- `FakeVisionGateway` scenarios are `SUCCESS`, `MALFORMED_OUTPUT`, `TIMEOUT`,
  and `UNAVAILABLE`.
- `VisionModelLifecycle.ensure_loaded(model_id: str, revision: str) -> None`;
  `release() -> None`;
  `assert_sequential_with_bge() -> None`.

- [ ] **Step 1: Write RED tests.** Prove strict image/request validation,
  malformed output rejection, source/model/prompt binding, model lifecycle
  ordering (vision released before BGE, NLI remains CPU), no concurrent GPU
  residency, and honest failure statuses.
- [ ] **Step 2: Run RED.**
  Run `uv run pytest tests/phase8/test_vision_gateway.py tests/phase8/test_vision_lifecycle.py -q`.
- [ ] **Step 3: Implement typed gateway/lifecycle adapters.** Do not change
  text-only `ChatRequest`; the multimodal request is a separate closed type.
  Existing local model inventory is configuration input only; no download or
  fallback model selection is added.
- [ ] **Step 4: Run GREEN, inference/RAG regressions, Ruff, and mypy.**
- [ ] **Step 5: Commit** `feat(phase8): add bounded vision gateway`.

## Task 7: Add visual provenance persistence behind a migration gate

**Files:**
- Create: `src/aaos/persistence/visual_models.py`
- Create: `src/aaos/phase8/visual_provenance.py`
- Create: `migrations/versions/0007_phase8_visual_provenance.py` only after
  verifying parent revision `0006_phase7e_task_records` and receiving the
  separate migration approval gate
- Test: `tests/phase8/test_visual_provenance.py`
- Test: `tests/test_phase8_visual_migration.py`

**Interfaces:**
- `VisualAnalysis` and `VisualElement` match the spec’s closed fields.
- `VisualProvenanceRepository.save_analysis(analysis: VisualAnalysis) -> int`.
- `VisualProvenanceRepository.get_analysis(document_version_id: int, page_number: int, source_content_sha256: str) -> VisualAnalysis | None`.
- `VisualCitationProjection` is distinct from `RagChunkSourceSpanValue` and
  cannot be passed to the existing text citation validator.

- [ ] **Step 1: Write RED tests.** Prove page/document/model/revision/hash
  binding, bounded typed element payloads, idempotent source-hash uniqueness,
  partial/failure persistence, and rejection of visual rows masquerading as
  embedded text spans. Add direct-SQL constraint tests for the migration.
- [ ] **Step 2: Run RED.**
  Run `uv run pytest tests/phase8/test_visual_provenance.py tests/test_phase8_visual_migration.py -q`.
- [ ] **Step 3: Verify the next Alembic revision from `alembic history` and
  implement only the additive tables/constraints.** No `create_all()`, no
  Phase 4 table mutation, and no migration is created if the human migration
  gate is not approved.
- [ ] **Step 4: Run GREEN, migration regressions, Ruff, mypy, and `uv lock --check`.**
- [ ] **Step 5: Commit** `feat(phase8): persist visual provenance` only after
  the separate migration approval gate is satisfied.

## Task 8: Integrate bounded visual evidence with RAG and computation

**Files:**
- Modify: `src/aaos/rag/contracts.py`
- Modify: `src/aaos/rag/generation.py`
- Modify: `src/aaos/rag/indexing.py`
- Create: `src/aaos/phase8/multimodal_rag.py`
- Modify: `src/aaos/phase8/computation_tool.py`
- Test: `tests/phase8/test_multimodal_rag.py`
- Test: `tests/phase8/test_repl_vision_composition.py`

**Interfaces:**
- `MultimodalEvidence` carries a distinct provenance kind and exact page/model
  binding; it is not accepted by text-only citation paths until the projection
  is explicitly enabled.
- `build_visual_computation_input(analysis: VisualAnalysis) -> ComputationInput`.
- `project_multimodal_answer(...) -> RenderedRagResponse` only when the trusted
  provenance validator proves the distinct visual citation contract.

- [ ] **Step 1: Write RED tests.** Prove visual evidence cannot fabricate a
  text span/citation, cannot bypass trusted scope or policy, and can be passed
  to the isolated worker only as bounded safe data. Prove prompt injection in
  image/text content cannot invoke tools or alter limits.
- [ ] **Step 2: Run RED.**
  Run `uv run pytest tests/phase8/test_multimodal_rag.py tests/phase8/test_repl_vision_composition.py -q`.
- [ ] **Step 3: Implement the distinct provenance projection and one-way
  composition.** Preserve `C2` lineage and `C6` validators; do not reinterpret
  unsupported visual output as grounded success.
- [ ] **Step 4: Run GREEN plus all Phase 4 RAG and Phase 5 agent regressions.**
- [ ] **Step 5: Commit** `feat(phase8): bound visual evidence composition`.

## Task 9: Add closed observability and frozen evaluation fixtures

**Files:**
- Modify: `src/aaos/agent/events.py`
- Modify: `src/aaos/agent/observations.py`
- Create: `src/aaos/phase8/evaluation.py`
- Create: `tests/phase8/evaluation/manifest.py`
- Create: `tests/phase8/evaluation/harness.py`
- Test: `tests/phase8/test_observability.py`
- Test: `tests/phase8/test_evaluation_harness.py`

**Interfaces:**
- New event kinds, if needed, are closed enum members with typed event-specific
  payloads; no arbitrary details dictionary is added.
- `Phase8EvaluationManifest(schema_version: str, calibration_ids: tuple[str, ...], heldout_ids: tuple[str, ...], sha256: str)`.
- `run_phase8_calibration(manifest: Phase8EvaluationManifest, *, sandbox: FakeSandboxExecutor, vision: FakeVisionGateway) -> Phase8Report`.
- `Phase8Report` records trusted-core pass/fail counters, model calls,
  external writes, retries, and fixture hashes.

- [ ] **Step 1: Write RED tests.** Prove event/detail closure, path/secret/
  traceback redaction, deterministic fixture membership and hashes, calibration/
  heldout disjointness, zero model downloads, zero RAG/external writes, and
  no normal pytest path to real heldout IDs.
- [ ] **Step 2: Run RED.**
  Run `uv run pytest tests/phase8/test_observability.py tests/phase8/test_evaluation_harness.py -q`.
- [ ] **Step 3: Implement typed events and synthetic-only evaluation harness.**
  Freeze manifests before any local model probe; do not inspect or execute real
  heldout cases in this task.
- [ ] **Step 4: Run GREEN, all Phase 4–7 regressions, Ruff, mypy, and import-smoke tests.**
- [ ] **Step 5: Commit** `test(phase8): freeze computation and vision evaluation`.

## Task 10: Final Phase 8 verification and review package

**Files:**
- Modify: `docs/GOVERNANCE_LEDGER.md` only if a separately authorized design/
  implementation state entry is required; never change Phase 7 history.
- Create: `tests/phase8/test_phase8_acceptance.py`
- Create: `tests/phase8/reports/README.md` describing safe artifact retention
  without including model outputs or secrets.

**Interfaces:**
- `run_phase8_acceptance()` executes only synthetic/fake adapters and returns
  a bounded `Phase8AcceptanceReport`.
- No function in this task loads a model, calls RAG, executes a real external
  write, or consumes heldout data.

- [ ] **Step 1: Write RED acceptance tests.** Cover every invariant in the
  spec, including isolation, no fallback, quotas, cleanup, unknown outcome,
  visual selection/provenance, prompt injection, Phase 4–7 regressions, clean
  imports, and scope exclusions.
- [ ] **Step 2: Run RED.**
  Run `uv run pytest tests/phase8/test_phase8_acceptance.py -q`.
- [ ] **Step 3: Implement only the deterministic report assembly and tests.**
  Keep model/provider fixtures fake and keep heldout execution unreachable from
  ordinary pytest.
- [ ] **Step 4: Run the complete verification set.**
  Run `uv run pytest -q`, `uv run ruff format --check .`,
  `uv run ruff check .`, `uv run mypy src`, `uv lock --check`,
  `git diff --check`, and a fresh import-smoke script. Run frontend/Phase 7
  checks only as read-only regressions if the repository requires them.
- [ ] **Step 5: Perform independent review against the spec and frozen Phase
  7 tag. Resolve only Critical/Important documentation or implementation
  findings with new commits, rerun the review, and record
  `PASS_NO_CRITICAL_OR_IMPORTANT_FINDINGS`.
- [ ] **Step 6: Commit** `test(phase8): verify restricted repl and multimodal boundaries`.

## Major invariant coverage map

| Spec invariant | Plan tasks |
|---|---|
| OS isolation; no fallback | 0, 3, 10 |
| Fresh worker, quotas, cleanup | 2, 3, 10 |
| `UNKNOWN_OUTCOME`, no retry | 2, 3, 9, 10 |
| Model cannot grant authority | 1, 4, 8, 9, 10 |
| Phase 5 scope/validation/events preserved | 4, 9, 10 |
| Deterministic visual selection | 5, 9, 10 |
| Render bounds and hashes | 5, 6, 9 |
| Visual provenance and no text-span masquerade | 6, 7, 8, 10 |
| Sequential Qwen/BGE/NLI/VLM residency | 6, 10 |
| Prompt injection resistance | 4, 8, 9, 10 |
| No Phase 6/external writes | 4, 8, 9, 10 |
| Migration/API/observability review | 7, 9, 10 |
| Calibration/heldout freeze and no leakage | 9, 10 |
| Frozen Phase 4–7 regressions | 4, 5, 6, 8, 10 |

## Plan self-review and stop gate

- All planned interfaces are named with parameter and return types.
- Every task has an explicit RED command, minimal implementation boundary,
  GREEN/static checks, and a focused commit.
- No plan step contains an unresolved placeholder, an open-ended edge-case
  instruction, or an unspecified fallback.
- The migration task is explicitly gated and cannot silently mutate Phase 4.
- No task creates a browser route, external write, model download, or RLM
  behavior.
- The next action after these documentation commits is human review and
  explicit `HUMAN_IMPLEMENTATION_AUTHORIZATION`; no product implementation is
  authorized by this plan alone.
