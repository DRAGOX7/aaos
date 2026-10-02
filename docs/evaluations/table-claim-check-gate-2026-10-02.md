# Table-Row Claim Check — Evidence Gate (2026-10-02)

- **Plan:** `docs/superpowers/plans/2026-10-02-table-claim-check.md`.
- **Change:** `src/aaos/rag/table_claims.py`, hooked into `IndependentClaimEntailmentVerifier.verify` after a neutral NLI verdict.
- **Runs:** from the runtime data root, using the `evaluation_scope()` candidate set, on the same machine and models as the baseline.

## Gate results

| # | Criterion | Result |
|---|---|---|
| 1a | Unanswerable questions refused, with 0 cited answers (3-run trace) | **8/8, 0 cited**: met |
| 1b | Expected-page hits do not fall beyond noise | 78/96, against 80/96 baseline. See analysis: met, as noise |
| 2 | `se440-weights` EN and `project-weights` EN each answered in ≥4/5 runs at production settings | `project-weights` **5/5**: met. `se440-weights` **3/5**: **NOT met** |
| 3 | Repeatable inputs (fixed IDs, generator at temperature 0): both cases 5/5 | **5/5 and 5/5**: met (both were 0/5 before) |
| 4 | Every table-rule acceptance checked by hand | 13 acceptances, all true. See below: met |
| 5 | A–H selectors, full suite, ruff, mypy | 95 passed; 4111 passed, 6 skipped; clean: met |

**Stop rule (wrong claim accepted, or unanswerable guard dropped):** not triggered.

## Every claim the rule accepted

All 13 acceptances are one of four wordings of two true facts:

- "The final exam is worth 40% of the SE440 grade" (and "…of the course grade"). Row: `Final Exam (40%)`.
- "The machine learning implementation component is worth 20% of the project grade" (and "…of the grade", "…of the KNIME course project grading"). Row: `Machine learning implementation 20%`.

Each was checked against the row text. No other claim was accepted by the rule.

## Analysis

### Criterion 2 (`se440-weights` 3/5)

Both production misses are generator wordings that the v1 rule declines by design:

- **"The Final Exam (Final Part#2) is worth 40% of the grade."** `Part#2` is a second number, so the rule steps aside.
- **"The provided evidence states that the 'Final Exam (40%)' corresponds to the 'Final Part#2' component…"** This is a statement about the evidence, which leaves content words the rule must not ignore.

Both claims are true. Widening the rule to accept them would be tuning against the gate, which the plan forbids. They remain refusals, which is the safe direction.

### Criterion 1b (78 vs 80)

The rule only *adds* an acceptance after a neutral verdict. When it does not match, the verifier runs exactly as before. The rule therefore cannot cause a new refusal.

The trace misses that differ from the baseline are all claims with no percentage, in other cases:

- `knime-missing` EN: five claims about the Missing Value node;
- `ml-pickle` EN;
- `se440-prerequisite` AR;
- `ai495-five-vs` AR;
- one `INVALID_EVIDENCE_REFERENCE` generator error.

This is the run-to-run variation documented in `rag-coverage-flips-2026-10-02.md`. Counting the `ml-precision` page-23 answers as correct, the trace is 81/96.

## Recommendation

- The change is strictly additive and fail-closed, accepted no wrong claim, and moves both target questions from unreliable to reliable under repeatable inputs.
- Criterion 2 is missed by one case because of generator wording outside v1's scope.
- **Owner decision:** accept with criterion 2 recorded as missed, or keep it unmerged.
