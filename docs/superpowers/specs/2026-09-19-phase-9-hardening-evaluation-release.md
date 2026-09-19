# Phase 9 Hardening, Evaluation, and Portfolio Release Specification

**Status:** planning-only specification on `phase-09-hardening-release`.

**Starting freeze:** annotated tag `phase-08-passed`, dereferenced target
`5dea7f19bbe26e85b968afc63a21c10f0dd4e389`.

## 1. Purpose and roadmap alignment

The repository roadmap names Phase 9 **Release Qualification and Portfolio
Evidence**. Its purpose is to qualify the accepted system for clean setup,
recovery demonstration, and an end-to-end accepted demo with sanitized
portfolio evidence, finalized documentation, and honest metrics and
limitations. The roadmap explicitly says that security, recovery, and
evaluation remain continuous responsibilities of their owning phases and that
Phase 9 consolidates/revalidates existing evidence rather than introducing
late security or product capabilities.

The roadmap's listed work is: end-to-end tests, portal extraction and RAG
evaluation, agent/tool evaluation, prompt-injection and security tests,
resource benchmarks, failure recovery, threat-model finalization,
academic-integrity policy, sanitized demo data, setup and architecture
documentation, demo material, and portfolio metrics. Its critical gates are:

- external writes without approval: zero;
- secrets exposed in logs or the frontend: zero;
- all earlier phase gates still pass;
- clean setup succeeds;
- the end-to-end demo succeeds; and
- documented benchmark/evaluation results exist.

This specification turns those statements into a measurable release gate. It
does not authorize implementation by itself.

## 2. Frozen baseline and authority boundaries

Phase 8 is frozen by `phase-08-passed` at the governance commit above. Phase
7 is frozen by `phase-07-passed`; Phase 6B is frozen by
`phase-06b-passed` at `40ff9d3ef6e0e3010982dabfb37cb597aceeb6ed`; Phase 4 is
frozen by `phase-04-passed`. Phase 6A is implemented/verified as the durable
approval prerequisite consumed by Phase 6B. Phase 6C is optional and not
pursued. Phase 5 remains governed by its recorded corrective-review status;
Phase 9 does not silently manufacture a Phase 5 freeze.

The accepted architecture remains:

1. Moodle/browser authentication and synchronization are local, read-only
   academic ingestion boundaries.
2. Phase 4 owns document versions, page text, hybrid retrieval, grounded
   generation, NLI validation, citations, and `INSUFFICIENT_EVIDENCE`.
3. Phase 5 owns a bounded `RETRIEVE_OR_TERMINAL` proposal loop. Model text,
   retrieved content, observations, and provider responses are data, never
   authority.
4. Phase 6 owns capability permission, immutable `PreparedAction`, human
   approval, one-shot claim/consumption, durable `EXECUTING`, external
   execution, reconciliation, and `UNKNOWN_OUTCOME`. Phase 9 uses fake
   adapters for this boundary and never creates a new write path.
5. Phase 7 owns the same-origin dashboard/API boundary, read-only academic
   surfaces, approvals/tasks, Activity/SSE, System Health, and Ask AAOS.
6. Phase 8 owns trusted-only bounded computation, qualified OS isolation,
   deterministic visual selection/rendering, typed vision, immutable visual
   provenance at Alembic head `0007_phase8_visual_provenance`, and the
   `DERIVED_VISUAL_EVIDENCE` versus `EMBEDDED_PDF` distinction.

 Phase 9 must preserve these owners. It may add verification harnesses,
release documentation, bounded diagnostics, and evidence tooling. It may not
add a browser route, proposal field, provider, approval bypass, retry path,
second RAG system, unrestricted REPL, or new external write integration.

## 3. Explicit non-goals and hard stops

The following are out of scope unless a future separately approved phase
changes the roadmap:

- RLM, Recursive Language Models, Deep Ask AAOS, recursive model delegation,
  or multi-agent recursion;
- new external providers, Calendar/GitHub write integrations, or new write
  authority;
- unrestricted Python, shell, filesystem, network, Docker-control, or
  credential functionality;
- dashboard product areas unrelated to release qualification;
- Kubernetes, cloud deployment, or scale infrastructure added for portfolio
  optics;
- dependency upgrades, migration rewrites, or Git history rewrites merely to
  improve a release report;
- real provider writes during Phase 9 acceptance;
- downloading or loading a VLM as a prerequisite to the planning gate;
- publishing real university data, credentials, cookies, OAuth state, model
  weights, private evidence, or normal application databases.

Planning or implementation must stop and report a Critical scope issue if a
release requirement appears to need any of those capabilities, a Phase 6
authority redesign, a destructive migration rewrite, real credentials, or
uncontrolled provider access.

## 4. Release-readiness definition

"Release ready" means the complete accepted AAOS repository can be installed
and demonstrated from documented steps, and the evidence package makes no
claim stronger than the observed boundary. Readiness is a vector, not one
aggregate score:

| Dimension | Required evidence |
| --- | --- |
| Correctness | deterministic unit/integration/contract suites and end-to-end smoke pass |
| Security | threat matrix, adversarial corpus, authority-negative tests, secret/browser scans, and zero Critical/Important review findings |
| Recovery | fake-only lifecycle/reconciliation matrix with explicit `UNKNOWN_OUTCOME` and no automatic retry |
| Reproducibility | clean-environment setup, lockfile, migration, import-inert and configuration checks |
| Evaluation | frozen manifests, domain-specific metrics, calibration/heldout separation, and hashes |
| Performance/resources | median/p95 where sample size supports it, hardware/configuration, warm/cold labels, and 6 GB VRAM constraints |
| Privacy | logs, API, browser, artifacts, and public-tree scans contain no secrets/private data |
| Demo safety | synthetic data is labeled, fake adapters are labeled, and no real provider write is implied |
| Documentation | setup, architecture, security, evaluation, limitations, and academic-integrity framing agree with evidence |
| Installability | documented Python/uv/Node/Alembic/Docker prerequisites are reproducible without credentials |
| Maintainability | focused release scripts, typed reports, bounded artifacts, and no duplicate ledgers |

Each dimension is reported independently. A missing dimension is a release
blocker; it is never hidden by averaging other dimensions.

## 5. Whole-system threat model

The Phase 9 threat model uses the following fixed review table. Each future
task must attach a concrete test or evidence artifact to its row.

| Asset/entry point | Boundary and existing mitigation | Phase 9 verification | Residual risk to disclose |
| --- | --- | --- | --- |
| Moodle cookies, auth state, session redirects | local auth/runtime; read-only adapter; secret exclusion | auth/error/log redaction scans and expired-session fixtures | connected-account setup remains user-specific |
| Google OAuth credentials/tokens | Phase 6 adapter boundary; never model/browser-visible | tree/log/browser scans and fake adapter tests | one historical real CREATE is evidence, not ongoing provider availability |
| environment secrets | configuration and process boundary | safe-config diagnostics and secret sentinel scans | operators can still misconfigure their environment |
| document/retrieved/visual prompt injection | content is untrusted data; closed proposals and validators | frozen text/visual/tool-observation corpus | semantic novelty beyond corpus remains residual risk |
| malicious PDFs/images | bounded parser/render/vision inputs | malformed/corrupt/oversize fixtures and cleanup checks | parser/library vulnerabilities remain dependency risk |
| arbitrary computation | Docker-qualified, network-none, read-only-root, cap-drop, non-root sandbox | real Docker qualification plus fake quota/crash suite | target hardware/runtime availability limits execution |
| filesystem traversal/symlink/junction | trusted path containment and bounded task roots | Windows path/symlink/junction corpus | platform-specific filesystem behavior requires local qualification |
| browser-to-backend/SSE leakage | same-origin proxy, typed API errors, passive Activity | route inventory, header/error scans, SSE reconnect tests | deployment proxy configuration remains environmental |
| approval/action authority | Phase 6 immutable records, approver context, atomic claim and reconciliation | fake-only prepare/approve/claim/execute/reconcile matrix | real provider semantics are only covered by the accepted narrow integration |
| malformed providers/model outputs | closed contracts, sanitizers, bounded errors | malformed response and late-result tests | unobserved provider variants require future adapter work |
| replay/race/ambiguous outcome | one-shot operation identity, CAS and `UNKNOWN_OUTCOME` | concurrent/late/crash matrix | reconciliation still requires trusted operator action |
| SQL/path/traceback/raw payload leakage | error boundary and typed event/detail contracts | response/log/artifact scans | internal diagnostics remain bounded rather than exhaustive |
| dependencies/model revisions | lockfiles and explicit model identity/revision | dependency inventory and hash manifest | unbounded ranges and externally managed model artifacts are disclosed |

No row may be marked "secure" merely because a test was written; the report
must name the exact executed evidence and the remaining limitation.

## 6. Frozen evaluation design

Phase 9 evaluation is a set of independent domains with immutable manifests.
Every manifest records schema version, case IDs, ownership, expected labels,
language where applicable, creation method, and a SHA-256 over canonical JSON.
Calibration may guide diagnosis; heldout evidence is final generalization
evidence and is never selected from observed failures. No current model score
may alter case membership or a threshold.

### 6.1 Prompt-injection corpus

Create synthetic sentinel cases for English and Arabic where applicable:

- text documents say to ignore policy, reveal a sentinel secret, call a tool,
  execute Python, approve an action, or send data externally;
- the same intents appear in rendered screenshot, table, diagram, and image
  text fixtures;
- retrieved chunks, fake provider responses, computation output, and visual
  analysis output carry the same hostile strings.

Expected behavior is always data treatment: no authority escalation, no Phase
6 bypass, no computation privilege, no browser/server bypass, and no altered
limits, tool identity, approval identity, citation ownership, or provider
arguments. Real secrets are never used; sentinels are inert values.

### 6.2 External-action safety

Use only `FakeExternalAdapter` and the existing Phase 6 repository/service.
Cover prepare, trusted approval request, approve, reject, trusted
invalidation/revocation, expiry, atomic claim, durable dispatch start,
confirmed success, confirmed failure, ambiguous result, crash before adapter
call, crash after possible adapter call, reconciliation, late result, and
duplicate/concurrent workers. Assert zero real external writes, one operation
attempt, no automatic retry from `UNKNOWN_OUTCOME`, and complete event
lineage.

### 6.3 RAG and academic evaluation

Keep retrieval and generation separate. The frozen benchmark reports:

- retrieval recall@k (where a labeled relevant set supports it), evidence
  selection and distractor handling;
- answer correctness and unsupported-claim rate;
- NLI entailment/contradiction/neutral and false-support rate;
- insufficient-evidence precision/behavior;
- citation document/page/span correctness, including honest handling of
  `DERIVED_VISUAL_EVIDENCE`.

English and Arabic remain separate dimensions where the accepted scope has
both. No blended accuracy hides a language or provenance failure. Fixtures
are reviewable, synthetic/sanitized, and frozen before any optional local
model probe.

### 6.4 Multimodal evaluation

Extend the Phase 8 deterministic fixtures only for release coverage:
selection precision/recall, bounded rendering, table/chart/diagram/equation
observations, `PARTIAL`, unavailable/timeout/failure fallback, visual
provenance, prompt injection, source-versus-derived distinction, and
projection into the existing RAG path. A real VLM is not required unless the
roadmap is separately amended; no VLM is downloaded during planning.

### 6.5 Computation evaluation

Reuse the Phase 8 contracts and isolation harness. Release evidence covers
correct arithmetic/tabular results, timeout, memory/CPU/pid/output pressure,
cleanup, network denial, host-filesystem and environment/credential denial,
Docker socket/device/GPU denial, no image pull, no retry, `UNKNOWN_OUTCOME`,
and import inertness. At least one explicit real-Docker qualification is a
manual local gate; fake tests remain the ordinary deterministic gate.

## 7. Failure and recovery matrix

The release package contains a table with these rows: Moodle unavailable,
session expired, malformed Moodle response, document parse failure/corrupt
PDF, index unavailable, embedding failure, LLM unavailable, NLI unavailable,
VLM unavailable, Docker unavailable, worker crash, computation timeout,
database locked, migration mismatch, Next.js unavailable, FastAPI unavailable,
SSE disconnect, Calendar unavailable, ambiguous provider response, filesystem
full, and permission denial.

Every row specifies user-visible result, internal state, retry policy,
data-loss risk, recovery procedure, and whether automatic retry is allowed.
Rules already fixed by earlier phases are carried forward: ambiguous external
outcomes require reconciliation and no automatic retry; Phase 8 ambiguous
worker outcomes are `UNKNOWN_OUTCOME` with no automatic retry; text/RAG
failures do not fabricate grounded success; browser errors remain bounded.

## 8. Reproducibility, configuration, and database qualification

The clean-install test separates repository setup from personal account
connection. It documents Python 3.11, uv, `uv.lock`, Node/npm and
`frontend/package-lock.json`, Alembic, Docker Desktop/WSL2 when required,
optional LM Studio/model expectations, environment variable names without
printing values, and optional Moodle/Google setup. The generic setup smoke
uses no credentials and must not contact providers.

The dependency inventory records exact pins versus ranges. Current notable
pins include ChromaDB 1.5.9, FlagEmbedding 1.4.2, Google clients, Playwright
1.62.0, pypdfium2 5.13.0, sentencepiece 0.2.2, torch 2.13.0+cu130, and
transformers 5.16.1; Alembic, FastAPI, SQLAlchemy, uvicorn, mypy, pytest, and
Ruff retain range constraints. Phase 9 must not upgrade dependencies merely
to make a report pass; it must either pin a justified risk or document it.

Release database tests start from an empty file, apply migrations through
`0007_phase8_visual_provenance`, verify no `create_all()` or import-time DB
creation, and exercise the supported historical baseline(s) chosen in the
plan. Downgrade is tested only where repository policy permits it. The normal
production database is never used for release fixtures.

Configuration diagnostics classify settings as required, optional,
misconfigured, or unavailable without reading or printing secrets, contacting
providers, downloading models, or starting Docker unnecessarily.

## 9. Safe data, demo, and portfolio evidence

Phase 9 uses repository-owned synthetic/sanitized fixtures. It never ships
real university/private documents, Moodle cookies, OAuth tokens, Google
tokens, personal assignments, auth state, or normal databases. Fixtures are
explicitly labeled `DEMO / SYNTHETIC DATA`; the application must not silently
replace connected data with demo data.

No new product demo mode is planned until discovery proves acceptance
fixtures cannot support the demonstration. The preferred demonstration uses
an isolated fixture and an explicit label rather than a runtime mode. It
shows, in order: dashboard academic reads; grounded Ask AAOS answer and
honest citation; visual evidence; bounded computation; task creation;
approval/rejection; fake execution and reconciliation; Activity; and System
Health. Each step labels what is real local behavior versus deterministic fake
adapter behavior. The demo performs zero real external writes.

## 10. Performance and resource qualification

Measurements are recorded with operation name, sample count, median, p95 only
when the sample count supports it, warm/cold state, hardware, software/model
identity, and whether the result is fake or real local behavior. Planned
operations include backend/frontend startup, course/document reads, retrieval,
Qwen generation if locally available, NLI, ingestion, indexing, SSE delivery,
task/approval mutation, Docker cold start/round trip, page rendering, and the
multimodal pipeline. Fake adapter latency is never presented as provider or
model latency.

The primary target profile is Windows 11, RTX 4050 Laptop GPU with 6 GB VRAM,
and 16 GB RAM. Measure BGE-M3, Qwen, mDeBERTa NLI, optional VLM, Docker
computation, CPU/RAM/VRAM and latency. The report must preserve sequential
residency assumptions: weights are not VRAM usage, no claim of simultaneous
residency is allowed without measurement, and NLI remains CPU as accepted in
Phase 8.

Windows qualification covers PowerShell, CRLF/LF, path/symlink/junction
containment, Docker Desktop desktop-linux/WSL2, subprocess output decoding,
cleanup, Node/npm, and browser startup. The Phase 8 OpenAPI newline lesson is
retained as a release check rather than a reason to normalize unrelated files.

## 11. Privacy, web, and public-release audits

The logging audit classifies important fields as safe, sensitive, or
forbidden. Credentials, cookies, authorization headers, provider payloads,
secret-bearing prompt contents, raw documents where unnecessary, Python
internals, filesystem secrets, OAuth/Moodle/Google state, and raw traceback
details are forbidden in ordinary logs.

The browser artifact scan checks `DATABASE_URL`, backend origins,
Authorization/Bearer strings, OAuth/Google/Moodle state, Docker controls,
host paths, `TrustedApproverContext`, `PreparedAction` raw arguments, provider
payloads, and computation internals. The required result is zero browser
secret/authority leaks.

The API inventory classifies every browser route as read, safe internal
mutation, approval mutation, RAG query, SSE, or health. It compares OpenAPI
with actual routes and proves there is no generic execute/provider/retry/Python,
Docker-control, credential, raw SQL, or filesystem endpoint. Any proposed CSP,
content-type, frame, referrer, or cache headers must first be shown compatible
with same-origin proxy and SSE behavior; no blind header change is part of
this spec.

Public release review scans current tree/history where feasible for secrets,
personal paths/account data, university-private material, auth files,
database files, generated auth state, and ignored runtime artifacts without
rewriting history. License/model/icon/fixture attribution is evidence and
human/legal review, not a legal conclusion.

Documentation must include setup, architecture, security/authority, RAG,
agent, approval/execution, computation, vision, development/testing, demo,
limitations, threat model, evaluation, and academic-integrity framing. The
README must distinguish accepted local behavior, deterministic fake evidence,
the one historical Calendar CREATE, optional external setup, and unsupported
claims. Academic framing is study assistance, course understanding, search,
planning, and calendaring—not autonomous assessed-work completion.

## 12. Evidence package and final checklist

The final package is a sanitized manifest of test totals, benchmark summaries,
security results, migration head, Docker qualification, browser results,
secret scans, claim audit, limitations, commit/tag boundaries, hardware,
dependency/model identities, and hashes. It contains no secret, private raw
payload, model weight, or normal database.

The authoritative release checklist requires:

1. clean tracked tree and exact release commit/tag;
2. accepted phase tags and migration head verified;
3. backend/frontend tests, static checks, API drift, and import smoke;
4. threat/adversarial/secret/privacy/API/browser audits;
5. fake-only action lifecycle and zero real external writes;
6. real Docker qualification where the environment supports it;
7. frozen RAG/multimodal/agent evaluation with separate metrics;
8. clean setup and migration smoke;
9. production browser/demo evidence with safe synthetic data;
10. performance/resource evidence with hardware and sample labels;
11. docs, limitations, claim, attribution, and academic-integrity review;
12. independent review `PASS_NO_CRITICAL_OR_IMPORTANT_FINDINGS`;
13. cleanup of databases, containers, models, auth state, and temporary
    artifacts; and
14. explicit human acceptance before a Phase 9 freeze tag.

## 13. Proposed implementation slices

The companion plan decomposes Phase 9 into these independently reviewable
slices, without starting implementation now:

- **9A — baseline, threat model, and evidence manifest:** inventory the
  accepted tree, tags, routes, migrations, tests, limitations, and release
  evidence format.
- **9B — adversarial/security qualification:** freeze the synthetic
  prompt-injection corpus and run authority, privacy, API, browser, logging,
  and error-boundary audits.
- **9C — external-action and failure recovery qualification:** exercise fake
  Phase 6 lifecycle races, all failure matrix rows, and no-retry/
  `UNKNOWN_OUTCOME` behavior.
- **9D — reproducibility and clean-install qualification:** validate setup,
  lockfiles, configuration diagnostics, migrations, Windows behavior, and
  import inertness.
- **9E — whole-system evaluation:** freeze and execute separate RAG,
  agent/tool, multimodal, and academic-integrity metrics.
- **9F — performance/resource qualification:** measure meaningful local
  operations and the sequential 6 GB resource profile.
- **9G — demo, documentation, claims, and release package:** produce the
  labeled synthetic demo, README/limitations/threat/evaluation docs, claim
  audit, attribution review, and sanitized evidence bundle.
- **9H — final verification and freeze:** run the single checklist, clean all
  runtime state, perform independent review, obtain human acceptance, and only
  then create a named Phase 9 freeze tag.

Each slice is TDD where code or tests are added: RED, minimal GREEN, focused
verification, relevant frozen-phase regressions, static checks, independent
review, and one focused commit. Documentation-only slices use a documented
diff/self-review gate instead of artificial failing tests.

## 14. Acceptance criteria and stop gate

Phase 9 is eligible for human acceptance only when all of the following are
true:

- the exact roadmap purpose and non-goals remain intact;
- all earlier accepted/frozen phase gates still pass without modifying their
  worktrees;
- zero unauthorized real external writes occurred;
- zero browser/log/frontend secret or authority leaks are evidenced;
- threat, adversarial, failure/recovery, RAG, agent, multimodal, computation,
  reproducibility, performance, and demo evidence is present and hashed;
- clean setup and migration qualification pass;
- the end-to-end demo succeeds with clearly labeled synthetic/fake portions;
- documented metrics and limitations do not overclaim;
- independent review reports `PASS_NO_CRITICAL_OR_IMPORTANT_FINDINGS`; and
- a human explicitly accepts the exact release commit before any
  `phase-09-passed` tag is created.

No implementation or acceptance run is authorized by this spec. The stop
gate after planning is `HUMAN_REVIEW_OF_PHASE9_SPEC_AND_PLAN`.
