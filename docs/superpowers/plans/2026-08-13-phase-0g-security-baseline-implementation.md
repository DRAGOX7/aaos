# Phase 0G Security Baseline Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce current, reviewable evidence that the accepted Phase 0 system satisfies its existing security claims, without adding security runtime functionality.

**Architecture:** Verification is organized around the approved Phase 0G matrix. Existing Phase 0E/0F reports provide provenance and historical acceptance evidence, while all deterministic correctness and security gates are freshly rerun against the current `main` HEAD. The deliverable is focused checks and an evidence report, not a new security subsystem.

**Tech Stack:** Existing Python 3.11 project, pytest, Ruff, strict mypy, uv lock/sync, Git static checks, and the current Phase 0E/0F implementation.

## Global Constraints

- Work only within Phase 0G; do not begin Phase 0H or create `phase-00-passed`.
- Do not modify production code, runtime dependencies, public interfaces, database schema, or AAOS runtime/application state. Approved local development-environment synchronization via `uv sync --locked --all-groups` is permitted solely for reproducibility verification.
- The approved locked sync may update only the local development environment as required for verification. It must not modify `pyproject.toml` or `uv.lock`, change credentials/auth state, modify databases, start or reconfigure LM Studio, perform external writes, or alter operating-system configuration. After sync, verify that no unexpected tracked repository mutation occurred.
- Do not add a secret-scanning dependency or claim exhaustive arbitrary-secret detection.
- Never print, fixture, commit, or report credential, cookie, token, prompt, response-body, academic-private, or raw subprocess contents.
- Reuse accepted 0E/0F reports for provenance and historical evidence only; rerun current deterministic gates against `main`.
- A failing applicable requirement or unresolved applicable evidence gap blocks 0G; a limitation is not a pass unless a human explicitly accepts that specific limitation.
- Later-phase-owned properties are classified as excluded ownership, not treated as Phase 0G evidence gaps.

---

### Task 1: Establish the current verification baseline

**Files:**
- Read: `AGENTS.md`, `docs/PHASES.md`, `docs/PROJECT_SPEC.md`, `docs/ARCHITECTURE.md`, `docs/DEVELOPMENT_RULES.md`
- Read: `docs/superpowers/specs/2026-08-13-phase-0g-security-baseline-design.md`
- Read: accepted Phase 0E/0F design and evidence reports
- Evidence: `.superpowers/sdd/2026-08-13-phase-0g-security-baseline/phase-0g-evidence-report.md` (ignored/governed evidence location)

**Interfaces:**
- Consumes: current `main` HEAD and the approved 0G matrix.
- Produces: a baseline record containing commit hash, branch, working-tree status, reused 0E/0F evidence references, and a row-by-row verification ledger.

- [ ] **Step 1: Confirm repository identity and scope**

Run:

```powershell
git -c safe.directory="C:/Users/aljaf/aaos" rev-parse HEAD
git -c safe.directory="C:/Users/aljaf/aaos" status --short --branch
git -c safe.directory="C:/Users/aljaf/aaos" tag -l phase-00-passed
```

Record the outputs without changing the repository. A non-clean starting tree is a limitation to resolve or explicitly report before acceptance; no unrelated change may be staged.

- [ ] **Step 2: Reconcile the matrix against current files**

For each matrix row, record the exact source requirement, implementation/test location, and whether the evidence is current, historical, or later-phase-owned. Do not mark an applicable row PASS from documentation alone.

- [ ] **Step 3: Record the baseline**

Write the commit/status identity and the initial matrix ledger to the ignored evidence report. Do not copy secret contents or raw environment/subprocess output.

### Task 2: Verify repository hygiene and output boundaries

**Files:**
- Read: `src/aaos/cli.py`, `src/aaos/doctor/commands.py`, `src/aaos/doctor/rendering.py`, `src/aaos/inference/lm_studio.py`
- Read: `tests/test_doctor_cli.py`, `tests/test_doctor_commands.py`, `tests/test_lm_studio_gateway.py`, `tests/test_doctor_service.py`
- Modify: no production files; add no test file unless Task 2 identifies a concrete unproven applicable invariant
- Evidence: `.superpowers/sdd/2026-08-13-phase-0g-security-baseline/phase-0g-evidence-report.md`

**Interfaces:**
- Consumes: Task 1 matrix ledger and the existing allowlists/redaction tests.
- Produces: fresh repository-hygiene and output-boundary results.

- [ ] **Step 1: Run the bounded repository hygiene checks**

Run:

```powershell
git -c safe.directory="C:/Users/aljaf/aaos" ls-files -- data/auth data/credentials
git -c safe.directory="C:/Users/aljaf/aaos" ls-files | Select-String -Pattern '(^|/)(\.env($|\.)|data/auth/|data/credentials/)'
```

This command is candidate discovery only. Classify matches against the approved boundary: tracked `data/auth/`, tracked `data/credentials/`, prohibited tracked `.env`-style credential/config files, explicitly documented credential/auth-state locations, and obvious violations of existing governance. A harmless source, test, or documentation filename containing `auth` or `credentials` is not a failure; substring matches alone cannot produce FAIL. Record path names and pass/fail status only; never print file contents. Do not interpret this as exhaustive secret detection.

- [ ] **Step 2: Verify output and error redaction**

Run the existing focused tests:

```powershell
uv run pytest tests/test_lm_studio_gateway.py tests/test_doctor_commands.py tests/test_doctor_cli.py tests/test_doctor_service.py -v
```

Review the user-visible render/CLI paths for fixed safe messages, allowlisted facts, no raw stderr/subprocess output, and no terminal-control injection. If a specific applicable invariant is not covered, add one narrowly scoped regression test with synthetic sentinel values that contain no real secret; otherwise add no test file.

- [ ] **Step 3: Record results**

Update the evidence report with exact commands, pass/fail results, and any concrete gap. Any unresolved applicable gap blocks 0G; it cannot be converted into a generic limitation.

### Task 3: Fresh deterministic Phase 0 security gates

**Files:**
- Read: `src/aaos/`, `tests/`
- Modify: only a narrowly scoped test file if Task 3 proves an actual applicable regression gap
- Evidence: `.superpowers/sdd/2026-08-13-phase-0g-security-baseline/phase-0g-evidence-report.md`

**Interfaces:**
- Consumes: current `main` implementation and Task 2 findings.
- Produces: fresh deterministic correctness/security evidence for all applicable matrix rows.

- [ ] **Step 1: Run import and boundary-focused tests**

```powershell
uv run pytest tests/test_inference_imports.py tests/test_doctor_imports.py tests/test_config.py tests/test_inference_models.py tests/test_doctor_models.py tests/test_doctor_system.py tests/test_doctor_nvidia.py tests/test_doctor_reference.py tests/test_doctor_model_check.py tests/test_doctor_runtime.py -v
```

Confirm import-time no-I/O behavior, typed model invariants, untrusted observation parsing, safe diagnostic mappings, and no hidden generation/lifecycle behavior.

- [ ] **Step 2: Run the complete deterministic gate**

```powershell
uv lock --check
uv sync --locked --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest -v
git -c safe.directory="C:/Users/aljaf/aaos" diff --check
```

All commands must pass against the current `main` HEAD. Do not weaken tests, formatter configuration, lint rules, type checking, or lock constraints to obtain a pass.

- [ ] **Step 3: Perform the focused static security review**

Verify the frozen 0F reader sequence, canonical command identity, fixed arguments, `shell=False`, `stdin=DEVNULL`, `stderr=DEVNULL`, sanitized environment, five-second timeout, strict decoding, gateway transport ownership/cleanup, loopback-only settings, and absence of doctor generation or remediation. Record exact file paths and outcomes, not raw sensitive values.

### Task 4: Complete the Phase 0G evidence report

**Files:**
- Evidence: `.superpowers/sdd/2026-08-13-phase-0g-security-baseline/phase-0g-evidence-report.md`
- Modify: no production files; no governance changes unless a factual evidence correction is required and separately approved

**Interfaces:**
- Consumes: Tasks 1–3 results and accepted 0E/0F reports.
- Produces: one complete evidence report suitable for independent review and human acceptance.

- [ ] **Step 1: Fill every matrix row**

For each applicable property, record PASS only when current code/tests/static evidence proves it. Record a later-phase ownership classification only for genuinely excluded properties. Record limitations separately; any limitation affecting an applicable requirement blocks the proposed 0G pass until a human explicitly accepts that exact limitation.

- [ ] **Step 2: Separate current and historical evidence**

Label accepted 0E/0F material as provenance or historical acceptance evidence. Label all fresh deterministic commands and current static checks as current evidence. Do not rerun old benchmark or architecture experiments.

- [ ] **Step 3: Record the final status and verified revision**

If 0G adds no tracked implementation or test change, the baseline HEAD remains the verified revision. If a genuine evidence gap requires a narrowly scoped regression test, add only that test, review it, commit it as a focused Phase 0G change, rerun every affected focused gate and the complete deterministic gate, and record the new final HEAD. Never present pre-change verification as evidence for a later changed revision. Include the verified commit hash, branch/status, exact commands, test counts, reused report paths, fresh results, failures/limitations, scope confirmation, and recommended next action. State explicitly whether 0G is ready for human acceptance; do not claim acceptance in the report.

### Task 5: Independent review and human acceptance handoff

**Files:**
- Read: the focused 0G evidence report and the approved design
- Modify: no production files; no Phase 0H files

**Interfaces:**
- Consumes: complete Task 4 evidence report and the focused implementation diff, if any.
- Produces: independent review findings and a human-approval handoff.

- [ ] **Step 1: Dispatch focused independent review**

Use Terra medium for an ordinary verification/security review. The reviewer must check that every applicable requirement has PASS evidence, no limitation silently waives a failure, repository hygiene is bounded rather than exhaustive, current gates are fresh, and no 0H or later-phase work leaked in. Escalate to Sol only for a genuine architecture/security contradiction.

- [ ] **Step 2: Resolve only in-scope findings**

If review finds a concrete verification gap, add the smallest test/check or correct the evidence report, rerun the affected gates, and obtain a focused re-review. Do not add production security architecture or weaken a failing gate.

- [ ] **Step 3: Stop for human decision**

Present the evidence report and review result for explicit human Phase 0G acceptance. Do not begin Phase 0H, create `phase-00-passed`, or implement any later-phase functionality.

## Completion checklist

- [ ] Every applicable Phase 0G matrix row has current PASS evidence.
- [ ] Every later-phase-owned property is explicitly excluded by ownership.
- [ ] No unresolved applicable evidence gap remains.
- [ ] Repository hygiene checks are bounded and expose no secret contents.
- [ ] Current deterministic gates pass on `main`.
- [ ] Evidence report and independent review are complete.
- [ ] Human Phase 0G acceptance is still pending.
