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

## Current project-owner amendment (2026-09-26)

On 2026-09-26, the project owner established this current AAOS governance
rule. It records the current instruction; it does not assert prior human
acceptance.

- `TECHNICAL_ACCEPTANCE` means objective satisfaction of the canonical
  implementation, deterministic test, evaluation, architecture/security,
  private-data, exact-SHA evidence, and no-blocking-finding requirements.
  Classify each gate as `TECHNICALLY_SATISFIED`,
  `TECHNICALLY_UNSATISFIED`, or `NOT_PROVABLE`. Report
  `TECHNICAL_GATE_CLEAR=YES` only when every applicable requirement passes.

- After a clear technical gate, `LOCAL`, `REVERSIBLE`, `NON-PRODUCTION`
  AAOS engineering may continue without another routine human confirmation.
  This supersedes only older stop/wait instructions that block such work
  after objective technical acceptance. It does not waive requirements or
  turn missing evidence into a pass.

- Do not invent or backdate `HUMAN_APPROVED`, `PASSED`, or `FROZEN` history.
  Historical reports and tags remain evidence only for their recorded scope
  and SHA.

- `HUMAN_RELEASE_AUTHORIZATION` remains required for production deployment
  or configuration changes, external writes with user impact, destructive
  operations, irreversible migrations against real user data,
  credential-sensitive live operations not already narrowly authorized,
  release publication, official phase/release tags, and other high-risk or
  irreversible actions.

This amendment supersedes the downstream human-acceptance handoff clauses in
`docs/superpowers/plans/2026-08-13-phase-0g-security-baseline-implementation.md`,
`docs/superpowers/plans/2026-08-13-phase-0h-phase-0-final-acceptance.md`, and
`docs/superpowers/specs/2026-08-13-phase-0h-phase-0-final-acceptance-design.md`
only where they would stop later local work after a clear technical gate.
Their objective criteria remain in force. `phase-00-passed` remains a release
declaration and is not authorized by this amendment.

## Phase 5 deferred V4 disposition (2026-09-26)

The project owner records Phase 5 implementation and corrective work as
complete for local downstream development, with deterministic verification
PASS and formal V4 acceptance deferred, at candidate
`844afdeddd11a7cfa6fbed1d3eaefdf1e45ea07c`:

- `PHASE5_IMPLEMENTATION=COMPLETE_FOR_LOCAL_DOWNSTREAM_DEVELOPMENT`
- `PHASE5_DETERMINISTIC_VERIFICATION=PASS`
- `PHASE5_FORMAL_V4_ACCEPTANCE=DEFERRED`
- `PHASE5_V4_BLOCKER=SEALED_MANIFEST_CLI_FAILURE` (`V4_CLI_FAILURE`; no safe
  manifest aggregate was produced)
- `PHASE5_HELDOUT_STATE=AVAILABLE`
- `PHASE5_HELDOUT_EXECUTION_COUNT=0`
- `LOCAL_DOWNSTREAM_DEVELOPMENT=AUTHORIZED`
- `PHASE5_RELEASE_FREEZE=BLOCKED_UNTIL_V4_COMPLETES`

This disposition does not record Phase 5, V4, or held-out as PASSED, does not
authorize moving `phase-05-passed`, and does not grant production or release
authorization. Formal V4 acceptance remains deferred technical debt; preserve
the release freeze until V4 completes. Phase 5 Slice 7 / Slice 8 acceptance
remains blocked pending its applicable gates. The local downstream
authorization does not itself start downstream work or waive that work's scope
and gates.

## Phase evidence

| Scope | Status / evidence | Constraint |
| --- | --- | --- |
| Phase 0 | Acceptance unresolved; historical `phase-00-passed` at `55d2293` is a final-acceptance planning commit per retained roadmap record | No retroactive 0G/0H acceptance; preserve tag |
| Phase 1 | FROZEN: `phase-01-passed`, `8728aee00f4d6cddc8d1cc9b8f1d41a1c4684fef` | Historical accepted baseline |
| Phase 2 | FROZEN: `phase-02-passed`, `736c8498ab1fe2c1d4d425ba4625774eaf791e93` | Historical accepted baseline; freeze tag added at project-owner request on 2026-09-30 |
| Phase 3 | FROZEN: `phase-03-passed`, `12a27f7a77ed79a3e638d9fc6bf4509840a9b326` | No reopening |
| Phase 4 | FROZEN: `phase-04-passed`, `e99c494628952e0a1ebcafa2450c87611b4dc6c8` | No reopening or held-out rerun |
| Phase 5 | Implementation COMPLETE_FOR_LOCAL_DOWNSTREAM_DEVELOPMENT; deterministic verification PASS; formal V4 acceptance DEFERRED | `SEALED_MANIFEST_CLI_FAILURE`; held-out AVAILABLE (`execution_count=0`); local downstream development AUTHORIZED; Slice 7 / Slice 8 acceptance remains blocked; release freeze BLOCKED until V4 completes; not PASSED/FROZEN and no `phase-05-passed` move |
| Phase 6A | IMPLEMENTED / VERIFIED as the durable approval/execution prerequisite exercised by the frozen Phase 6B workflow; no separate freeze tag is recorded here | Preserve the approval/execution authority boundary; do not invent a standalone Phase 6A freeze |
| Phase 6B | FROZEN: `phase-06b-passed`, `40ff9d3ef6e0e3010982dabfb37cb597aceeb6ed` | Real Google Calendar acceptance reached `CONFIRMED_SUCCESS`; exactly one real CREATE attempt was performed; do not reopen without defect evidence |
| Phase 6C | OPTIONAL / NOT PURSUED | GitHub read-only remains outside the current scope; no GitHub write authority follows |
| Phase 7 | PASSED / FROZEN by whole-phase human acceptance; 7A–7G HUMAN_ACCEPTED; freeze tag `phase-07-passed` targets the final governance commit | Settings route and favicon carryovers are closed; no Phase 8 authority follows |
| Phase 8 | PASSED / FROZEN by whole-phase human acceptance; Tasks 0–10 HUMAN_ACCEPTED; freeze tag `phase-08-passed` targets the final governance commit `b07fa31dd95978e986269e4572fecb07d8b3e94a` | Restricted computation and multimodal vision are accepted within the recorded boundaries; RLM and Phase 9 remain out of scope |
| Phase 9 | PASSED / FROZEN by whole-phase human acceptance; Tasks 0–9 HUMAN_ACCEPTED; freeze tag `phase-09-passed` targets the final governance commit | Release qualification is accepted with the recorded non-blocking limitations; no Phase 10 or RLM authority follows |
| Optional Gemini cloud model (outside the phase sequence) | IMPLEMENTED; HUMAN_APPROVED by the owner on 2026-10-23 as built; VERIFIED on 2026-10-23 by a mock-transport test re-run (see "Verification re-run (2026-10-23)"). Merged to `main` on 2026-10-04 without a recorded authorization and recorded retroactively. Not verified against the live Gemini API | Off by default; touches frozen Phase 4 and Phase 7 code (see the 2026-10-23 record); no Phase 11 authority follows |
| Phase 10 | PASSED / FROZEN by whole-phase human acceptance on 2026-10-23; freeze tag `phase-10-passed` targets the acceptance commit `2dfb0fac46c0f1e341556d90759dc9ebf39a5ade` | Accepted with two KNOWN_LIMITATIONs: no recorded 10E real-history replay (still open), and no test re-run on the acceptance day (closed by the re-run later on 2026-10-23); Phase 0 and Phase 5 gates stay deferred; no Phase 11 authority follows |
| Phase 11 | PASSED / FROZEN by whole-phase human acceptance on 2026-10-23; freeze tag `phase-11-passed` targets the acceptance commit `6d49a0effb35046d412bcea8f94a1f93b6337fa1` (accepted boundary `b69e0f98`) | The local model stays the default; known limitations and the not-authorized list are recorded in "Phase 11 whole-phase acceptance (2026-10-23)"; Phase 0 and Phase 5 gates stay deferred; no Phase 12 authority follows |
| Phase 12 | PASSED / FROZEN by whole-phase human acceptance on 2026-10-24; freeze tag `phase-12-passed` targets the acceptance commit `398a03ad0cc962d63bb771ec06dd9d543a1cfa04` (accepted boundary `cd4d0e81`) | Calendar proposals stay off by default; known limitations and the not-authorized list are recorded in "Phase 12 whole-phase acceptance (2026-10-24)"; Phase 0 and Phase 5 gates stay deferred; no Phase 13 authority follows |
| Dashboard dark mode (outside the phase sequence) | IMPLEMENTED and VERIFIED on 2026-10-24 at the owner's request; awaiting the owner's review (not HUMAN_APPROVED) | Light stays the default look; touches frozen Phase 7 frontend code (see "Dashboard dark mode (2026-10-24)"); no backend change and no Phase 13 authority follows |

### Agentic AAOS A–H frozen evaluation baseline (2026-09-28)

- Status: FROZEN as a synthetic evaluation baseline at candidate commit 37a7b4b19637458556da140649d8100af2bc8e79.
- Frozen manifest: 100 cases; SHA-256 e1de0c65941633080f7bee400f7449f67a13725762e1959648e911ce025c4f49.
- Deterministic report SHA-256: a3c75a6b563ff1b9db147eb4ba525cdeab397cc347f172a0fd2f6760aed3ae11.
- Results artifact: docs/evaluations/agentic-aaos-ah-frozen-evaluation-v1-results-2026-09-28.md; SHA-256 370E4B49FBFE7C6921BCC9FE43EE6E42273220166032916A5F7CDB574D978BEE.
- Frozen selectors: 100/100 PASS. Frontend, backend, security boundary, report reproducibility, and independent exact-SHA reviews: PASS.
- Protected V4 evaluation material was not accessed or executed.
- This records evaluation evidence only. It does not change Phase 9 acceptance or authorize Phase 10 or RLM work.

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

### 2026-09-26 owner clarification — V4 held-out consumption

The project owner defines AVAILABLE, RESERVED, and CONSUMED as the canonical
V4 held-out ledger states. Consumption begins at the first protected semantic
evaluation operation. A reserved permit may return to available only when
trusted non-semantic audit metadata proves no protected semantic access,
execution, scoring, or case-specific result occurred. Once consumed, the split
is permanent and cannot be rerun; stale or corrupt state fails closed unless
the permitted pre-semantic release is proven. The complete transition and
recovery rules are normative in the
[C10 V4 evaluation contract](superpowers/specs/2026-09-10-phase-5-c10-v4-evaluation-contract.md#one-time-held-out-consumption-and-recovery).
This docs-only clarification ran no evaluation and records no new evaluation
result or execution-count assertion.

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

## Phase 10 authorization and standing notification approval (2026-10-02)

- **Authorization:** the owner authorized Phase 10, Moodle change notifications via Telegram. The open Phase 0 and Phase 5 gates are explicitly deferred, not resolved.
- **Spec:** `docs/superpowers/specs/2026-10-02-phase-10-moodle-change-notifications.md`, approved.
- **Standing approval (spec §7)** for Telegram sends that meet all of these conditions:
  - the destination is the single chat the owner links with `aaos notify link`;
  - the message is one of the fixed templates for the closed event set (`NEW_ANNOUNCEMENT`, `NEW_ASSIGNMENT`, `DEADLINE_CHANGED`, `NEW_COURSE`, `COURSE_ARCHIVED`, `AUTH_REQUIRED`), the digest, the test message or the link confirmation;
  - the content is limited to course names, item titles, dates and Moodle links built from the configured origin.
- **Revocation:** `aaos notify unlink` revokes the standing approval. Every other content or destination requires the normal approval path, and every send is recorded in `notification_deliveries`.
- **Owner decisions:**
  - checks run every 15 minutes, 07:00–24:00 Asia/Amman;
  - a visible Edge window per run is accepted, and the headless-browsing boundary is unchanged;
  - consent is given to send titles and dates through Telegram;
  - more than 5 events from one run are sent as one digest.

## Phase 10 addendum — remembered Microsoft sign-in (2026-10-03)

- **Approval:** the owner approved `docs/superpowers/specs/2026-10-03-phase-10-sso-continuation.md`. This narrows the Phase 2 rule "the user logs in themselves".
- **What AAOS may do:** follow Moodle's own same-origin "Log in with Microsoft" link once per run, so a Microsoft sign-in remembered by the browser profile completes.
- **What AAOS never does:** type into, click on, read or store anything on any sign-in page.
- **Waiting budget:** unattended runs end as `AUTH_REQUIRED` after 90 s; runs with the owner present keep 10 minutes.

## Phase 10 status note (2026-10-04)

- **State:** IMPLEMENTED / VERIFIED. The Telegram sender, the `notify` and `monitor` commands and the event pipeline are merged to `main`; `tests/test_notification_*.py` pass.
- **Not recorded:** whole-phase human acceptance and a `phase-10-passed` tag. Phase 10 is therefore not FROZEN.
- **Still open and unchanged:** Phase 0 acceptance (0G/0H) and Phase 5 Slice 6 remain deferred by the 2026-10-02 authorization. Quiz changes are not notified.

## Phase 10 whole-phase acceptance (2026-10-23)

- **Decision:** the owner accepted Phase 10 (Moodle change notifications via Telegram) on 2026-10-23. Status: PASSED / FROZEN.
- **Freeze tag:** at the owner's request, the annotated `phase-10-passed` tag targets `2dfb0fac46c0f1e341556d90759dc9ebf39a5ade`, the commit that recorded this acceptance. Unlike `phase-07-passed` to `phase-09-passed`, the tagged commit still reads "not FROZEN"; the FROZEN wording was added in the commit that follows it. The frozen baseline is the tagged commit.
- **Evidence relied on:** the 2026-10-04 status note (implementation merged to `main` in `472505ca`; `tests/test_notification_*.py` passing at that time). The owner chose to accept on that evidence.
- **KNOWN_LIMITATION: no 10E real-history replay is recorded.** Spec §10E calls for a replay of real snapshot history through a fake adapter to check correctness and completeness. The repository holds only synthetic pipeline tests (`tests/test_notification_pipeline.py`), and no replay evidence file. The acceptance-gate items "every notification matches a real change" and "every in-scope change is notified exactly once" are accepted by the owner without that replay.
- **KNOWN_LIMITATION: no test re-run on the acceptance day** (closed later on 2026-10-23; see "Verification re-run (2026-10-23)"). The project `.venv` was absent on 2026-10-23, so the notification tests and the full regression suite were not re-run. The optional Gemini provider and UI follow-ups were merged on 2026-10-04, before the status note's commit; the ledger does not record whether the note's passing tests ran before or after that merge, and this entry does not re-verify them against Phase 10.
- **Unchanged:** Phase 0 acceptance (0G/0H) and Phase 5 Slice 6 remain deferred, not resolved. The standing Telegram approval from 2026-10-02 stays as recorded.
- **No new authority:** this acceptance authorizes no Phase 11 or other work. The ChatGPT-plan gateway note (`docs/superpowers/specs/2026-10-23-chatgpt-plan-model-gateway-design.md`) remains a proposal.

## Optional Gemini cloud model — retroactive record (2026-10-23)

This records work that reached `main` outside the phase sequence. The record
itself is not an approval or a verification; the owner's approval is recorded
separately at the end of this section.

- **What exists:** an opt-in `GeminiGateway` behind the existing `ModelGateway`
  boundary, so a Google Gemini model can answer instead of the local LM Studio
  model. The local model stays the default.
  - It is enabled by `AAOS_MODEL_PROVIDER=gemini`, `AAOS_GEMINI_MODEL` and
    `GEMINI_API_KEY`, or by switching in the model panel when the last two are set.
  - Code: `src/aaos/inference/gemini.py`, `factory.py`, `selection.py`,
    `src/aaos/config.py`, `src/aaos/main.py`, `src/aaos/rag/factory.py`,
    `src/aaos/rag/semantic_coverage.py`, `src/aaos/web/contracts.py`,
    `src/aaos/web/routes.py`, the model badge and model panel in the frontend,
    and `scripts/gemini_probe.py`.
- **History:** 12 commits dated 2026-10-04. Ten are code commits, from
  `e2bbb794` (the opt-in gateway) to `0e1d6749` (choosing the model from a list).
  Two change README and demo wording: `ac99c401` and `96b33040`. They came through the `feat/gemini-model-option`,
  `feat/model-switch-ui` and `feat/followups` branches, and `main` received them
  in merges `27915548` and `59097e23`.
- **Authorization: none recorded.** No PHASES.md entry, spec or ledger decision
  exists for this work. It was merged while Phase 10 acceptance was pending, and
  it is outside Phase 10's scope.
- **Frozen phases it touches.** These are recorded here, not accepted
  retroactively:
  - **Phase 4 (local RAG):** in `85e75bc2`, the three semantic steps
    (decomposition, evidence spans, requirement coverage) gained an
    `approved_identifiers` set, so replies named after the active Gemini model
    are accepted. The Qwen-only default and refusal of any other name are kept,
    per `tests/test_semantic_model_identity.py`. Embeddings and the NLI claim
    checker still run locally.
  - **Phase 7 (dashboard):** new browser routes `GET /model`,
    `GET /model/gemini-models` and `POST /model`, with the
    `tests/test_phase7d_scope_audit.py` and `tests/test_phase7g_route_inventory.py`
    expectations updated to include them.
  - The `phase-04-passed` and `phase-07-passed` tags still mark the earlier baselines.
- **Boundaries in the code (read on 2026-10-23):**
  - The endpoint is a fixed HTTPS constant, with no redirects or proxies.
  - The key is read from the environment only. It is held in one private
    attribute and appears in no telemetry, `repr` or error message.
  - Browser contracts carry names only (`ModelStatusResponse`,
    `ModelSelectRequest`), and the browser never sends a key.
  - Moving to the cloud model needs `confirm_cloud`, and a switch is refused
    while a question is running.
  - The backend logs `CLOUD MODEL ACTIVE`.
  - No tools are sent to the model.
  - AAOS still validates every reply with its own strict parsers.
- **Data boundary:** with Gemini selected, question text and the selected course
  excerpts go to Google. The README's local-first claim now says "by default" and
  lists Gemini among the outside calls.
- **Test evidence (not re-run 2026-10-23, `.venv` absent):**
  - `tests/test_gemini_gateway.py`, `tests/test_gemini_model_picker.py`,
    `tests/test_gemini_probe.py`, `tests/test_model_selection.py`,
    `tests/test_semantic_model_identity.py` and `frontend/tests/model-badge.test.tsx`
    use mock transports only.
  - Commit `e2bbb794` states "Not verified against the live Gemini API". Later
    commit messages describe live use by the owner (for example the AI Studio
    usage chart in `85e75bc2`), but no live evidence artifact is recorded.
- **Status:** IMPLEMENTED; HUMAN_APPROVED (see the owner approval below);
  VERIFIED by the re-run recorded in "Verification re-run (2026-10-23)".
- **Consequence for Phase 11:** the proposed ChatGPT-plan gateway builds on this
  provider-selection path, so a Phase 11 authorization should name this record
  and decide whether Gemini's status must first reach VERIFIED.

### Owner approval of the Gemini provider (2026-10-23)

- **Decision:** the owner approved the optional Gemini provider as built:
  "I approve the optional Gemini provider as built, including its changes to the
  Phase 4 semantic steps and the Phase 7 /model routes."
- **Scope of the approval:** the code on `main` at commit `f031a67d` that this
  record describes. That includes the gateway and its selection path, the Phase 4
  `approved_identifiers` change in the semantic steps, and the Phase 7 routes
  `GET /model`, `GET /model/gemini-models` and `POST /model`.
- **Effect on the frozen phases:** those Phase 4 and Phase 7 changes are now
  owner-approved amendments. The `phase-04-passed` and `phase-07-passed` tags
  still mark the original baselines and are not moved. The amended code is
  covered by this approval, not by those phases' acceptance evidence.
- **Status:** IMPLEMENTED; HUMAN_APPROVED. **Not VERIFIED.** The owner set the
  condition that verification needs a recorded re-run of the listed tests (the
  `.venv` was absent on 2026-10-23).
- **Approval does not extend to:**
  - other cloud providers or changes to the local-first default;
  - Phase 11 or the ChatGPT-plan gateway, which remain unauthorized.

## Verification re-run (2026-10-23)

- **Purpose:** to meet the owner's condition for marking the Gemini provider
  VERIFIED, and to close the Phase 10 "no test re-run" limitation.
- **Tested commit:** `85f341d5`, on `main` with a clean tracked tree.
- **Environment:** the `.venv` was rebuilt with `uv sync --frozen` after
  `uv lock --check` passed (198 packages), giving Python 3.11.9 and
  torch 2.13.0+cu130.
- **Commands:**
  - `.venv\Scripts\python.exe -m pytest -p no:cacheprovider -o addopts="" -q`
    for the targeted files and for the full suite (`-rfEs`);
  - `npx vitest run tests/model-badge.test.tsx tests/ask-view.test.tsx` in `frontend/`.
- **Results:**

| Scope | Files | Result |
| --- | --- | --- |
| Gemini, backend | `test_gemini_gateway.py`, `test_gemini_model_picker.py`, `test_gemini_probe.py`, `test_model_selection.py`, `test_semantic_model_identity.py` | 104 passed |
| Phase 10 notifications | `test_notification_events.py`, `_messages.py`, `_monitor.py`, `_pipeline.py`, `_telegram.py` | 62 passed |
| Gemini, frontend | `model-badge.test.tsx`, `ask-view.test.tsx` | 49 passed |
| Full backend suite | `tests/` | 4,396 passed, 1 failed, 6 skipped, 1 warning (7 m 46 s) |
| Phase 8 sandbox, re-run with Docker running | `tests/phase8/test_sandbox.py` | 7 passed, including the earlier failure |

- **The one failure is environmental:**
  `tests/phase8/test_sandbox.py::test_real_docker_supervisor_path_uses_qualified_runtime`
  needs a running Docker engine, and the probe returned `DOCKER_ENGINE_UNAVAILABLE`.
  - `docker info` on the `desktop-linux` context could not reach the
    `dockerDesktopLinuxEngine` pipe, because Docker Desktop was not running.
  - The test and `scripts/phase8/qualify_isolation_runtime.py` are unchanged
    since Phase 8 apart from a formatting-only commit (`93f964d5`).
  - It is not a Gemini or Phase 10 regression.
  - **Confirmed by a re-run:** with Docker Desktop started by the owner (engine
    29.5.3, linux), `tests/phase8/test_sandbox.py` ran at `3be31e49` with 7 passed,
    including this test. `3be31e49` differs from `85f341d5` only in ledger and
    PHASES.md text.
  - Taken together, all 4,397 non-skipped backend tests passed. The full suite
    was not repeated as one run.
- **Skips:** the 6 opt-in live and acceptance gates (LM Studio, Moodle browser,
  Phase 3 ingestion, two Phase 4 acceptance gates, and the Phase 8 real
  qualification probe), each skipped by its own environment flag.
- **Warning:** the chromadb `DeprecationWarning` for a legacy embedding-function
  config in `tests/test_rag_chroma.py`.
- **Not run:**
  - ruff, mypy, and the rest of the frontend suite;
  - any live Gemini or Telegram call;
  - the Phase 10 §10E real-history replay, which stays a KNOWN_LIMITATION.
- **Status changes:**
  - **Gemini provider:** VERIFIED, by mock-transport tests only. It is not
    verified against the live Gemini API.
  - **Phase 10:** the "no test re-run" limitation is closed. Phase 10 stays
    PASSED / FROZEN at `phase-10-passed`; the tag is not moved.

## Phase 11 authorization (2026-10-23)

- **Authorization:** the owner authorized Phase 11, an optional ChatGPT-plan
  cloud model through OpenAI's Sign in with ChatGPT plan usage, on 2026-10-23.
- **Basis:** the design note
  `docs/superpowers/specs/2026-10-23-chatgpt-plan-model-gateway-design.md`
  (commit `0b896d0a`). Phase 10 is PASSED / FROZEN at `phase-10-passed`, and the
  Gemini provider it builds on is IMPLEMENTED, HUMAN_APPROVED and VERIFIED (see
  the 2026-10-23 records above).
- **Deferred gates:** the open Phase 0 (0G/0H) and Phase 5 (Slice 6) gates stay
  deferred, not resolved, as in the Phase 10 authorization.
- **Authorized now:**
  - writing the Phase 11 spec and plan from the design note;
  - one owner-present probe. It covers a single Sign in with ChatGPT sign-in and
    a few requests in the formats AAOS uses, to settle the design note's §10
    open questions. With the owner's consent at that time, only short synthetic
    prompts are sent to OpenAI. No course material is sent, and no credential
    is written to Git, logs or model context.
- **Authorized only after the owner approves the spec:**
  - implementation of the sign-in flow;
  - the credential store;
  - `ChatGPTPlanGateway`;
  - the `chatgpt_plan` selection path.
- **Not authorized:**
  - the Codex CLI, `codex exec` or reuse of the Codex CLI's login;
  - model-side tools;
  - hosted or multi-user use;
  - changing the local model as the default;
  - any other provider.
- **Acceptance gate:** as listed under Phase 11 in `docs/PHASES.md`.

## Phase 11 spec approval (2026-10-23)

- **Approval:** the owner approved
  `docs/superpowers/specs/2026-10-23-phase-11-chatgpt-plan-model.md` (the draft
  in commit `5826ef3e`), in these words: "I approve the Phase 11 spec with the
  recommended defaults for D1–D7."
- **Owner decisions:** all seven recommended defaults (spec §2).
  - **D1:** sign-in from the CLI only.
  - **D2:** credentials at `%LOCALAPPDATA%\AAOS\credentials\chatgpt-plan.json`,
    with any in-repository path refused.
  - **D3:** `PyJWT[crypto]`, pinned, as the only new dependency.
  - **D4:** the probe runs after 11A–11B and before 11D.
  - **D5:** switching between cloud providers needs confirmation again.
  - **D6:** no default model.
  - **D7:** no AAOS-side usage cap; the owner sets a weekly cap at
    chatgpt.com/settings/usage.
- **Effect:** under the Phase 11 authorization, implementation of slices 11A–11F
  may now begin.
  - **Not started:** the owner asked that coding not start yet. Each start is
    the owner's call.
  - **Probe:** the owner-present probe (11C) still needs the owner present, and
    their consent at that time.
  - **Evaluation:** the §10 evaluation sends real course excerpts to OpenAI, so
    it also needs the owner's consent at the time of the run.
- **Unchanged:** the Phase 0 and Phase 5 gates stay deferred. The not-authorized
  list in the Phase 11 authorization still applies.

## Phase 11 implementation status (2026-10-23)

- **Instruction:** the owner asked to "finish all phase 11". This entry records
  how far that could go without the owner present.
- **Implemented on `feat/phase-11-chatgpt-plan`:**
  - **11A:** configuration (`chatgpt_plan` provider, `AAOS_CHATGPT_PLAN_MODEL`) and
    the owner-only credential store (`src/aaos/inference/chatgpt_plan_credentials.py`).
  - **11B:** the CLI-only sign-in (`chatgpt_plan_signin.py`, `chatgpt_plan_cli.py`,
    `aaos model chatgpt-plan sign-in | status | sign-out`).
  - **11C:** the probe script `scripts/chatgpt_plan_probe.py`. It has not been run.
  - **11D:** `ChatGPTPlanGateway` (`chatgpt_plan.py`) and the RAG wiring.
  - **11E:** the selection rules (including D5), additive contracts, the route
    `GET /model/chatgpt-plan-models`, the model panel, and the regenerated
    OpenAPI file and types.
  - **Release scan:** `scripts/phase9/public_release.py` also flags stored tokens
    and bare JWTs.
  - **Dependency:** `pyjwt[crypto]==2.15.1` (D3).
- **Order:** 11D was built before the probe, against D4's order, because the owner
  asked for all of Phase 11. The gateway falls back step by step, and spec §14
  records this.
- **Verification on 2026-10-23 (Docker running):**
  - full backend suite: 4,492 passed, 6 skipped, 1 warning, 0 failed (95 new tests);
  - frontend suite: 51 files, 288 tests passed;
  - `tsc --noEmit` clean; ESLint has 0 errors and the 2 earlier warnings;
  - ruff check and format clean on the changed files; mypy clean on the changed
    modules;
  - a browser check of the model panel against the real backend: the ChatGPT plan
    option shows the terminal sign-in note, with no console errors.
  - All automated tests use fake transports. No real OpenAI call was made.
- **Status:** IMPLEMENTED / VERIFIED by automated tests. **Not accepted.** These
  need the owner present:
  1. the first real sign-in (`aaos model chatgpt-plan sign-in`);
  2. the 11C probe, with its findings recorded in spec §13;
  3. the §10 evaluation: at least three runs of the Phase I4 answer evaluation.
     It sends real course excerpts to OpenAI, so it needs consent at the time.
  4. whole-phase human acceptance.

## Phase 11 probe (2026-10-23)

- **Sign-in:** the owner completed the first real sign-in (`aaos model chatgpt-plan
  sign-in`, including OpenAI's own authenticator-app check). `status` reported
  `SIGNED_IN`, and the credential file was confirmed owner-only. No token was read
  into this record.
- **Probe:** `scripts/chatgpt_plan_probe.py` ran three times with the owner present,
  sending only synthetic prompts. Spec §13 has the findings:
  - seven models offered; `gpt-6.1-sol` probed;
  - developer messages and `instructions` accepted;
  - `reasoning.effort` `none` and `minimal` rejected, `low` accepted;
  - all six strict AAOS JSON schemas accepted unchanged.
- **Defects found and fixed:**
  1. The gateway's fallback dropped the JSON schema when OpenAI had rejected
     `reasoning.effort`. Every structured call lost its strict format and made
     two extra requests. The fallback now drops only the field OpenAI's
     `error.param` names, and remembers it.
  2. Stray `httpcore2` tracebacks appeared because streams were not read to
     their end. They now are, and the third run printed 0 tracebacks.
- **Verification after the fixes:**
  - full backend suite: 4,494 passed, 6 skipped, 0 failed;
  - the gateway and probe tests: 25 passed;
  - the third probe run: all six gateway shapes accepted with their schema, and
    one rejected attempt in all, for the first request only.
- **Status:** Phase 11 stays IMPLEMENTED / VERIFIED, now with 11C done. Still
  needed: the §10 evaluation, which sends real course excerpts to OpenAI and
  needs the owner's consent at the time, and whole-phase human acceptance.

## Phase 11 §10 evaluation (2026-10-23)

- **Consent:** the owner consented to sending the evaluation's course excerpts to
  OpenAI ("I consent to sending those course excerpts to OpenAI for it").
- **Prerequisite fixed:** the local embedding model BGE-M3 was missing from the
  Hugging Face cache, so every document question failed for every provider. With
  the owner's approval it was re-downloaded at the pinned revision `89f985cb…`
  (only AAOS's listed files, 2.2 GB) and confirmed to load offline.
- **Run:** `gpt-6.1-sol`, `main` at `0d829d3a`. The frozen Phase I4 manifest was run
  with `refusal_trace.py`'s procedure: 3 runs, 96 answerable and 8 unanswerable
  rows. The raw trace is outside the repository (SHA-256 `5ea9b91e…0db2`).
- **Report:** `docs/evaluations/phase11-chatgpt-plan-evaluation-2026-10-23.md`.
- **Results:**
  - **86/96** answerable rows cite an expected page, against the local model's
    78/96 (81/96 adjusted) from the 2026-10-02 gate;
  - unanswerable rows: **8/8 refused, 0 cited**;
  - 0 errors;
  - median 21.1 s per question;
  - one rejected request in the whole run, the expected reasoning-effort step-down.
- **Where answers were lost:**
  - `project-weights` AR was lost at retrieval, as with the local model;
  - 4 flips, 3 of them at the local claim check (S4).
- **Status:** the evaluation required by spec §10 is recorded. Phase 11 is
  IMPLEMENTED / VERIFIED, with every owner-present step done. **Human acceptance
  remains**, and the default model stays local.

## Phase 11 whole-phase acceptance (2026-10-23)

- **Decision:** the owner accepted Phase 11, the optional ChatGPT-plan cloud
  model through Sign in with ChatGPT, on 2026-10-23: "I accept Phase 11."
  Status: PASSED / FROZEN.
- **Freeze tag:** at the owner's request, the annotated `phase-11-passed` tag
  targets `6d49a0effb35046d412bcea8f94a1f93b6337fa1`, the commit that recorded this acceptance. As with
  `phase-10-passed`, the tagged commit still reads "not FROZEN"; the FROZEN
  wording was added in the commit that follows it. The frozen baseline is the
  tagged commit.
- **Accepted boundary:** `main` at `b69e0f98`. That includes slices 11A–11E,
  the probe-driven gateway fix (`0d829d3a`), and the §10 evaluation report.
- **Evidence relied on:**
  - automated: 4,494 backend tests, 288 frontend tests, ruff, mypy and `tsc`;
  - the first real sign-in, by the owner;
  - three owner-present probe runs (spec §13);
  - the §10 evaluation: 86/96 answerable rows with an expected page, against the
    local model's 78/96, and unanswerable rows 8/8 refused with 0 cited.
- **Default unchanged:** the local model (`lm_studio`) stays the default. The
  ChatGPT plan answers only after the owner signs in, selects it and confirms
  the cloud warning.
- **KNOWN_LIMITATION:**
  - the evaluation covers one model (`gpt-6.1-sol`), one day and three runs;
  - its local comparison is the 2026-10-02 gate, not a fresh side-by-side run;
  - answers that cite another page were not read by hand;
  - behaviour at the plan limit is verified with fake transports only;
  - the preview ignores `temperature`, so answers can vary between runs.
- **Unchanged:** the Phase 0 (0G/0H) and Phase 5 (Slice 6) gates stay deferred,
  not resolved. The Phase 11 not-authorized list still applies: no Codex CLI or
  Codex login, no model-side tools, no hosted or multi-user use, no change of
  default model and no other provider.
- **No new authority:** this acceptance authorizes no Phase 12 or other work.

## Phase 12 authorization (2026-10-23)

- **Authorization:** the owner authorized Phase 12, everyday Calendar proposals
  and approvals, on 2026-10-23: "I authorize Phase 12 (everyday Calendar
  proposals and approvals) based on the design note."
- **Basis:** the design note `docs/superpowers/specs/2026-10-23-everyday-calendar-approvals-design.md` (commit `48ce6d2d`). Phase 11 is PASSED /
  FROZEN at `phase-11-passed`.
- **Deferred gates:** the open Phase 0 (0G/0H) and Phase 5 (Slice 6) gates stay
  deferred, not resolved, at the owner's instruction.
- **Relation to frozen phases:** Phase 12 composes the frozen Phase 6A/6B
  approval and Calendar parts and the Phase 7E approval routes and cards. It
  does not reopen them. Phase 6B's single real `CREATE` remains evidence, not
  standing authority. Any change to a frozen contract needs its own recorded
  amendment.
- **Authorized now:** writing the Phase 12 spec and plan from the design note.
  Coding has not started, at the owner's instruction.
- **Authorized only after the owner approves the spec:** implementation, and the
  owner-present real checks in the Phase 12 gate (one approved and one rejected
  proposal on the dedicated calendar).
- **Not authorized:**
  - standing or automatic approval;
  - update, delete, invite or attendee actions;
  - writes to the owner's primary calendar;
  - OAuth user sign-in;
  - remote or multi-user use;
  - any other external write.
- **Acceptance gate:** as listed under Phase 12 in `docs/PHASES.md`.

## Phase 12 spec approval (2026-10-23)

- **Approval:** the owner approved `docs/superpowers/specs/2026-10-23-phase-12-everyday-calendar-approvals.md` (the draft in commit
  `f5837c67`), in these words: "I approve the Phase 12 spec with the recommended
  defaults for D1–D6."
- **Owner decisions:** all six recommended defaults (spec §2).
  - **D1:** a proposal can wait 24 hours for approval, and the action is valid
    for 25 hours. These are parameters; the frozen 5- and 10-minute constructor
    defaults are not changed.
  - **D2:** the existing 6B dedicated calendar, once confirmed in spec §11.
  - **D3:** approver identity `owner`, context `aaos-laptop-1`.
  - **D4:** a partial configuration starts the backend with proposals off, a
    warning, and `MISCONFIGURED` in System health.
  - **D5:** no Telegram announcement of pending proposals.
  - **D6:** proposals from Ask AAOS only.
- **Open item:** the read-only pre-check confirmed the 6B service account is
  the same account and that its key still works. The dedicated calendar is not
  yet confirmed. The owner can confirm it with the read-only check described in
  spec §11. If it can't be confirmed, D2 falls back to a new dedicated calendar,
  recorded here before 12D.
- **Effect:**
  - Under the Phase 12 authorization, slices 12A–12C may now be implemented. The
    owner asked that coding not start yet, so each start is the owner's call.
  - The 12D real check (one approved and one rejected proposal) needs the owner
    present.
- **Unchanged:** the Phase 0 and Phase 5 gates stay deferred. The Phase 12
  not-authorized list still applies.

## Phase 12 calendar finding (2026-10-23)

- **Finding:** the owner gave the calendar ID of the calendar Phase 6B wrote to.
  Its hash matches the 6B record (`cal-e22174a2…`). It is the owner's **primary**
  calendar.
- **About Phase 6B:** the frozen run's single real `CREATE` went to the primary
  calendar, although the 6B plan described a dedicated calendar. This is
  recorded as a fact about the frozen evidence. Phase 6B is not reopened: the
  write was approved, and it is recorded as `CONFIRMED_SUCCESS`.
- **D2 falls back** to a new dedicated calendar, as the Phase 12 spec approval
  provides. Phase 12 never writes to the primary calendar (spec invariant 3).
  The owner creates the new calendar and shares it with the same service
  account ("Make changes to events"). Its ID is confirmed by the read-only
  check before 12D.
- **Recommended to the owner:** remove the service account's access to the
  primary calendar once the dedicated one is shared.

## Phase 12 dedicated calendar confirmed (2026-10-23)

- **Calendar:** the owner created a dedicated calendar named `AAOS` and shared it
  with the 6B service account ("Make changes to events").
- **Read-only check** (`calendar.readonly`, GET only):
  - it is a group (non-primary) calendar;
  - the service account can read it (summary `AAOS`, time zone `Asia/Amman`);
  - write permission is confirmed in 12D.
- **D2 is settled:** Phase 12 uses this calendar. Its ID is kept in the owner's
  environment, not in this ledger.
- **Open recommendation:** the service account can still read the owner's
  primary calendar. The owner should remove it from the primary calendar's
  sharing, and check the primary calendar is not public. Phase 12 refuses the
  primary calendar by configuration either way.

## Phase 12 implementation status (2026-10-23)

- **Built on `feat/phase-12-calendar-approvals`:**
  - **12A:** `AAOS_LOCAL_APPROVER_*` read into the settings, failing closed;
    `src/aaos/integrations/google_calendar/setup.py` decides OFF, ON or
    MISCONFIGURED.
  - **12B:** `composition.py`:
    - the `CREATE_EVENT` definition, with fingerprints computed from its contracts;
    - the 24 h / 25 h lifetimes (D1);
    - the factory-built client, with no fake fallback;
    - the served app's wiring in `main.py`.
  - **12C:** System health states; the Approvals page deadline; card wording.
- **Defect found and fixed:**
  - The frozen 7E task service and the frozen 6B adapter disagreed on the remote
    precondition version, so every approved action was refused unwritten.
  - It was fixed by building the adapter with
    `derivation_version = "calendar-adapter-v1"`. No frozen code changed.
  - Spec §12 has the root cause, and a regression test pins the two values.
- **Contract amendment, recorded as D4 requires:** two additive Phase 7 health
  detail codes, `CALENDAR_PROPOSALS_READY` and `CALENDAR_PROPOSALS_MISCONFIGURED`.
  The OpenAPI file and the frontend types are regenerated.
- **Verification (fake Google client only; no real Calendar write):**
  - full backend suite: 4,534 passed, 6 skipped, 0 failed, with 40 new Phase 12
    tests;
  - frontend: 51 files, 290 tests passed;
  - `tsc` clean; ESLint 0 errors and the 2 earlier warnings;
  - ruff and mypy clean on the changed files.
- **Status:** IMPLEMENTED / VERIFIED by automated tests. **Not accepted.** Still
  needed:
  - the 12D real check with the owner present: one proposal approved into the
    dedicated `AAOS` calendar, and one rejected;
  - whole-phase human acceptance.

## Phase 11 defect fix after freeze (2026-10-23)

- **Approval:** the owner approved a recorded defect fix for frozen Phase 11:
  "make the ChatGPT-plan gateway wrap union (oneOf) schemas in an object and unwrap
  the reply, with tests and a re-run of the schema sweep."
- **Defect:** OpenAI rejected all 17 AAOS agent decision schemas. 15 have a
  top-level `oneOf`, which needs an object root, and 2 failed only in strict mode.
  So agent features on the ChatGPT plan ran without structured output and often
  failed (`INVALID_PROPOSAL`). The Phase 11 probe and evaluation covered only the
  document-answering schemas.
- **Fix:** in `src/aaos/inference/chatgpt_plan.py` only:
  - `oneOf` becomes `anyOf`;
  - non-object roots are wrapped as `{"decision": …}`, with the reply unwrapped;
  - a schema steps from strict to not strict before being dropped.
  - The `_sse_events` type annotation was also corrected (`AsyncGenerator`); it
    fixes two mypy errors left by `0d829d3a`, with no behaviour change.
- **Evidence:**
  - a schema check on `gpt-6-luna` and `gpt-6.1-sol`: 24 of 27 strict and 27 of 27
    not strict accepted (before: 10 of 27);
  - 4 new gateway tests;
  - full backend suite: 4,538 passed, 6 skipped, 0 failed;
  - ruff and mypy clean;
  - a reproduction on a database copy with the fake Google client: the agent's
    decision is now accepted.
- **Freeze:** the `phase-11-passed` tag is not moved. This record and spec §15 are
  the amendment.
- **Second defect found, not fixed (awaiting owner decision):**
  - `_parse_local_start` in `src/aaos/agent/calendar_actions.py` (frozen agentic
    Calendar code, `c4bc8848`) keeps the last of two identical fold candidates, so
    an ordinary time such as 18:00 gets `fold=1`.
  - The Calendar canonicalizer then correctly refuses it ("start fold is invalid
    for declared timezone"). Every study-task Calendar proposal fails at
    preparation (`INVALID_REQUEST`), with any model.
  - The existing agent tests use a fake proposal service that never runs the real
    canonicalizer.
  - Proposed fix: keep the first candidate per instant (one line).

## Agentic Calendar defect fix after freeze (2026-10-23)

- **Approval:** the owner approved a recorded defect fix for the frozen agentic
  Calendar code: "in _parse_local_start keep the first fold candidate per instant,
  with a regression test that runs the real canonicalizer."
- **Defect:**
  - `_parse_local_start` in `src/aaos/agent/calendar_actions.py` (`c4bc8848`)
    tried fold 0 and 1 and kept the **last** candidate per UTC instant.
  - So an ordinary time such as 18:00 got `fold=1`, and the Calendar
    canonicalizer refused it ("start fold is invalid for declared timezone").
  - Every study-task Calendar proposal failed at preparation (`INVALID_REQUEST`),
    with any model.
  - The earlier agent tests used a fake proposal service that never ran the
    canonicalizer.
- **Fix:** `candidates.setdefault(...)` keeps the first candidate (fold 0) per
  instant. Repeated and skipped wall times are still refused.
- **Evidence:**
  - `tests/test_calendar_time_fold.py` (6 tests: ordinary times in Asia/Amman,
    Europe/Berlin and UTC through the real canonicalizer; repeated and skipped
    times still refused);
  - the Calendar agent and Phase 12 flow tests: 32 passed;
  - full backend suite: 4,544 passed, 6 skipped, 0 failed;
  - ruff and mypy clean;
  - an end-to-end reproduction on a database copy with the fake Google client,
    on `gpt-6-luna`: `CALENDAR_PROPOSAL_CREATED`, pending approval, with 0 Google
    writes.
- **Frozen evidence:** no freeze tag is moved. The A–H frozen evaluation used fake
  providers that never ran the canonicalizer, so its recorded results are
  unaffected.

## Phase 12 real check 12D (2026-10-23/24)

- **Setup:**
  - the owner ran the served app from `main` at `2d1cc832`, with the Phase 12
    settings (`CALENDAR PROPOSALS ACTIVE` logged);
  - the answering model was the ChatGPT plan;
  - the two study tasks were created earlier in Ask AAOS.
- **Approved:** "Put my study task Review k-fold cross-validation on my calendar
  tomorrow at 6 pm", approved by the owner.
  - Records: approval `CONSUMED` (issued 20:59:14Z, approve-by 24 h later,
    decided 20:59:21Z); operation `CONFIRMED_SUCCESS`; lifecycle
    `CONFIRMED_SUCCESS`.
- **Rejected:** "Put my study task Practice KNIME workflows on my calendar tomorrow
  at 8 pm", rejected by the owner.
  - Records: approval `REJECTED`; no operation record; lifecycle `REJECTED`.
- **Read-only Google check** (`calendar.readonly`, GET only) of 24–25 October:
  - the dedicated AAOS calendar has **exactly one** event, "Study session: Review
    k-fold cross-validation", 2026-10-24 18:00–19:00 Asia/Amman;
  - no KNIME event exists;
  - neither event is in the owner's primary calendar.
- **Target:** both actions are bound to the hashed target
  `cal-0b6331ca92c7b66664d045b7`, which hashes from the AAOS calendar ID, not the
  primary calendar (`cal-e22174a2…`).
- **Gate items met:**
  - one write, and only after a recorded human approval;
  - a rejection writes nothing;
  - `CREATE_EVENT` only, to the dedicated calendar only;
  - the approval window is 24 h (D1);
  - no calendar ID, key or approver value in this record (hashes only).
- **Status:** Phase 12 is IMPLEMENTED / VERIFIED, with 12D done. **Whole-phase
  human acceptance remains.**

## Phase 12 whole-phase acceptance (2026-10-24)

- **Decision:** the owner accepted Phase 12, everyday Calendar proposals and
  approvals: "I accept Phase 12." Status: PASSED / FROZEN.
- **Freeze tag:** at the owner's request, the annotated `phase-12-passed` tag
  targets `398a03ad0cc962d63bb771ec06dd9d543a1cfa04`, the commit that recorded this acceptance. As with
  `phase-10-passed` and `phase-11-passed`, the tagged commit still reads "not
  FROZEN"; the FROZEN wording was added in the commit that follows it. The
  frozen baseline is the tagged commit.
- **Accepted boundary:** `main` at `cd4d0e81`. That includes:
  - slices 12A–12C (`32adc88f`);
  - the Phase 11 union-schema defect fix (`90244593`);
  - the agentic Calendar fold defect fix (`2c03b71c`);
  - the 12D record.
- **Evidence relied on:**
  - automated: 4,544 backend tests, 290 frontend tests, ruff, mypy and `tsc`;
  - the 12D real check: one approved proposal written once to the dedicated
    `AAOS` calendar (`CONFIRMED_SUCCESS`), and one rejected proposal with nothing
    written, both confirmed by a read-only Google check.
- **Default unchanged:** Calendar proposals stay off. They switch on only with
  `AAOS_CALENDAR_PROPOSALS=on` and a complete, valid configuration, and every
  write still needs its own approval.
- **KNOWN_LIMITATION:**
  - the real check covered one approval and one rejection, on the ChatGPT plan
    only;
  - a lost Google reply (`UNKNOWN_OUTCOME`) is verified with fake clients only;
  - the proposal card does not show the approval deadline, because its frozen
    contract has no field for it; the Approvals page does;
  - the service account can still read the owner's primary calendar (removal
    recommended);
  - the read-only pre-check script is not yet an `aaos doctor` check.
- **Unchanged:** the Phase 0 (0G/0H) and Phase 5 (Slice 6) gates stay deferred.
  The Phase 12 not-authorized list still applies: no standing or automatic
  approval, no update/delete/invite actions, no primary calendar, no OAuth user
  sign-in, no remote use and no other external write.
- **No new authority:** this acceptance authorizes no Phase 13 or other work.

## Dashboard dark mode (2026-10-24)

The owner asked for dark mode on the dashboard on 2026-10-24. This is a frontend-only
change outside the phase sequence. It touches frozen Phase 7 code, which the Phase 7
spec allows for (§13.6: dark mode was "not a Phase 7 blocking requirement" and the
tokens were to keep it possible).

What changed:

- `frontend/app/globals.css`: a `.dark` block redefines every `--aaos-*` colour token;
  new tokens for the status text and danger colours and the hover border
  (`success-strong`, `warning-strong`, `danger-soft`, `danger-strong`, `border-hover`)
  replace hex values that were hard-coded in components.
- `frontend/app/layout.tsx` and `frontend/lib/theme.ts`: an inline script in `<head>`
  applies the saved choice (or the OS setting) before first paint, so there is no flash
  of the wrong theme. The choice is stored in the browser's `localStorage` under
  `aaos-theme` (`light`, `dark` or `system`; default `system`). Nothing is sent to the
  backend.
- `frontend/components/aaos/theme-toggle.tsx`: a button in the top bar cycles
  Light, Dark, System.
- 16 components: `bg-white` became `bg-card` and hard-coded hex colours became tokens.
  Light mode looks the same as before.
- Tests: the Phase 7 test "keeps Phase 7 light-only" was replaced by two tests: light
  stays the default, and every colour token has a dark value. 8 new toggle tests.

Verification on 2026-10-24: frontend suite 299 passed (290 before, minus the replaced
test, plus 10 new); `tsc` clean; ESLint has only the 2 earlier warnings. In the built-in
browser against the owner's running dev server: Overview, System health and Ask render
correctly in dark; a Light choice survives a page reload while the OS is dark; no
console errors or hydration warnings.

Status: IMPLEMENTED and VERIFIED. Not HUMAN_APPROVED until the owner reviews it.
