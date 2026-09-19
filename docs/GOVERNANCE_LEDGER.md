# AAOS governance status and decision ledger

This is the authoritative status/decision ledger. Roadmap, project and
architecture summaries are views of this ledger, not independent approvals.
Evidence recorded here is not a substitute for missing acceptance results.

## Vocabulary

| Status | Meaning |
| --- | --- |
| PLANNED | Scope proposed/documented; no execution authorization implied |
| AUTHORIZED | Human permission for a specified scope, not completion |
| IMPLEMENTED | Implementation exists; neither verification nor acceptance implied |
| VERIFIED | Named verification evidence exists; does not imply human approval |
| HUMAN_APPROVED | Explicit human review of the named scope/evidence |
| PASSED | All formally required gates satisfied and accepted for that scope |
| FROZEN | Accepted baseline pinned by an actual Git tag/commit |
| SUPERSEDED | Historical decision retained but replaced for future work |
| KNOWN_LIMITATION | Recorded limitation, never silently treated as a passed gate |

These are separate evidence dimensions, not automatic promotions. Historical
ACCEPTED maps to accepted evidence only; it does not manufacture a freeze tag.
Unresolved evidence remains explicitly unresolved.

## Phase evidence

| Scope | Status / evidence | Constraint |
| --- | --- | --- |
| Phase 0 | Acceptance unresolved; historical `phase-00-passed` at `55d2293` is a final-acceptance planning commit per retained roadmap record | No retroactive 0G/0H acceptance; preserve tag |
| Phase 1 | FROZEN: `phase-01-passed`, `8728aee00f4d6cddc8d1cc9b8f1d41a1c4684fef` | Historical accepted baseline |
| Phase 2 | Historical ACCEPTED at `736c8498ab1fe2c1d4d425ba4625774eaf791e93`; NOT FROZEN | No recorded `phase-02-passed`; do not invent one |
| Phase 3 | FROZEN: `phase-03-passed`, `12a27f7a77ed79a3e638d9fc6bf4509840a9b326` | No reopening |
| Phase 4 | FROZEN: `phase-04-passed`, `e99c494628952e0a1ebcafa2450c87611b4dc6c8` | No reopening or held-out rerun |
| Phase 5 | AUTHORIZED; partially IMPLEMENTED; not PASSED/FROZEN | Corrective gate blocks Slice 7 / Slice 8 acceptance work |
| Phase 6A | IMPLEMENTED / VERIFIED as the durable approval/execution prerequisite exercised by the frozen Phase 6B workflow; no separate freeze tag is recorded here | Preserve the approval/execution authority boundary; do not invent a standalone Phase 6A freeze |
| Phase 6B | FROZEN: `phase-06b-passed`, `40ff9d3ef6e0e3010982dabfb37cb597aceeb6ed` | Real Google Calendar acceptance reached `CONFIRMED_SUCCESS`; exactly one real CREATE attempt was performed; do not reopen without defect evidence |
| Phase 6C | OPTIONAL / NOT PURSUED | GitHub read-only remains outside the current scope; no GitHub write authority follows |
| Phase 7 | PASSED / FROZEN by whole-phase human acceptance; 7A–7G HUMAN_ACCEPTED; freeze tag `phase-07-passed` targets the final governance commit | Settings route and favicon carryovers are closed; no Phase 8 authority follows |
| Phase 8 | PASSED / FROZEN by whole-phase human acceptance; Tasks 0–10 HUMAN_ACCEPTED; freeze tag `phase-08-passed` targets the final governance commit `b07fa31dd95978e986269e4572fecb07d8b3e94a` | Restricted computation and multimodal vision are accepted within the recorded boundaries; RLM and Phase 9 remain out of scope |
| Phase 9 | PASSED / FROZEN by whole-phase human acceptance; Tasks 0–9 HUMAN_ACCEPTED; freeze tag `phase-09-passed` targets the final governance commit | Release qualification is accepted with the recorded non-blocking limitations; no Phase 10 or RLM authority follows |

Phase 0/2 qualifications are preserved from the pre-amendment roadmap, not
new acceptance adjudications. Tags are historical evidence; a misleading tag
alone cannot establish acceptance.

## Phase 5 implementation evidence

Human approval source: the explicit roadmap-review amendment instruction
received 2026-09-08, plus the earlier slice approvals retained in this task.
Commit existence is implementation evidence only; this documentation task
does not claim fresh production verification.

Specific retained human instruction evidence: **"Continue AAOS Phase 5 from
the human-approved specification checkpoint"**, expected HEAD
`99ef3d9cf7902de06d5e9929b5c6d43f209beb82`, authorizing Slice 1, explicitly
states **"HUMAN-APPROVED ACCEPTANCE TARGETS — Freeze"** (.99 structured
validity, .95 tool choice, all listed safety/runtime zeroes) and
**"HUMAN-APPROVED EVALUATION SPLIT — Freeze before implementation tuning"**
(72 calibration, English 36/Arabic 36; 24 held-out, English 12/Arabic 12).
That instruction also defines the unique-correct-action tool denominator and
8/1/2 limits with 60/120/5-second timeouts. These approvals are conversation
evidence, not inferred from the stale committed "proposed" wording or from
Slice 8 implementation. Dataset construction, membership freeze, execution
and acceptance have NOT thereby occurred. This entry preserves the specific
approval source without fabricating a historical verification report.

| Scope | Commit evidence | Human review / next gate |
| --- | --- | --- |
| Original design/plan | `99ef3d9` | HUMAN_APPROVED before Slice 1 |
| Slice 1 | `cf6e2e4` | IMPLEMENTED / HUMAN_APPROVED |
| Slice 2 | `0fc1a2b` | IMPLEMENTED / HUMAN_APPROVED |
| Slice 3 | `0d732bb` | IMPLEMENTED / HUMAN_APPROVED |
| Slice 4 | `93d879f` | IMPLEMENTED / HUMAN_APPROVED |
| Slice 5 | `90c6908` | IMPLEMENTED / HUMAN_APPROVED |
| Slice 6 | `e0f468d1ffa103591a6ffdef71162acec351ee86` | IMPLEMENTED / NOT_YET_HUMAN_APPROVED / CORRECTIVE_REVIEW_REQUIRED |
| C1–C7 | No corrective implementation authorized by this amendment | Separate human authorization, TDD, verification and review required |
| Slice 7 | NOT AUTHORIZED; conditional useful domain operation | If not justified/approved, NOT_IMPLEMENTED_BY_SCOPE; handler disabled |
| Slices 8–10 | PLANNED | Do not bypass corrective gate or their own authorization |

## Decisions and explicit supersession

- 2026-09-16: HUMAN_APPROVED sequencing decision: use the frozen
  `phase-06b-passed` baseline at
  `40ff9d3ef6e0e3010982dabfb37cb597aceeb6ed` for Phase 7 design work;
  optional Phase 6C is not pursued. This records actual repository evidence
  and the human direction to proceed without manufacturing Phase 5 PASSED or
  withdrawing any unresolved Phase 5 limitation.
- 2026-09-16: Phase 7 dashboard/operational-visibility architecture sections
  1–7 and the exact written design specification at commit `095979f` are
  HUMAN_APPROVED. Production implementation is authorized slice-by-slice via
  reviewed implementation plans; no Phase 7 PASSED/FROZEN claim is implied.
- 2026-09-16: Phase 7A browser/API foundation TECHNICAL_GATE_PASS and
  HUMAN_ACCEPTED at `61313d0`. Evidence included focused 7A tests, full
  repository pytest, Ruff, strict mypy, uv lock check, deterministic OpenAPI
  -> TypeScript drift, browser-artifact leak scan, import-inert startup
  regression coverage, and a clean worktree. This accepts 7A only.
- 2026-09-17: Phase 7B frontend shell/design-system/browser acceptance was
  HUMAN_ACCEPTED at `0f887a2f867648f95e69abe534978a1482f69df1`. Evidence
  included frontend tests (26/26), full pytest (2091 passed, 5 skipped,
  1 warning), canonical mypy gates, lint/typecheck/production build/API
  drift, Ruff, uv lock and diff checks, browser-artifact leak scanning,
  production backend/proxy/frontend runtime checks, 1440/768/390 browser
  acceptance, keyboard/focus/overflow checks, fake-content audit, and the
  SectionCard token correction at that SHA. No external writes occurred; the
  disposable browser acceptance database was removed after evidence capture;
  the final worktree was clean. The broader `mypy src tests` command remains
  a non-blocking pre-existing Phase 6B diagnostic warning. This accepts 7B
  only; it does not mark Phase 7 PASSED/FROZEN or authorize 7C.
- 2026-09-18: Phase 7C academic read-only experience and production browser
  acceptance was HUMAN_ACCEPTED at
  `26e517bb8967527de435ffba11a77ff1c00000a0` after the server/client route
  parser defect was corrected and the complete Task 9 evidence passed. Tasks
  1–8 were implemented and reviewed; the accepted boundary contains no 7D
  behavior, no external writes, and a clean worktree after disposable fixture
  cleanup. This accepts 7C only; Phase 7 remains not PASSED/FROZEN.
- 2026-09-18: Phase 7D Ask AAOS / validated local RAG experience was
  HUMAN_ACCEPTED at
  `ebae43800f6600ef61c07c355c9ad085b1f5f483` after the repository/frontend
  verification gates, production responsive/browser/security acceptance, and
  real local BGE-M3 retrieval, local Qwen3.5-4B generation through LM Studio,
  and pinned mDeBERTa NLI validation passed. Grounded retrieval returned HTTP
  200 `ANSWER` with a trusted citation; unsupported retrieval returned HTTP
  200 `INSUFFICIENT_EVIDENCE` with zero citations. No fabricated answers or
  citations, external writes, external-provider/network requirements, or
  credential leakage occurred; the disposable database/index were removed
  and LM Studio residency was cleaned. The Windows runtime required
  `PYTHONUTF8=1` for one local subprocess because the default CP1252 decoder
  could not decode its output; this was an environment-only, non-blocking
  limitation with no product-code change. This accepts 7D only; Phase 7
  remains IN_PROGRESS and 7E remains NOT_STARTED.
- 2026-09-18: Phase 7E tasks/approvals operational-visibility experience was
  HUMAN_ACCEPTED at implementation boundary
  `459431a11f81d567642b3d531a2db4c2ad4976dc`, with final acceptance
  verification performed at `e3091f52e92cee6d4cbd3ab6d512cd32567b9c9d`.
  Durable `TaskRecord` persistence is present at Alembic head
  `0006_phase7e_task_records`; Phase 6 remains authoritative for capability,
  approval, claim, execution, reconciliation and `UNKNOWN_OUTCOME`. The
  trusted approver context is derived server-side and cannot be supplied by
  the browser. Production approve/reject workflows passed, refresh preserved
  durable `APPROVED` and `REJECTED` states, duplicate/opposite terminal
  decisions returned bounded `APPROVAL_NOT_ACTIONABLE`, and
  `UNKNOWN_OUTCOME` rendered distinctly and honestly. Deterministic
  `FakeExternalAdapter` acceptance was used; no automatic external-write
  retry or real external provider write occurred. The earlier fixture clock
  mismatch was closed as an acceptance-fixture defect using a new
  preflight-validated frozen fixture without production-code changes. No
  Phase 7F functionality was introduced and the final tracked worktree was
  clean. This accepts 7E only; Phase 7 remains IN_PROGRESS and 7F remains
  NOT_STARTED.
- 2026-09-18: Phase 7F browser-safe Activity history, deterministic composite
  projection and cursors, same-origin SSE streaming with reconnect/replay/
  deduplication, passive System Health, and responsive Activity/System Health
  surfaces were HUMAN_ACCEPTED at
  `8f6691ecaeff64612da89187e57bbcf8ba84f16a`. Evidence included the
  canonical backend suite (2086 passed, 5 skipped, 1 warning), focused Phase
  7F backend tests (45 passed), frontend tests (161 passed), Ruff, both
  canonical mypy gates, uv lock, frontend typecheck/lint/build/API-drift,
  production same-origin browser acceptance, responsive checks, security and
  artifact audits, and zero external writes. The final fixes changed only
  System Health and Activity presentation/tests; no migration, duplicate
  Activity ledger, Phase 7E authority change, model/RAG/provider side effect,
  or Phase 7G functionality was introduced. The final worktree was clean.
  Two pre-existing global integration carryovers remain explicitly open for
  Phase 7G: `CARRYOVER_7G_SETTINGS_ROUTE_GAP` (the pre-existing Settings
  navigation points to `/settings` without a matching route; 7G must either
  implement the intended surface or remove/disable the dead item) and
  `CARRYOVER_7G_FAVICON_ASSET_GAP` (the pre-existing `/favicon.ico` 404; 7G
  must provide the intended application icon). Whole Phase 7 must not pass
  while either carryover remains. This accepts 7F only; Phase 7 remains
  IN_PROGRESS, 7G remains NOT_STARTED, and no `phase-07-passed` tag is
  created.
- 2026-09-18: `PHASE_7G=HUMAN_ACCEPTED` at
  `ac8cd6b75d4bb6809b3a4bf20e21252c45e31a64`. The accepted boundary removes
  the unsupported Settings navigation item and adds the repository-native
  application icon, with exact route/mutation inventories, same-origin
  browser/API enforcement, import-inert startup, authority/privacy audits,
  and responsive/accessibility integration checks. Canonical verification
  recorded 3044 passed and 5 skipped backend tests with one existing Chroma
  deprecation warning, 9 focused Phase 7G backend tests, 173 frontend tests,
  Ruff, both canonical mypy gates, uv lock, typecheck, lint, production
  build, API drift, diff-check, and production browser evidence at 1440,
  1280, 768, and 390 widths. No migration, Phase 6/7E authority change,
  model/RAG call, external browser resource, credential/authority leakage, or
  real external write occurred; the disposable synthetic acceptance fixture
  was removed after evidence capture and the final tracked worktree was clean.
  `CARRYOVER_7G_SETTINGS_ROUTE_GAP` is CLOSED because the dead item was
  removed without inventing a Settings feature. `CARRYOVER_7G_FAVICON_ASSET_GAP`
  is CLOSED because the repository-authored icon is served through the
  approved Next metadata/icon mechanism. This accepts 7G only; Phase 7 remains
  IN_PROGRESS, no `phase-07-passed` tag was created, and Phase 8 was not
  started.
- 2026-09-18: Whole Phase 7 was HUMAN_ACCEPTED and recorded as
  `PHASE_7=PASSED`. Slices 7A–7G are individually HUMAN_ACCEPTED, with the
  accepted 7G implementation/evidence boundary at
  `ac8cd6b75d4bb6809b3a4bf20e21252c45e31a64`; the preceding 7G governance
  checkpoint was `c4058d84e112658acb95044e034d2816b431e112`. Whole-phase
  verification recorded 3,044 backend tests passed (5 skipped, 1 warning),
  10 import-inert/doctor checks passed, 173 frontend tests passed, and PASS
  for Ruff, both canonical mypy gates, uv lock, diff-check, typecheck, lint,
  production build, API drift, browser/security audits, and responsive
  production acceptance. No unresolved carryovers remain: the Settings route
  and favicon gaps were closed in 7G. No real external writes occurred, and
  independent review found no Critical or Important findings. This
  docs-only governance commit is the final commit targeted by the annotated
  `phase-07-passed` freeze tag; Phase 8 was not started.
- 2026-09-08: HUMAN_APPROVED architecture review decision:
  `ROADMAP_ARCHITECTURE_STATUS=REVISE_BEFORE_PHASE6`;
  `PHASE5_CAN_CONTINUE=YES`; `PHASE4_REOPEN_REQUIRED=NO`.
  Approval authorizes this docs amendment only, not corrective production work.
- SUPERSEDED: Phase 5 "implementation has not started" and "targets proposed"
  wording. Slices 1–5 are approved; numeric targets/split were approved before
  Slice 1. Historical Git versions preserve the original proposal.
- SUPERSEDED for future Phase 5 consumption: Phase 4 design sections 7 and 38
  assign history/follow-ups/approvals to Phase 5. Current Phase 5 excludes
  history/follow-up rewriting; Phase 6 owns approvals. Frozen Phase 4 text,
  implementation and tag are unchanged.
- KNOWN_LIMITATION: reviewed consumer scope, final-output lineage, synchronous
  timeout semantics, Slice 6 sanitizer privacy/correctness, effective policy,
  tool output validation and backend event boundaries require C1–C7 review.
  Existing slice approvals are not silently withdrawn or expanded.
- The present document amendment awaits human review after its docs-only
  commit. It does not mark Phase 5 PASSED, Slice 6 HUMAN_APPROVED, or Phase 6
  AUTHORIZED.

Normative detail: [roadmap](PHASES.md),
[Phase 5 design](superpowers/specs/2026-09-07-phase-5-controlled-agent-loop-design.md),
[Phase 5 plan](superpowers/plans/2026-09-07-phase-5-controlled-agent-loop-implementation.md).

## C8 least-authority implementation evidence

The human-authorized C8 amendment narrows model responsibility to
`RETRIEVE_OR_TERMINAL`. The model cannot select tool identity, scope,
arguments, permission, runtime policy, contract metadata or grounded lineage;
trusted AAOS composition supplies those values and returns successful Phase 4
RAG output directly. The v1 evaluation artifacts remain immutable and the
uniform v2 contract is versioned separately. This entry records implementation
evidence only; calibration, held-out execution and human approval remain
future gates.

## C9 trusted decision ownership / V3 evaluation evidence

C9 introduces the distinct `phase5-agent-benchmark-v3` evaluation contract.
V1 and V2 schemas, manifests, fingerprints and historical results remain
immutable. A closed, architecture-derived mapping assigns exactly one primary
owner to every V2 category; the mapping is independent of model outputs and
observed performance. V3 keeps model-routing, trusted-system,
security-hard-gate, runtime-environment and proposal-format-efficiency metrics
as separate domains. Security hard gates remain zero-tolerance and cannot be
hidden by routing accuracy. Calibration and held-out manifests are migrated
uniformly from V2 before any V3 model run; held-out remains unconsumed and
results remain invisible. This C9 slice changes scoring ownership only and
does not alter the production orchestrator or add a trusted pre-model gate.
The historical V2 result remains reportable as the unchanged
`V2_ARCHITECTURALLY_MIXED_HISTORICAL_METRIC=42/72`; proposal-format/repair
efficiency remains a secondary diagnostic rather than routing accuracy.

## C10 numeric routing policy / V4 evaluation contract

C10 is the human-approved, outcome-independent routing acceptance policy for a
new V4 contract. It requires at least 64 model-routing cases in both
calibration and held-out, global routing accuracy `>=0.95`, and exact integer
comparison (at N=64, 61/64 passes and 60/64 fails). Each split must contain at
least 16 cases in each structural stratum: English/Retrieve, English/Terminal,
Arabic/Retrieve and Arabic/Terminal. Coverage is a hard structural gate;
subgroup accuracy is diagnostic only. There is no asymmetric false-terminal
gate. Security and trusted-system gates remain independent zero-tolerance
requirements, and proposal-format efficiency remains a separate domain.

C10 is an engineering evaluation policy for the local/portfolio system, not a
population-reliability or statistical-confidence certification. Its value and
parameters were selected from architecture, risk and small-sample semantics,
not from current model results (`C10_POLICY_SELECTED_FROM_CURRENT_MODEL_RESULTS=NO`).
The V1/V2/V3 contracts and artifacts remain immutable. V4 routing cases are
authored from the frozen `c10-routing-matrix-v1` responsibility boundary and
must be disjoint between calibration and held-out under the predeclared
NFKC/casefold/whitespace equality rule. Trusted-system material is migrated
deterministically from C9 without inflating the routing denominator.

The V4 implementation freezes both manifests before inference; V4 held-out is
created before calibration but remains unconsumed and invisible until separate
authorization. This documentation and contract change performs no model or
RAG execution, does not modify the production orchestrator, and does not mark
Phase 5 passed or freeze a candidate. See the [C10/V4 contract](superpowers/specs/2026-09-10-phase-5-c10-v4-evaluation-contract.md).

## Phase 9 whole-phase acceptance

`PHASE_9=PASSED` was human-accepted for the final implementation/evidence
boundary `bd31a6089d6409bbb31d9781699eac641a6ec40d`. Tasks 0–9 are
`HUMAN_ACCEPTED`; the annotated `phase-09-passed` tag targets the final
governance commit carrying this entry. Canonical verification recorded 3,202
backend tests passed, 6 expected skips and one warning, 43 frontend files with
173 tests passed, Alembic head `0007_phase8_visual_provenance`, a passing
public-release/security audit, and independent review
`PASS_NO_CRITICAL_OR_IMPORTANT_FINDINGS`. Real model downloads, provider calls,
external writes and RLM work were all zero. The earlier `3,044` backend figure
was stale Phase 7 documentation; the canonical final result is `3,202`
passed, with the reconciliation retained in the Phase 9 release checklist.
Historical Ruff-format findings, the combined Phase 9 mypy namespace
diagnostic, author-specific internal planning paths, absent explicit model
weight suffix rules in `.gitignore`, synthetic/fake measurement limitations,
the absence of a real VLM benchmark, and two pre-existing frontend lint
warnings remain documented non-blocking limitations. Phase 10 and RLM remain
not started.

## Phase 8 whole-phase acceptance

`PHASE_8=PASSED` was human-accepted at the final implementation/evidence
boundary `b07fa31dd95978e986269e4572fecb07d8b3e94a`. Tasks 0–10 are
HUMAN_ACCEPTED and the annotated `phase-08-passed` tag targets the final
governance commit. The accepted boundary includes qualified OS-enforced Docker
computation isolation, trusted-only bounded computation, deterministic visual
selection/rendering, typed multimodal vision, immutable visual provenance at
Alembic head `0007_phase8_visual_provenance`, and the distinction between
`DERIVED_VISUAL_EVIDENCE` and `EMBEDDED_PDF`. Existing grounding/NLI safeguards
remain authoritative; no Phase 5 authority widening, Phase 6 bypass, provider
authority, or parallel RAG system was introduced. Observability is closed and
passive, evaluation is deterministic and synthetic, and the final evidence
recorded 3,144 passed tests, 6 skips, one warning, 4 real-Docker qualification
tests, 28 computation/security tests, and the accepted Phase 4–7 regression
totals. Real external writes, real VLM downloads, model/provider calls, and
RLM work were all zero/not started. The historical 28-file formatting baseline
and retained deterministic rather than raw-output evidence are non-blocking
minor findings; no Critical or Important findings remain. Phase 9 remains
PLANNED / NOT STARTED.
