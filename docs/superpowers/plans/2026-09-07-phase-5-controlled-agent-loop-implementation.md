# Phase 5 — Controlled Agent Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement one bounded, local-only Phase 5 orchestrator whose untrusted structured proposals can reach only statically allowlisted, trusted tools after strict validation and permission checks, while preserving the frozen Phase 4 `LocalRagService` boundary.

**Architecture:** A primary orchestrator owns immutable task state and legal transitions. The existing `ModelGateway` proposes one typed action at a time; strict parsing, registry lookup, capability policy, bounded execution, observation sanitization, and deterministic termination remain application-owned. Phase 4 is consumed through `LocalRagService.ask(RagQuery)` only.

**Tech Stack:** Python 3.11, existing frozen dataclass/`StrEnum` contracts, strict JSON-schema parsing through `ModelGateway`, SQLAlchemy only through an explicitly approved AAOS task-state repository, pytest/fakes, Ruff, strict mypy, uv, and disposable local evaluation artifacts.

**Spec:** `docs/superpowers/specs/2026-09-07-phase-5-controlled-agent-loop-design.md`

## Global Constraints

- Phase 4 is frozen at `phase-04-passed` / `e99c494628952e0a1ebcafa2450c87611b4dc6c8`; do not modify its production code, tests, data, lifecycle, retrieval, or model configuration.
- Do not create Phase 5 modules or tests until this plan receives explicit human approval.
- One primary orchestrator only. No agents, recursive loops, hidden fallback, memory, or follow-up rewriting.
- Model output, retrieved text, evidence, local state, and observations are untrusted data. Trusted application code owns state, policy, registry, schemas, limits, handlers, and rendering.
- No arbitrary Python, REPL, shell, subprocess, filesystem, SQL, HTTP, browser, Moodle, credentials, external writes, MCP writes, Phase 6 approvals, Phase 7 dashboard, or Phase 8 execution.
- Proposed limits remain hard and bounded: `MAX_AGENT_STEPS=8`, `MAX_STRUCTURED_PROPOSAL_REPAIRS=1`, `MAX_IDENTICAL_TOOL_CALL_REPETITIONS=2`; model/tool timeouts are finite and configurable.
- `UNKNOWN_TOOL_POLICY=REJECT`, `INVALID_ARGUMENT_POLICY=REJECT_BEFORE_HANDLER`, `UNAUTHORIZED_TOOL_POLICY=REJECT_BEFORE_HANDLER`, and dynamic model tool registration is forbidden.
- Normal AAOS data paths are never used by tests or acceptance. `reflex` is never touched or staged.
- Every task below is test-first: write the named failing test, implement the smallest behavior, run the focused command, then run the listed regression commands.
- Suggested production paths are planning targets only; the current specification task creates documentation, not Python files.

## Slice 1 — Contracts and state-machine types

**Files (planned, not created now):**

- `src/aaos/agent/contracts.py`
- `src/aaos/agent/state.py`
- `tests/test_agent_contracts.py`
- `tests/test_agent_state.py`

**Interfaces:** Consumes immutable caller task metadata; produces strict `AgentTask`, `AgentProposal`, tool/error contracts, `AgentLimits`, and legal transition functions. No handler or model call.

**TDD steps:**

1. Add tests that reject blank/oversized ids and text, booleans-as-integers, unknown enum values, extra fields, and non-canonical serialization. Add tests for every legal transition and deterministic rejection of every illegal transition.
2. Implement frozen dataclasses/`StrEnum` contracts and canonical serialization. Keep model-facing parsing separate from trusted construction.
3. Verify terminal states cannot transition, step numbers are monotonic, and limits cannot be mutated by a proposal.

**Validation:** `uv run pytest tests/test_agent_contracts.py tests/test_agent_state.py -q`; `uv run ruff check src/aaos/agent tests/test_agent_contracts.py tests/test_agent_state.py`; `uv run mypy src/aaos/agent tests/test_agent_contracts.py tests/test_agent_state.py`.

**Acceptance gate:** All contracts are closed/strict, state ownership is trusted application code, and no Phase 4 import or data path is introduced.

**Commit boundary:** `feat(phase5): add agent contracts and state machine`.

**Future-phase exclusions:** No tools, model calls, persistence, CLI, approvals, or code execution.

## Slice 2 — Tool registry and permission policy

**Files (planned):** `src/aaos/agent/registry.py`, `src/aaos/agent/policy.py`, `tests/test_agent_registry.py`, `tests/test_agent_policy.py`.

**Interfaces:** Consumes closed `ToolName`, typed arguments, and trusted capabilities; produces registry metadata and `PermissionDecision` before any handler.

**TDD steps:**

1. Test unknown tools, dynamic registration attempts, malformed arguments, missing/extra fields, and denied capabilities all reject before a spy handler is called.
2. Implement a static registry with schema, permission, side-effect, timeout, handler, sanitizer, and audit metadata. Normalize arguments before computing `LOOP_DETECTION_KEY`.
3. Test that model text, evidence, and observations cannot alter registry or permission state.

**Validation:** `uv run pytest tests/test_agent_registry.py tests/test_agent_policy.py -q`; Ruff and strict mypy on the same paths.

**Acceptance gate:** Unknown/unauthorized/invalid calls have zero handler invocations; registry is application-owned and immutable at runtime.

**Commit boundary:** `feat(phase5): add allowlisted tool policy`.

**Future-phase exclusions:** No generic filesystem, shell, HTTP, SQL, browser, external write, or dynamic plugin surface.

## Slice 3 — Phase 4 RAG adapter and safe read tools

**Files (planned):** `src/aaos/agent/tools.py`, `src/aaos/agent/phase4_adapter.py`, `tests/test_agent_rag_tool.py`, `tests/test_agent_task_state_read.py`.

**Interfaces:** Consumes `RagQuery` plus explicit scope and injected `LocalRagService`; produces a bounded `ToolResult` and redacted observation. Reads only through the frozen public service.

**TDD steps:**

1. Test that `rag_query` forwards exactly one explicit `CourseScope`, `GlobalScope`, or `HistoricalScope`, never broadens scope, and maps safe Phase 4 failures without exposing internals.
2. Test `read_task_state` returns only the allowlisted summary fields and cannot accept a path or SQL expression.
3. Implement handlers with injected services and finite tool timeouts; ensure all resources are closed by their existing owner.

**Validation:** `uv run pytest tests/test_agent_rag_tool.py tests/test_agent_task_state_read.py tests/test_rag_service.py tests/test_rag_runtime.py -q`; Ruff/mypy on changed paths.

**Acceptance gate:** Phase 4 is called once through `LocalRagService`; no repository/retrieval duplication or startup side effect exists.

**Commit boundary:** `feat(phase5): add bounded Phase 4 read adapter`.

**Future-phase exclusions:** No conversation memory, follow-up rewriting, external tools, or public API.

## Slice 4 — Structured proposal adapter

**Files (planned):** `src/aaos/agent/proposal.py`, `tests/test_agent_proposal.py`.

**Interfaces:** Consumes local `ModelGateway` responses; produces either a strict `AgentProposal` or a typed safe failure. Schema is infrastructure-owned.

**TDD steps:**

1. Add fake-gateway tests for valid proposals, invalid JSON, missing/extra fields, unknown tools, malformed arguments, model unavailable, timeout, and one repair followed by success/failure.
2. Implement deterministic JSON-schema request construction, strict parsing, at most one repair, finite timeout, and no fallback model/provider.
3. Assert retrieved/tool text cannot change the schema and raw output is not logged or serialized.

**Validation:** `uv run pytest tests/test_agent_proposal.py tests/test_model_gateway.py tests/test_lm_studio_gateway.py -q`; Ruff/mypy on changed paths.

**Acceptance gate:** Two model calls is the absolute maximum for a proposal; parser and application validation remain authoritative.

**Commit boundary:** `feat(phase5): add strict structured proposal adapter`.

**Future-phase exclusions:** No tool execution, planner memory, or model-supplied permissions.

## Slice 5 — Bounded primary orchestrator

**Files (planned):** `src/aaos/agent/orchestrator.py`, `tests/test_agent_orchestrator.py`.

**Interfaces:** Consumes `AgentTask`, proposal adapter, registry/policy, and handlers; produces `AgentTerminalResult` with safe failure codes.

**TDD steps:**

1. Test the full legal state path for terminal completion and one tool observation followed by completion.
2. Test step exhaustion, repeated canonical tool calls, model timeout/unavailability, tool timeout/failure, cancellation, invalid transitions, and cleanup on every exit.
3. Implement one-step-at-a-time orchestration with no recursion, no hidden retries, explicit loop key counting, and deterministic terminal results.

**Validation:** `uv run pytest tests/test_agent_orchestrator.py tests/test_agent_state.py -q`; then relevant RAG service/runtime regressions plus Ruff/mypy.

**Acceptance gate:** One primary orchestrator is the only state owner; all limits and terminal outcomes are observable and bounded.

**Commit boundary:** `feat(phase5): add bounded agent orchestrator`.

**Future-phase exclusions:** No swarm, autonomous agents, tools created by the model, approvals, or external writes.

## Slice 6 — Observation sanitization and injection boundaries

**Files (planned):** `src/aaos/agent/observations.py`, `tests/test_agent_observations.py`, `tests/test_agent_prompt_injection.py`.

**Interfaces:** Consumes trusted `ToolResult`; produces bounded `ToolObservation` and safe audit fields.

**TDD steps:**

1. Test removal/redaction of tokens, cookies, authorization state, raw paths, SQL, stack traces, and oversized fields.
2. Test adversarial instructions in evidence, document text, local state, and observations cannot change policy, registry, limits, or state.
3. Implement allowlisted structured fields, deterministic truncation, opaque references, and fingerprints without exposing raw content.

**Validation:** `uv run pytest tests/test_agent_observations.py tests/test_agent_prompt_injection.py -q`; Ruff/mypy on changed paths.

**Acceptance gate:** Model-visible observations are data-only and sanitized; injection bypass count is zero.

**Commit boundary:** `feat(phase5): isolate and sanitize agent observations`.

**Future-phase exclusions:** No prompt rewriting that changes authority, no secret access, no generic file tools.

## Slice 7 — Approved domain-specific local-state write (conditional)

**Files (planned only if approved):** `src/aaos/agent/task_state.py`, `tests/test_agent_task_state_write.py`.

**Interfaces:** Consumes server-owned `AgentTaskId` and closed task-state fields; produces typed acknowledgement or safe failure.

**TDD steps:**

1. Obtain human approval of the exact AAOS-owned persistence surface and injected disposable root.
2. Test that arbitrary paths, SQL, fields, credentials, and normal protected paths are rejected before the handler.
3. Implement one transactionally bounded domain handler with audit event and deterministic cleanup.

**Validation:** `uv run pytest tests/test_agent_task_state_write.py -q` plus protected-path audit, Ruff, and mypy.

**Acceptance gate:** Local write is capability-checked, target-bound, auditable, and never generic filesystem authority. If approval is absent, leave the tool disabled and record `NOT_IMPLEMENTED_BY_SCOPE`.

**Commit boundary:** `feat(phase5): add approved task-state write surface` (only if approved).

**Future-phase exclusions:** No external writes, approvals, arbitrary persistence, or Phase 6 behavior.

## Slice 8 — Evaluation harness and datasets

**Files (planned):** `src/aaos/agent/evaluation/contracts.py`, `src/aaos/agent/evaluation/metrics.py`, `src/aaos/agent/evaluation/reports.py`, `tests/test_agent_evaluation.py`.

**Interfaces:** Consumes versioned calibration/held-out manifests and sanitized traces; produces deterministic metrics and private frozen bundles.

**TDD steps:**

1. Test manifest schema, disjoint splits, fingerprints, English/Arabic coverage, and exact case ordering.
2. Test metric calculations for tool choice, policy safety zeroes, bounded repair, loop escape, runtime failures, and refusal/ambiguity outcomes.
3. Test private bundle retention contains only formally required safe fields, is local/fingerprinted, and is sufficient for post-hoc metrics without rerunning held-out.

**Validation:** `uv run pytest tests/test_agent_evaluation.py -q`; full evaluation/privacy tests, Ruff, mypy, and `uv lock --check`.

**Acceptance gate:** Human can audit every planned metric from the frozen local bundle; no external upload or production dependency path exists.

**Commit boundary:** `feat(phase5): add reproducible agent evaluation harness`.

**Future-phase exclusions:** No RAGAS/cloud judge, answer-quality redesign, dashboard, or held-out rerun.

## Slice 9 — Optional integration boundary

**Files (planned only if approved):** `src/aaos/agent/service.py`, `src/aaos/cli.py` or a new narrow entrypoint only if the approved spec requires it, and integration tests.

**Interfaces:** Consumes a fully constructed orchestrator through explicit dependency injection; produces safe `AgentTerminalResult` output.

**TDD steps:**

1. First confirm whether a public/CLI boundary is required; absence means no files are added.
2. Test lazy imports, argument validation, success/failure cleanup, and sanitized output with fakes.
3. Implement only the approved boundary; never duplicate Phase 4 or tool logic.

**Validation:** focused integration/import tests, full pytest, Ruff, strict mypy, `uv lock --check`, and `git diff --check`.

**Acceptance gate:** No import/startup model, network, browser, or normal-path side effect; lifecycle closes on success and failure.

**Commit boundary:** `feat(phase5): expose approved agent boundary` (only if approved).

**Future-phase exclusions:** No production REPL, dashboard, external write UI, or Phase 6 approval flow.

## Slice 10 — Formal acceptance evidence

**Files (planned):** disposable acceptance runner/report paths under an explicitly approved temporary root; no normal data paths and no committed secrets.

**Interfaces:** Consumes approved calibration/held-out manifests and local model/tool adapters; produces sanitized report plus private frozen evaluation bundle.

**TDD/verification steps:**

1. Run the complete deterministic unit/regression suite and import-side-effect checks.
2. Run calibration only, inspect safety zeroes, bounded retries, loop limits, lifecycle, and English/Arabic behavior.
3. Obtain explicit authorization, then consume the untouched held-out once; freeze artifacts immediately and do not rerun/tune against them.
4. Review failures honestly; do not average safety violations away with quality metrics.

**Validation:** `uv run pytest -q`; `uv run ruff format --check .`; `uv run ruff check .`; `uv run mypy src tests`; `uv lock --check`; `git diff --check`; import/network/protected-path audits.

**Acceptance gate:** Human review records the formal result and limitations; Phase 6 remains unstarted.

**Commit boundary:** Documentation/report commit only unless an explicitly approved implementation slice remains to commit.

**Future-phase exclusions:** No Phase 6 approval engine, Phase 7 dashboard, Phase 8 REPL/VLM, or Phase 5 freeze tag without separate governance.

## Cross-slice review checklist

- [ ] One primary orchestrator and one model boundary remain visible in code.
- [ ] Every proposal is strict, request-local, and untrusted until policy validation.
- [ ] Unknown tools and invalid/unauthorized arguments never reach handlers.
- [ ] State transitions and loop limits are deterministic and bounded.
- [ ] Observations cannot expose secrets or become instructions.
- [ ] Phase 4 scope/snapshot/citation/lifecycle behavior is consumed, not copied.
- [ ] Local-write target is domain-specific and optional, never generic filesystem access.
- [ ] Calibration and held-out manifests are disjoint and fingerprinted before tuning.
- [ ] Private held-out evidence is retained locally and never committed.
- [ ] No Phase 6–8 capability leaks into Phase 5.

## Stop gate

After each slice, stop for human review when its commit and evidence satisfy
the slice acceptance gate. Slice 1 cannot begin until this specification and
plan are human-approved.
