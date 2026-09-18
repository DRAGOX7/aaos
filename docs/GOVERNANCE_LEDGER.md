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
| Phase 7 | AUTHORIZED / IMPLEMENTATION IN PROGRESS; 7A HUMAN_ACCEPTED at `61313d0`; 7B HUMAN_ACCEPTED at `0f887a2f867648f95e69abe534978a1482f69df1`; 7C HUMAN_ACCEPTED at `26e517bb8967527de435ffba11a77ff1c00000a0`; 7D HUMAN_ACCEPTED at `ebae43800f6600ef61c07c355c9ad085b1f5f483` | 7E remains NOT_STARTED; Phase 7 is not PASSED/FROZEN |
| Phases 8–9 | PLANNED, NOT AUTHORIZED / NOT STARTED | No implementation authority |

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
