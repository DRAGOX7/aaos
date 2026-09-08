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
| Phases 6–9 | PLANNED, NOT AUTHORIZED / NOT STARTED | Phase 6 cannot start before Phase 5 passes |

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
