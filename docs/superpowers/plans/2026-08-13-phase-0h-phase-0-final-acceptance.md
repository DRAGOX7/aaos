# Phase 0H Phase 0 Final Acceptance Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to execute this acceptance plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verify the current repository against every applicable Phase 0 requirement and produce a final human-acceptance report without adding product functionality.

**Architecture:** This is an acceptance-only workflow over the existing 0A–0G implementation and evidence. It reuses accepted reports for provenance, reruns required current-HEAD gates, performs the required local/live checks, and ends with one whole-Phase-0 review followed by a human decision. The `phase-00-passed` tag is created only after that decision and must target the exact accepted commit.

**Tech Stack:** Existing Python 3.11 project, uv, Ruff, strict mypy, pytest, FastAPI TestClient, Alembic/SQLite, current LM Studio gateway, `aaos doctor`, Git, and Docker validation only if Docker configuration exists.

## Global Constraints

- Do not modify production code, dependencies, database schema, governance, or later-phase architecture.
- Do not begin Phase 1 or create `phase-00-passed` before explicit human Phase 0 acceptance.
- Reuse accepted 0A–0G reports for provenance; do not repeat historical experiments or benchmarks.
- Fresh current-HEAD Phase 0E generation smoke is required and cannot be replaced by historical evidence.
- LM Studio must already be running with the approved model/profile; do not start, load, unload, download, reconfigure, or alter JIT/GPU settings.
- Docker is applicability-only: do not add Docker files, dependencies, or deployment work.
- Never expose credentials, cookies, tokens, prompts, private academic data, raw responses, stderr, or secret file contents.
- If an applicable requirement fails, an external prerequisite blocks required smoke, or production repair appears necessary, stop and report for human decision.
- Temporary resources must be cleaned up: stop any local health server and remove only the temporary migration database/directory created by the acceptance run.

---

### Task 1: Reconcile the accepted revision and provenance

**Files:**
- Read: `docs/superpowers/specs/2026-08-13-phase-0h-phase-0-final-acceptance-design.md`
- Read: `docs/PHASES.md`, `docs/PROJECT_SPEC.md`, `docs/ARCHITECTURE.md`, `AGENTS.md`, `docs/DEVELOPMENT_RULES.md`
- Read: accepted 0A–0G reports and current governance history
- Evidence: `.superpowers/sdd/2026-08-13-phase-0h-phase-0-final-acceptance/phase-0h-evidence-report.md` (ignored/governed location)

**Interfaces:**
- Consumes: current `main` and accepted subphase evidence.
- Produces: a baseline record with current HEAD, branch/status, tag state, authoritative 0A–0G statuses, and provenance references.

- [ ] **Step 1: Capture the starting identity**

Run:

```powershell
git -c safe.directory="C:/Users/aljaf/aaos" rev-parse HEAD
git -c safe.directory="C:/Users/aljaf/aaos" status --short --branch
git -c safe.directory="C:/Users/aljaf/aaos" tag -l phase-00-passed
```

Record only safe summaries. The starting worktree must be clean before acceptance work proceeds.

- [ ] **Step 2: Reconcile statuses**

Use explicit human acceptance records and current governance as authoritative: 0A–0D complete, 0E–0G accepted, 0H in progress. Mark older pending wording as historical, not current.

- [ ] **Step 3: Initialize the evidence report**

Record the baseline HEAD and the exact accepted report/specification paths. Do not copy raw logs or private data.

### Task 2: Run fresh deterministic repository and foundation gates

**Files:**
- Read: `src/`, `tests/`, `pyproject.toml`, `uv.lock`, `alembic.ini`, `migrations/`
- Modify: no production or test files unless a genuine acceptance failure proves a pre-existing defect; stop before repair
- Evidence: the Phase 0H evidence report

**Interfaces:**
- Consumes: Task 1 baseline.
- Produces: current-HEAD deterministic evidence for reproducibility, health, migration, inference, doctor, and security boundaries.

- [ ] **Step 1: Run the complete deterministic gate**

```powershell
uv lock --check
uv sync --locked --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest -v
git -c safe.directory="C:/Users/aljaf/aaos" diff --check
```

After locked sync, verify `pyproject.toml` and `uv.lock` are unchanged and the worktree has no unexpected tracked mutation.

- [ ] **Step 2: Verify FastAPI health and import safety**

Run the existing health and import probes:

```powershell
uv run pytest tests/test_health.py tests/test_inference_imports.py tests/test_doctor_imports.py -v
```

The `/health` server is created by the test client and must be closed by its context manager; no persistent server may remain.

- [ ] **Step 3: Verify clean SQLite migration round trip**

Run:

```powershell
uv run pytest tests/test_database.py -v
```

The test-created temporary database and parent directory must be removed by pytest temporary-directory cleanup. If a manual migration probe is needed, create it under a temporary directory, run upgrade → inspect → downgrade → upgrade, then dispose the engine and remove that temporary directory before continuing.

### Task 3: Run required local/live acceptance checks

**Files:**
- Read: `tests/live/test_lm_studio_smoke.py`, `src/aaos/cli.py`, `src/aaos/doctor/`, `src/aaos/inference/`
- Modify: none
- Evidence: the Phase 0H evidence report

**Interfaces:**
- Consumes: current deterministic PASS from Task 2 and the user-prepared local LM Studio profile.
- Produces: fresh local `/health`, doctor, and mandatory Phase 0E generation-smoke evidence.

- [ ] **Step 1: Run the local doctor smoke**

Run `uv run aaos doctor` against the existing environment. Record the aggregate
status, exit code, and safe report facts only. Preserve the accepted Phase 0F
semantics: aggregate PASS with exit 0 is acceptable; aggregate WARN with exit 0
is also acceptable when required capabilities pass; aggregate FAIL with exit 1,
usage errors, or unexpected exits block Phase 0. Optional NVIDIA/reference
warnings must not be promoted to failures. Do not invent hardware thresholds or
require an all-PASS report. Do not start or alter LM Studio, NVIDIA tools, or
other environment state.

- [ ] **Step 2: Run the mandatory fresh Phase 0E generation smoke**

With the user-prepared LM Studio service already running, JIT disabled, and the approved configured model/profile already loaded, run the opt-in live smoke:

```powershell
$env:AAOS_RUN_LM_STUDIO_SMOKE = "1"
$smokeExit = 1
try {
    uv run pytest tests/live/test_lm_studio_smoke.py -v
    $smokeExit = $LASTEXITCODE
}
finally {
    Remove-Item Env:AAOS_RUN_LM_STUDIO_SMOKE -ErrorAction SilentlyContinue
}
if ($smokeExit -ne 0) {
    exit $smokeExit
}
```

The smoke must perform the approved availability diagnostic and one plain generation request, then validate the model-independent `ChatResponse`. Record only safe status/usage metadata. If LM Studio, the model, context profile, or another external prerequisite is unavailable, record the precise blocker and stop for human decision; do not classify the applicable requirement as optional.

- [ ] **Step 3: Verify Docker applicability**

Inspect the repository for Docker configuration introduced during Phase 0. If none exists, record `NOT APPLICABLE` with the repository evidence. If configuration exists, run only its already appropriate validation and require PASS. Do not add Docker files or install Docker tooling.

### Task 4: Final repository/security checks and exact revision capture

**Files:**
- Read: current tracked files and accepted 0G evidence
- Modify: no production files; no later-phase files
- Evidence: the Phase 0H evidence report

**Interfaces:**
- Consumes: Tasks 1–3 results.
- Produces: final current-HEAD cleanliness/security/scope evidence.

- [ ] **Step 1: Run bounded Git hygiene checks**

```powershell
git -c safe.directory="C:/Users/aljaf/aaos" ls-files -- data/auth data/credentials
git -c safe.directory="C:/Users/aljaf/aaos" status --short --branch
git -c safe.directory="C:/Users/aljaf/aaos" diff --check
```

Inspect only approved credential/auth boundaries and obvious governance violations. Never emit file contents. Confirm no premature `phase-00-passed` tag exists.

- [ ] **Step 2: Review final scope and history**

Review the current diff/history from the accepted 0G revision. Confirm no product files, dependencies, Docker files, Phase 1+ functionality, external writes, or architecture changes were introduced by 0H.

- [ ] **Step 3: Capture the final verification HEAD**

Record the exact `git rev-parse HEAD` after all checks. If no tracked change occurred, it must equal the baseline HEAD. If an authorized verification-only test change somehow became necessary, stop, obtain the required scoped review, commit it, rerun all affected and complete gates, and record the new HEAD instead. Do not present pre-change evidence for a changed revision.

### Task 5: Final whole-Phase-0 review and human handoff

**Files:**
- Read: Phase 0H evidence report, design, accepted 0A–0G evidence, current final diff/history
- Modify: evidence report only

**Interfaces:**
- Consumes: complete current-HEAD evidence.
- Produces: final review result and explicit human acceptance handoff.

- [ ] **Step 1: Dispatch one final whole-Phase-0 review**

Use Sol with high reasoning. Review every applicable Phase 0 matrix row, stale-vs-current evidence labeling, exact HEAD identity, mandatory live generation smoke, Docker applicability, repository cleanliness, security boundaries, and absence of Phase 1+ leakage. This is a final consistency/security review, not a redesign.

- [ ] **Step 2: Assemble the final evidence report**

Include accepted 0A–0G provenance, fresh command outcomes and counts, smoke results, Docker classification, final HEAD, status/tag checks, limitations, review result, and a decision field. State that Phase 0 is ready for human acceptance only if every applicable row passes. Do not claim acceptance or create the tag.

- [ ] **Step 3: Stop for human Phase 0 acceptance**

Present the report and review result. Wait for explicit human acceptance. If accepted, the next separate authorized action may create `phase-00-passed` explicitly targeting the human-accepted HEAD and then verify the tag resolves to that exact commit. Do not begin Phase 1.

## Completion checklist

- [ ] Accepted 0A–0G provenance reconciled.
- [ ] Current deterministic gates pass.
- [ ] `/health`, clean SQLite migration, and `aaos doctor` checks pass.
- [ ] Fresh Phase 0E generation smoke passes against the prepared local profile.
- [ ] Docker applicability is recorded and, if applicable, validation passes.
- [ ] Repository/security/scope checks pass.
- [ ] Final deterministic/review/human-accepted HEAD identities are aligned.
- [ ] Whole-Phase-0 Sol review is clean.
- [ ] Final evidence report is complete.
- [ ] Human Phase 0 acceptance is pending; no tag exists before acceptance.
