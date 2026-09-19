# Phase 9 Hardening, Evaluation, and Portfolio Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Qualify the frozen AAOS system for a reproducible, secure, honest portfolio release without adding new product authority or external-write capability.

**Architecture:** Phase 9 adds a release-qualification layer around the accepted Phase 0–8 boundaries. Deterministic manifests, fake adapters, synthetic adversarial fixtures, and bounded report builders provide ordinary automated evidence; Docker, optional local-model, browser, and clean-machine checks remain explicitly labeled manual/local gates. The release layer never becomes a second action ledger, RAG system, provider adapter, or model authority.

**Tech Stack:** Python 3.11, uv/uv.lock, pytest, Ruff, strict mypy, SQLAlchemy/Alembic/SQLite, FastAPI/OpenAPI, Next.js/npm, Docker Desktop/WSL2 qualification, existing Phase 4–8 contracts, and PowerShell-compatible release scripts.

**Spec:** `docs/superpowers/specs/2026-09-19-phase-9-hardening-evaluation-release.md`

## Global Constraints

- Start from annotated `phase-08-passed` target `5dea7f19bbe26e85b968afc63a21c10f0dd4e389` in the dedicated `phase-09-hardening-release` worktree.
- Do not modify frozen Phase 4, Phase 5, Phase 6B, Phase 7, or Phase 8 worktrees, tags, migrations, or accepted contracts.
- Do not add RLM, Deep Ask AAOS, recursive orchestration, a new provider, a browser computation route, a second RAG system, or a new external write.
- Ordinary and release tests make zero model calls, zero RAG calls, zero provider writes, and zero credential reads unless a separately labeled manual local gate is explicitly invoked.
- Phase 6 fake evaluation is the only action-execution surface; one approval remains one consumed operation attempt, and `UNKNOWN_OUTCOME` never auto-retries.
- Synthetic/demo fixtures are isolated, deterministic, labeled, and disposable; the normal database and private academic data are never used.
- Every manifest, report, benchmark, and screenshot artifact is canonicalized, hashed, bounded, and free of secrets/raw private payloads.
- A discovered product/security defect is preserved as RED evidence and requires a separate human-authorized fix; never weaken a gate to match an observed result.
- No dependency upgrade, migration creation, model download, external write, or Git history rewrite is allowed by this plan.

## Review Focus

1. **Authority leakage through release fixtures:** a synthetic prompt, provider result, visual element, or benchmark label must remain data and cannot become a tool, approval, policy, path, SQL, credential, or provider argument. Test in Task 1 and Task 2.
2. **Ambiguous action outcomes:** a crash/timeout after possible dispatch must remain `UNKNOWN_OUTCOME`, persist one operation identity, and require reconciliation with zero automatic retries. Test in Task 2 and Task 3.
3. **Benchmark/evidence leakage:** calibration and heldout IDs, hashes, labels, and observed outputs must be frozen before optional model probes; current failures cannot select cases or thresholds. Test in Task 0 and Task 5.
4. **Clean-install and migration side effects:** importing AAOS or running generic setup must not create a database, migrate implicitly, contact providers, or read credentials. Test in Task 4.
5. **Public/demo overclaiming:** a fake adapter or synthetic fixture must be labeled and never presented as a real Google/Moodle/provider result; claim-to-evidence links must be machine-checkable. Test in Task 7 and Task 8.

---

### Task 0: Freeze the release baseline and evidence manifest

**Files:**
- Create: `tests/phase9/__init__.py`
- Create: `tests/phase9/release_manifest.py`
- Test: `tests/phase9/test_release_manifest.py`
- Create: `scripts/phase9/__init__.py`
- Create: `scripts/phase9/build_release_manifest.py`
- Test: `tests/phase9/test_release_baseline.py`
- Modify: `docs/superpowers/specs/2026-09-19-phase-9-hardening-evaluation-release.md` only if a discovery fact is corrected by repository evidence

**Interfaces:**
- `Phase9ReleaseManifest(schema_version: str, release_commit: str, phase_tags: tuple[tuple[str, str], ...], migration_head: str, artifact_hashes: tuple[tuple[str, str], ...])` is a frozen, strict, bounded value object.
- `build_release_manifest(*, repo_root: Path, release_commit: str, artifact_paths: tuple[Path, ...]) -> Phase9ReleaseManifest` reads only Git/tag/migration metadata and explicitly supplied artifact bytes.
- `canonical_manifest_bytes(manifest: Phase9ReleaseManifest) -> bytes` uses sorted keys, UTF-8, compact JSON, no floating values, and stable newline termination.
- `verify_phase8_freeze(*, repo_root: Path, expected_target: str) -> None` proves the annotated tag target, clean Phase 8 worktree, and unchanged `0007_phase8_visual_provenance` head without modifying either.

- [ ] **Step 1: Write the failing tests.** Assert exact Phase 8 tag/target, required accepted tags (`phase-01-passed`, `phase-03-passed`, `phase-04-passed`, `phase-06b-passed`, `phase-07-passed`, `phase-08-passed`), Alembic head `0007_phase8_visual_provenance`, strict field closure, canonical hash determinism, artifact path containment, and rejection of secrets/private payloads.
- [ ] **Step 2: Run RED.** Run `uv run pytest tests/phase9/test_release_manifest.py tests/phase9/test_release_baseline.py -q`. Expected: missing Phase 9 package/functions.
- [ ] **Step 3: Implement the minimum manifest/baseline reader.** Use subprocess argument arrays for Git/Alembic metadata, bounded text parsing, and no shell/provider/model/database side effects. Treat `phase-08-passed` as immutable input, not a branch to rewrite.
- [ ] **Step 4: Run GREEN and static checks.** Run the focused pytest command, `uv run ruff check scripts/phase9 tests/phase9`, `uv run mypy scripts/phase9 tests/phase9`, and `git diff --check`.
- [ ] **Step 5: Commit.** `git add scripts/phase9 tests/phase9 && git commit -m "test(phase9): freeze release baseline manifest"`.

**Acceptance evidence:** the manifest contains no model output, credentials, raw documents, or normal DB bytes; its hash changes when a phase tag, migration head, or artifact changes; and all later tasks consume the frozen manifest rather than reconstructing membership after results.

**Stop condition:** any tag target mismatch, Phase 8 tree mutation, missing accepted phase evidence, or migration-head discrepancy stops Phase 9 planning execution for human review.

### Task 1: Freeze the threat model and adversarial corpus

**Files:**
- Create: `docs/security/phase-9-threat-model.md`
- Create: `tests/phase9/security_corpus.py`
- Test: `tests/phase9/test_security_corpus.py`
- Test: `tests/phase9/test_phase9_security_boundaries.py`
- Modify: `docs/ARCHITECTURE.md` only if a narrow cross-reference is required and separately reviewed

**Interfaces:**
- `AdversarialFixture(fixture_id: str, channel: Literal["TEXT","VISUAL","RETRIEVED","TOOL_OBSERVATION","COMPUTATION"], language: Literal["EN","AR","NONE"], payload: str, expected: Literal["DATA_ONLY"])` is immutable and bounded.
- `PHASE9_SECURITY_CORPUS: tuple[AdversarialFixture, ...]` is deterministic, contains no real secrets, and is hashed before execution.
- `evaluate_untrusted_content(fixture: AdversarialFixture, *, trusted_policy: TrustedBoundaryProbe) -> SecurityFixtureResult` returns only closed pass/fail/reason codes; it never executes fixture text.

- [ ] **Step 1: Write RED tests.** Cover text, screenshot/table/diagram strings, retrieved chunks, fake provider responses, computation output, and visual output containing instructions to reveal a sentinel, call tools, execute Python, approve, or send data. Prove no fixture changes tool identity, paths, mounts, limits, model identity, approval context, SQL, citations, or browser responses. Assert canonical corpus hash and EN/AR membership.
- [ ] **Step 2: Run RED.** Run `uv run pytest tests/phase9/test_security_corpus.py tests/phase9/test_phase9_security_boundaries.py -q`. Expected: missing corpus/probe types.
- [ ] **Step 3: Implement the synthetic corpus and trusted boundary probe.** Reuse Phase 5 sanitizers, Phase 6 contracts, Phase 7 error boundary, and Phase 8 closed computation/vision contracts; do not duplicate or alter their authorities.
- [ ] **Step 4: Run GREEN, Phase 5–8 security regressions, Ruff, and mypy.** Run `uv run pytest tests/test_phase5* tests/test_phase6a_security.py tests/test_phase6a_reconciliation.py tests/test_phase7e_mutation_security.py tests/test_phase7f_security.py tests/phase8 -q`, then `uv run ruff check src/aaos tests/phase9` and `uv run mypy src/aaos tests/phase9`.
- [ ] **Step 5: Commit.** `git add docs/security/phase-9-threat-model.md tests/phase9 && git commit -m "test(phase9): freeze adversarial security corpus"`.

**Acceptance evidence:** a separate review table maps every threat to asset, entry point, existing mitigation, executed check, and residual risk; required result is zero Critical/Important findings and zero authority/secret leaks.

**Stop condition:** a fixture reaches a real provider, database write, browser authority, model proposal, or shell; stop and remove no evidence until reviewed.

### Task 2: Qualify the Phase 6 action lifecycle with fake adapters

**Files:**
- Create: `tests/phase9/action_safety_harness.py`
- Test: `tests/phase9/test_action_safety.py`
- Test: `tests/phase9/test_action_concurrency.py`
- Test: `tests/phase9/test_action_privacy.py`
- Modify: none in `src/aaos/approval` or `src/aaos/integrations`

**Interfaces:**
- `FakeExternalAdapter` implements the existing `ExternalAdapter` protocol and records only bounded call counts, operation IDs, and closed outcomes.
- `run_action_scenario(scenario: ActionScenario, *, clock: FrozenClock) -> ActionScenarioResult` drives trusted prepare/request-approval/decision/claim/dispatch/reconcile using the existing Phase 6 service and repository.
- `ActionScenarioResult` exposes action state, operation state, execution attempt count, reconciliation count, persisted outcome, event kinds, and real-write count; it exposes no provider payload or secret.

- [ ] **Step 1: Write RED tests.** Cover prepare, approve, reject, trusted invalidation, expiry, stale capability/precondition, atomic claim, duplicate/concurrent claim, durable `CLAIMED -> EXECUTING` before adapter call, crash before call, crash after possible call, confirmed success/failure, ambiguous result, late result, reconciliation, duplicate/opposite decisions, and zero automatic retry from `UNKNOWN_OUTCOME`.
- [ ] **Step 2: Run RED.** Run `uv run pytest tests/phase9/test_action_safety.py tests/phase9/test_action_concurrency.py tests/phase9/test_action_privacy.py -q`. Expected: missing scenario harness.
- [ ] **Step 3: Implement test-only orchestration.** Call production services through typed interfaces; do not add a release-specific execution path, mutate action authority, or invoke Google/Calendar/Moodle. Use two SQLite connections for concurrency and deterministic barriers around CAS.
- [ ] **Step 4: Run GREEN and frozen Phase 6 regressions.** Run `uv run pytest tests/phase6a_* tests/test_phase6b_evaluation.py tests/test_phase7e_approval_concurrency.py tests/phase7e_no_real_write_acceptance.py tests/phase9 -q`, then static checks and `git diff --check`.
- [ ] **Step 5: Commit.** `git add tests/phase9 && git commit -m "test(phase9): qualify approval execution lifecycle"`.

**Acceptance evidence:** `REAL_EXTERNAL_WRITES=0`, one operation attempt per
claim, `UNKNOWN_OUTCOME` requires reconciliation, late results are sanitized
events rather than durable truth, and no scenario can create a new automatic
retry.

**Stop condition:** any test needs a real credential/provider, or the existing
Phase 6 service cannot preserve one-shot/CAS semantics without a production
authority change; stop for a separate architecture review.

### Task 3: Build the whole-system failure and recovery matrix

**Files:**
- Create: `docs/release/phase-9-failure-recovery-matrix.md`
- Create: `tests/phase9/failure_scenarios.py`
- Test: `tests/phase9/test_failure_recovery_matrix.py`
- Test: `tests/phase9/test_import_inert.py`

**Interfaces:**
- `FailureScenario(name: str, subsystem: Literal[...], injected_condition: str, expected_user_state: str, expected_internal_state: str, retry_policy: Literal["NONE","TRUSTED_RETRY","RECONCILIATION"], data_loss_risk: Literal["NONE","BOUNDED","REVIEW_REQUIRED"], recovery: str)` is a strict bounded record.
- `PHASE9_FAILURE_MATRIX: tuple[FailureScenario, ...]` contains Moodle unavailable/session expiry/malformed response, parse/corrupt PDF, index/embedding/LLM/NLI/VLM failure, Docker/worker/timeout, DB lock/migration mismatch, Next/FastAPI/SSE, Calendar ambiguity, filesystem full, and permission denial.
- `run_failure_scenario(scenario: FailureScenario, *, fakes: FailureFakes) -> FailureResult` returns sanitized state/error/retry evidence.

- [ ] **Step 1: Write RED tests.** Require one row per listed failure, explicit user-visible behavior, internal state, data-loss risk, recovery, and automatic-retry decision. Add import/startup tests proving no database, network, model, Docker, or provider side effect.
- [ ] **Step 2: Run RED.** Run `uv run pytest tests/phase9/test_failure_recovery_matrix.py tests/phase9/test_import_inert.py -q`.
- [ ] **Step 3: Implement the matrix and fake scenario runner.** Reuse existing bounded error codes and state enums. Do not introduce silent fallback or a generic retry helper.
- [ ] **Step 4: Run GREEN plus relevant Phase 3–8 error/recovery/import tests, Ruff, mypy, and diff-check.**
- [ ] **Step 5: Commit.** `git add docs/release tests/phase9 && git commit -m "test(phase9): document failure recovery matrix"`.

**Acceptance evidence:** every automatic retry is either forbidden by the
owning phase or explicitly trusted/bounded; ambiguous action/worker outcomes
remain `UNKNOWN_OUTCOME`; and no failure response leaks SQL, paths,
tracebacks, provider payloads, or credentials.

### Task 4: Qualify clean setup, configuration, dependencies, and migrations

**Files:**
- Create: `docs/release/phase-9-clean-setup.md`
- Create: `scripts/phase9/qualify_clean_setup.py`
- Create: `tests/phase9/test_clean_setup.py`
- Create: `tests/phase9/test_configuration_diagnostics.py`
- Create: `tests/phase9/test_migration_release.py`
- Modify: `scripts/export_openapi.py` only if a release check exposes an existing deterministic contract defect and a separate fix is authorized

**Interfaces:**
- `CleanSetupReport(python_version: str, uv_lock_ok: bool, frontend_lock_ok: bool, migration_head: str, import_inert: bool, external_calls: int, created_paths: tuple[str, ...])` is bounded and secret-free.
- `class SetupProbe(Protocol): run(command: tuple[str, ...]) -> ProbeResult`.
- `validate_configuration(settings: Mapping[str, str | None]) -> ConfigurationReport` classifies each required/optional value as `VALID`, `MISSING`, `MISCONFIGURED`, or `UNAVAILABLE` without printing values or contacting services.
- `qualify_database(path: Path, *, baseline: str | None = None) -> MigrationReport` uses only normal Alembic invocation and reports revision/table/side-effect facts.

- [ ] **Step 1: Write RED tests.** Use an empty temporary environment and fake command runner to prove documented Python/uv/Node/npm/lockfile/migration prerequisites, no `create_all`, no implicit migration/import DB creation, no credentials, and exact head `0007_phase8_visual_provenance`. Test both clean latest migration and the historical baselines selected during discovery.
- [ ] **Step 2: Run RED.** Run `uv run pytest tests/phase9/test_clean_setup.py tests/phase9/test_configuration_diagnostics.py tests/phase9/test_migration_release.py -q`.
- [ ] **Step 3: Implement the release probes/documented commands.** Use explicit argument arrays, temporary paths under a controlled root, safe subprocess decoding including the Windows `PYTHONUTF8=1` lesson, and no provider/Docker/model startup. Do not add application startup migrations or change the normal DB.
- [ ] **Step 4: Run GREEN and setup/static gates.** Run focused pytest, `uv run ruff check scripts/phase9 tests/phase9`, `uv run mypy scripts/phase9 tests/phase9`, `uv lock --check`, and `git diff --check`.
- [ ] **Step 5: Commit.** `git add docs/release scripts/phase9 tests/phase9 && git commit -m "test(phase9): qualify clean setup and migrations"`.

**Acceptance evidence:** generic setup requires no credentials and makes no
provider call; normal AAOS database remains untouched; clean install and
migration results include sizes/hashes but never secrets.

**Stop condition:** a setup requirement cannot be reproduced without a real
credential, an implicit migration is observed, or a historical migration
requires destructive rewrite.

### Task 5: Freeze and execute domain-specific evaluation

**Files:**
- Create: `tests/phase9/evaluation/manifest.py`
- Create: `tests/phase9/evaluation/harness.py`
- Create: `tests/phase9/evaluation/metrics.py`
- Test: `tests/phase9/test_evaluation_manifest.py`
- Test: `tests/phase9/test_evaluation_metrics.py`
- Test: `tests/phase9/test_evaluation_harness.py`
- Create: `docs/release/phase-9-evaluation-protocol.md`

**Interfaces:**
- `Phase9EvaluationManifest(schema_version: str, calibration_ids: tuple[str, ...], heldout_ids: tuple[str, ...], domains: tuple[str, ...], sha256: str)` is frozen and validates disjointness, ownership, balanced language metadata, and canonical hash.
- `Phase9EvaluationReport(manifest_sha256: str, domain_reports: tuple[DomainReport, ...], model_calls: int, rag_calls: int, external_writes: int, fixture_mutations: int)` contains separate retrieval, generation/NLI, citation, agent/tool, and multimodal reports.
- `run_deterministic_evaluation(manifest: Phase9EvaluationManifest, *, adapters: EvaluationAdapters) -> Phase9EvaluationReport` uses fake adapters by default and cannot consume real heldout IDs through ordinary pytest.
- Metrics are typed functions: `recall_at_k`, `unsupported_claim_rate`, `nli_confusion`, `citation_accuracy`, `routing_domain_report`, `visual_selection_report`; none returns a blended primary score.

- [ ] **Step 1: Write RED tests.** Freeze calibration/heldout disjointness and hashes before any execution; prove metric denominators, EN/AR partitioning, retrieval/generation/NLI/citation separation, agent/tool authority, multimodal provenance, and zero model/RAG/provider calls in normal tests. Add a guard for real heldout IDs.
- [ ] **Step 2: Run RED.** Run `uv run pytest tests/phase9/test_evaluation_manifest.py tests/phase9/test_evaluation_metrics.py tests/phase9/test_evaluation_harness.py -q`.
- [ ] **Step 3: Implement deterministic manifests, metrics, and report assembly.** Reuse existing Phase 4–8 evaluation contracts; do not alter acceptance thresholds or select cases from current failures. A manual local-model probe, if separately authorized, produces a clearly separate capability section.
- [ ] **Step 4: Run GREEN and evaluation regressions.** Run `uv run pytest tests/phase4* tests/test_rag* tests/test_phase5* tests/test_phase6* tests/phase7* tests/phase8 -q`, then Ruff, mypy, lock, and diff checks.
- [ ] **Step 5: Commit.** `git add docs/release tests/phase9 && git commit -m "test(phase9): freeze whole-system evaluation protocol"`.

**Acceptance evidence:** no current Qwen/Granite/Ministral/other result can
change fixture membership or thresholds; reports expose domain metrics and
sample sizes separately; real external writes remain zero.

**Stop condition:** the proposed metric requires redefining a frozen Phase 4/5/8 contract, inspecting real heldout outcomes, or blending security with quality; stop for governance review.

### Task 6: Measure performance, resources, and Windows behavior

**Files:**
- Create: `scripts/phase9/benchmark.py`
- Create: `scripts/phase9/windows_qualification.py`
- Test: `tests/phase9/test_benchmark_reporting.py`
- Test: `tests/phase9/test_windows_qualification.py`
- Create: `docs/release/phase-9-performance-resource-report.md`

**Interfaces:**
- `BenchmarkSample(operation: str, elapsed_ms: float, sample_kind: Literal["WARM","COLD"], implementation_kind: Literal["REAL_LOCAL","FAKE","SYNTHETIC"], hardware_profile: str)` is bounded and immutable.
- `BenchmarkSummary(operation: str, sample_count: int, median_ms: float, p95_ms: float | None, samples: tuple[BenchmarkSample, ...])` calculates p95 only when the configured sample-size rule permits it.
- `collect_windows_qualification(*, runner: QualificationRunner, root: Path) -> WindowsQualificationReport` tests CRLF/LF, path containment, junction/symlink handling, subprocess decoding, cleanup, Node/npm, and browser startup without credentials.

- [ ] **Step 1: Write RED tests.** Prove canonical sample fields, warm/cold separation, no p95 for undersized samples, fake latency labeling, 6 GB/16 GB hardware disclosure, and safe Windows path/process cleanup.
- [ ] **Step 2: Run RED.** Run `uv run pytest tests/phase9/test_benchmark_reporting.py tests/phase9/test_windows_qualification.py -q`.
- [ ] **Step 3: Implement measurement/report helpers.** Measure only operations explicitly supplied by the caller; never load models, contact providers, run external writes, or claim simultaneous GPU residency. Keep resource sampling optional and label unavailable measurements.
- [ ] **Step 4: Run GREEN and static gates.** Run focused tests, Ruff, mypy, and diff-check. Execute real local benchmarks only through a separately named manual release command after fixture/evidence review.
- [ ] **Step 5: Commit.** `git add scripts/phase9 tests/phase9 docs/release && git commit -m "test(phase9): add performance and Windows qualification"`.

**Acceptance evidence:** every number carries sample count, warm/cold, hardware, model/revision, and real/fake labels; no fake adapter latency is presented as provider/model latency.

**Stop condition:** a benchmark would need simultaneous BGE/Qwen/NLI/VLM residency not proven on the 6 GB target, or local resource data cannot be collected without exposing sensitive process data.

### Task 7: Produce the labeled demo, documentation, claims, and privacy package

**Files:**
- Create: `docs/release/phase-9-demo-script.md`
- Create: `docs/release/phase-9-limitations.md`
- Create: `docs/release/phase-9-portfolio-claims.md`
- Create: `docs/release/phase-9-privacy-and-public-release-audit.md`
- Modify: `README.md` only for evidence-backed release links and status wording after the audit passes
- Modify: `docs/ARCHITECTURE.md` only for cross-links that remove duplication
- Test: `tests/phase9/test_demo_claims.py`
- Test: `tests/phase9/test_public_release_scan.py`

**Interfaces:**
- `PortfolioClaim(claim_id: str, text: str, evidence_refs: tuple[str, ...], scope: Literal["REAL_LOCAL","DETERMINISTIC_FAKE","HISTORICAL_EXTERNAL","NOT_SUPPORTED"])` is closed and rejects claims with no evidence.
- `DemoStep(step_id: str, label: Literal["REAL_LOCAL_BEHAVIOR","DETERMINISTIC_FAKE_BEHAVIOR","SYNTHETIC_DATA"], action: str, expected_observation: str, external_writes: int)` is bounded and immutable.
- `scan_public_release(repo_root: Path) -> PublicReleaseReport` reports secrets/private paths/auth artifacts/normal DB/model weights without printing matching content.

- [ ] **Step 1: Write RED tests.** Prove the end-to-end demo labels dashboard/RAG/visual/computation/task/approval/Activity/health steps, marks fake adapters as fake, records zero external writes, and rejects unsupported claims. Scan tracked/ignored release candidates for credentials, cookies, OAuth state, university-private data, normal DB files, model weights, and temporary screenshots.
- [ ] **Step 2: Run RED.** Run `uv run pytest tests/phase9/test_demo_claims.py tests/phase9/test_public_release_scan.py -q`.
- [ ] **Step 3: Implement docs and bounded scanners.** Do not add a product demo mode unless discovery proves fixtures cannot support the demo. Do not include raw matched secrets in reports.
- [ ] **Step 4: Run GREEN and documentation/static checks.** Run focused pytest, all browser/security artifact tests, frontend tests/typecheck/build/API drift, Ruff/mypy, and `git diff --check`.
- [ ] **Step 5: Commit.** `git add docs/release tests/phase9 README.md docs/ARCHITECTURE.md && git commit -m "docs(phase9): document safe demo and release claims"`.

**Acceptance evidence:** README, architecture, threat model, setup,
evaluation, demo, limitations, attribution, and academic-integrity wording
agree; no claim implies a real provider action beyond the one historical Phase
6B Calendar CREATE.

**Stop condition:** any historical sensitive material is discovered, a claim exceeds evidence, or documentation would imply Phase 9 performed a real write; stop and report rather than rewriting history.

### Task 8: Assemble the release checklist and sanitized evidence package

**Files:**
- Create: `scripts/phase9/release_check.py`
- Create: `tests/phase9/test_release_check.py`
- Create: `tests/phase9/test_evidence_package.py`
- Create: `docs/release/phase-9-release-checklist.md`
- Create: `docs/release/phase-9-evidence-package.md`
- Modify: none in frozen phase docs until human acceptance

**Interfaces:**
- `Phase9ReleaseChecklistResult(checks: tuple[ReleaseCheck, ...], critical_failures: int, important_findings: int, external_writes: int, secrets_exposed: int, ready_for_human_acceptance: bool)` is bounded and immutable.
- `run_release_checklist(*, manifest: Phase9ReleaseManifest, reports: tuple[object, ...], environment: ReleaseEnvironment) -> Phase9ReleaseChecklistResult` executes deterministic checks only; Docker/browser/local-model checks are injected reports, never silently assumed.
- `build_evidence_package(*, manifest: Phase9ReleaseManifest, reports: tuple[object, ...], output_dir: Path) -> EvidenceIndex` writes only bounded sanitized reports through temp-file/fsync/atomic-rename where supported and returns hashes.

- [ ] **Step 1: Write RED tests.** Cover clean tree/tag/migration, earlier phase regressions, tests/static/API drift, threat/security, fake action lifecycle, evaluation hashes, clean setup, browser/demo, performance/resource labels, docs/claims, cleanup, zero writes, and independent-review status. Reject missing/manual-gate evidence and any secret/private artifact.
- [ ] **Step 2: Run RED.** Run `uv run pytest tests/phase9/test_release_check.py tests/phase9/test_evidence_package.py -q`.
- [ ] **Step 3: Implement the checklist/report index.** Preserve evidence immutability, avoid raw outputs, and require explicit status for manual Docker/browser/model gates. Do not create a Phase 9 tag automatically.
- [ ] **Step 4: Run GREEN and the full release verification.** Run `uv run pytest -q`, frontend tests/typecheck/build/API drift, `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy src`, `uv run mypy src/aaos scripts/export_openapi.py`, `uv lock --check`, `git diff --check`, import smoke, and the explicitly authorized local Docker/browser gates.
- [ ] **Step 5: Perform independent review.** Review the entire branch against the Phase 9 spec and frozen tags. Resolve only Critical/Important findings with new commits and rerun the review; required result is `PASS_NO_CRITICAL_OR_IMPORTANT_FINDINGS`.
- [ ] **Step 6: Commit.** `git add scripts/phase9 tests/phase9 docs/release && git commit -m "test(phase9): assemble release qualification evidence"`.

**Acceptance evidence:** one sanitized evidence index ties every release dimension to a hash and command; no secret/raw private payload is included; no provider write, model download, RLM action, or automatic tag occurs.

**Stop condition:** any earlier phase regression, missing evidence, unresolved Critical/Important finding, secret leak, nonzero unauthorized write, or ambiguous external action outcome blocks release acceptance.

### Task 9: Human acceptance and freeze readiness (verification only)

**Files:**
- Modify: `docs/GOVERNANCE_LEDGER.md` only after explicit human acceptance
- Modify: `docs/PHASES.md` only after the governance convention requires a status entry
- Create: `tests/phase9/test_phase9_acceptance.py`

**Interfaces:**
- `run_phase9_acceptance()` consumes only the already-produced manifest and sanitized report index and returns a bounded `Phase9AcceptanceReport`; it performs no provider/model/RAG/write operation.
- The acceptance report records exact starting/final HEAD, tag targets, migration head, test totals, security/evaluation/performance/demo evidence hashes, external writes, model downloads, RLM work, and cleanup status.

- [ ] **Step 1: Write RED acceptance tests.** Prove acceptance cannot pass with a dirty tree, missing human decision, missing report hash, nonzero external writes, unresolved secret/authority leak, missing manual gate, or a Phase 8 tag mismatch.
- [ ] **Step 2: Run RED.** Run `uv run pytest tests/phase9/test_phase9_acceptance.py -q`.
- [ ] **Step 3: Implement only report validation.** Do not mark `PHASE_9=PASSED` or create `phase-09-passed` automatically.
- [ ] **Step 4: Run GREEN and all release gates.** Run the full suite and the final independent review package.
- [ ] **Step 5: Stop for human review.** The final state is `READY_FOR_HUMAN_ACCEPTANCE`, not passed/frozen. A separate human authorization is required before governance docs or a freeze tag change.

**Acceptance evidence:** the only allowed next decision is explicit human acceptance or a bounded remediation request. No Phase 10 or later work starts here.

## Cross-task verification matrix

| Spec invariant | Plan coverage |
| --- | --- |
| No RLM/Deep Ask/recursive authority | Global constraints; Tasks 1, 2, 8, 9 |
| Phase 4–8 ownership preserved | Tasks 0, 2, 4, 5, 8 |
| Zero unauthorized writes and no automatic retry | Tasks 2, 3, 5, 8, 9 |
| Prompt-injection stays data | Task 1 and Task 5 |
| Browser/API/error/secret privacy | Tasks 1, 3, 7, 8 |
| Docker isolation and computation UNKNOWN_OUTCOME | Tasks 2, 3, 6, 8 |
| Visual provenance/source-derived distinction | Tasks 1, 5, 8 |
| Calibration/heldout freeze and no leakage | Tasks 0 and 5 |
| Clean install/config/migration/import inertness | Task 4 |
| Performance/resource/Windows qualification | Task 6 |
| Demo/claim/limitations/academic integrity | Task 7 |
| Evidence hashes and final human gate | Tasks 0 and 8–9 |

## Plan self-review and stop gate

- The plan maps every roadmap critical requirement to a task and a concrete
  test/report artifact.
- Every code-bearing task has RED, minimal GREEN, focused verification, static
  checks, and a focused commit; documentation-only changes are explicitly
  treated as documentation gates rather than artificial product tests.
- All interfaces used by later tasks are defined in earlier task blocks or
  are existing frozen contracts.
- No step requires a real credential, provider call, model download, RLM,
  migration, Phase 6 authority change, or frozen-worktree modification.
- Manual Docker/browser/local-model gates are injected evidence and cannot be
  silently replaced by fake success.
- No task creates a release tag automatically; human acceptance remains the
  final gate.

**Stop gate:** `HUMAN_REVIEW_OF_PHASE9_SPEC_AND_PLAN`.
