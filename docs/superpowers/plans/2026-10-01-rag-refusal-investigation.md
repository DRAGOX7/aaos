# RAG Refusal Investigation — Plan

Status: **proposed**, awaiting owner approval. Diagnosis (Steps 1–3) is read-only with respect to production behaviour. Any change to frozen Phase 4 behaviour (Step 4) needs a separate approval with the evidence attached.

## Failure definition

On the frozen I4 manifest (`tests/evaluation/phase_i4_arabic_retrieval_cases_v1.json`), the production RAG path refused answerable questions whose expected chunk was already in the selected evidence:

- English: 5/16 refused, all with the expected chunk at fused rank 1; 1/16 failed with `INVALID_EVIDENCE_REFERENCE`.
- Arabic: 6/16 refused; 5 of them had the expected chunk at fused rank ≤ 3.

All 4 unanswerable questions were correctly refused in both languages, and no cited answer was given to an unanswerable question. Source: `docs/evaluations/phase-i4-arabic-retrieval-2026-10-01.md`.

**Expected:** when the selected evidence directly states the answer, the system answers and cites it. **Observed:** a fixed `INSUFFICIENT_EVIDENCE` refusal.

## Where a refusal can come from (facts from the code)

`GuardedLocalRagService.ask` (`src/aaos/rag/service.py`) produces `INSUFFICIENT_EVIDENCE` at one of these stages. The response does not record which.

| Stage | Code | Model call? |
|---|---|---|
| S1 Sufficiency gate `UNSUPPORTED` | `EvidenceSufficiencyGate.evaluate` | no |
| S2 Atomic requirement coverage `UNSUPPORTED` (zero requirements supported): decompose question → select evidence segments per requirement → NLI per requirement → `aggregate_requirement_coverage` | `_classify_atomic_requirements`, `semantic_coverage.py` | yes (decompose, segment selection) + NLI |
| S3 Generator chooses `INSUFFICIENT_EVIDENCE` | `GroundedAnswerGenerator.generate` | yes |
| S4 Validation downgrades to the fixed refusal | `CitationValidator.validate` | no |

Production composes the atomic path (`runtime.py`: `atomic_requirement_decomposer`, `evidence_span_extractor`, `entailment_verifier`), so S2 runs on every supported question. When S1 or S2 decides `UNSUPPORTED`, `build_insufficient_model_output` returns the refusal **without calling the generator**.

## Evidence so far

- **Timing (weak evidence):** of the 11 refusals, 5 took 17.6–19.7 s, the same band as the unanswerable questions (17.1–19.0 s), which suggests a refusal before generation (S1 or S2). The other 6 took 22.6–44.0 s, which overlaps answered questions (22.8–45.6 s) and could be S2 or S3. Timing alone can't separate the stages.
- **Retrieval is not the cause:** for 11 of the 12 refusals or failures, the expected chunk was at fused rank ≤ 3. All 5 English refusals were at rank 1.
- **Instability:** the same evidence flips between languages (`se440-weights`: refused in English, answered in Arabic; `ai495-five-vs`: the reverse). That points at a model-dependent stage (S2 or S3), not the deterministic S1.
- **Related prior defect:** the summary work found that long chunks overflow the pinned NLI model's 512-token window. S2's NLI raises `ENTAILMENT_INPUT_OVERFLOW` on overflow rather than refusing, so overflow alone does not explain a refusal. Whether segment selection keeps spans small enough is still unverified.

## Ranked hypotheses

1. **H1 — S2 marks every requirement unsupported:** decomposition produces a requirement the evidence doesn't literally state, segment selection picks the wrong segments, or NLI rejects a paraphrase (e.g. "final exam worth" vs a table row "Final Exam (40%)"). This fits the fast refusals and the instability.
2. **H2 — S3, the generator refuses** despite supported coverage. This could explain the slower refusals.
3. **H3 — S1, the sufficiency gate.** Unlikely: it is deterministic, and the expected chunk was ranked first.
4. **H4 — S4, validation downgrade.** This needs claims, so it would show generator-length timings. Unlikely for the observed cases.
5. **Separate:** `INVALID_EVIDENCE_REFERENCE` (`ai495-instructor`, English). The model cited an evidence id that wasn't supplied, and the validator correctly failed closed. Treated as its own item.

## Discriminating experiment (Steps 1–3, no production behaviour change)

1. **Stage trace in the evaluation runner only.** Extend `scripts/phase_i4/arabic_retrieval_eval.py` with a `trace` mode. It wraps the service's existing components (sufficiency gate, atomic decomposer, segment extractor, entailment verifier, generator, validator) with recording proxies, for this process only. It records per question:
   - S1 status;
   - decomposed requirements;
   - selected segments with their token counts;
   - NLI label and scores per requirement;
   - aggregate coverage;
   - whether the generator ran and its status;
   - the final status.

   Question text and evidence stay in the local results file; nothing is logged by the application.
2. **Repeat to measure instability:** 3 runs × 32 answerable runs (16 English + 16 Arabic), plus the 8 unanswerable runs once, with the model alone on the GPU.
3. **Classify each refusal by stage** and, for S2, by sub-step (decomposition, segment selection, NLI). The violated invariant is identified from this table, not assumed.

**Decision rule:**

- If ≥ 70% of refusals stop at one sub-step, that sub-step is the root-cause candidate; reproduce it with a unit-level fixture from the recorded case.
- If refusals spread across stages, report that and propose nothing until the dominant cause is clear.

## Step 4 — fix (separate approval)

Only after Step 3: one smallest root-cause fix with a RED regression test built from a recorded case. Constraints:

- Fail-closed behaviour is preserved:
  - unanswerable questions stay refused (4/4 in both languages, every run);
  - zero cited answers to unanswerable questions.
- No weakening of citation validation or of the trust boundary.
- The frozen A–H selectors still pass.
- The I4 manifest is re-measured over 3 runs, with before/after recorded.

**Success target:** answerable questions citing the expected page rise from the 10/16 (English) and 9/16 (Arabic) baseline, averaged over 3 runs, with no new false citations.

## Out of scope

Retrieval changes (I4 decided against them); model replacement; summary behaviour (I3 owns its own verifier); the protected V4 held-out material.

## Deliverables

- Trace mode in the evaluation runner.
- Trace results and a stage table under `docs/evaluations/`.
- A root-cause note (root cause, violated invariant, misleading symptom, isolating evidence, regression prevention).
- Then, if approved, the fix commit series.
