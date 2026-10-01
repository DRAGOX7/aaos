# RAG Refusal Trace — Results (2026-10-01)

Steps 1–3 of `docs/superpowers/plans/2026-10-01-rag-refusal-investigation.md`. Production code and behaviour were not changed.

## Method

- `scripts/phase_i4/refusal_trace.py` wraps the production service's components with recording proxies, for the evaluation process only. It records:
  - sufficiency;
  - decomposed requirements and selected spans;
  - coverage judgments;
  - generator status;
  - each claim's NLI label and scores;
  - the final status.
- Manifest: `tests/evaluation/phase_i4_arabic_retrieval_cases_v1.json`. The 16 answerable cases ran 3 times and the 4 unanswerable cases once, each in English and Arabic: 104 queries, with the model alone on the GPU.
- Committed rows: `rag-refusal-trace-2026-10-01.json`. Requirement text, span excerpts and claim text are removed because they quote the owner's course documents. The full local trace was used for the per-case reading below.

## Correction to the plan

The plan assumed the production stage 2 used an NLI requirement verifier with no claim verifier. The code says otherwise (`build_phase4_runtime` defaults):

- **Stage 2:** the model decomposes the question, the model selects evidence segments, then the model-based `RequirementCoverageClassifier` judges coverage.
- **Generated claims:** each is then checked by `IndependentClaimEntailmentVerifier`, a pinned NLI model. Its premise is the selected span quotes (≤ 400 characters each), not the full cited chunk.
- A claim that fails is dropped. If every claim is dropped, the answer becomes the fixed refusal; if only some are, it becomes `PARTIAL_ANSWER`.

So there are five refusal points, not four. The trace records all of them.

## Results

Answer quality per run (answerable questions citing the expected page, out of 16):

| Run | English | Arabic |
|---|---|---|
| 1 | 11 | 10 |
| 2 | 10 | 9 |
| 3 | 12 | 10 |

Unanswerable: 8/8 refused, 0 cited answers.

**Where the 31 answerable refusals stopped:**

| Stage | Refusals | Share |
|---|---|---|
| S4 claim NLI rejected every claim | 14 | 45% |
| S2 coverage judged the question unsupported | 9 | 29% |
| S3 generator chose to refuse | 8 | 26% |
| S1 sufficiency gate | 0 | 0% |

There were also 2 `INVALID_EVIDENCE_REFERENCE` errors, where the generator cited an evidence id that wasn't supplied.

**Per case, the stage is largely stable across runs:**

- S2 every time: `se440-makeup` (both languages) and `project-weights` (Arabic). The Arabic `project-weights` loss matches its retrieval rank of 7, just outside the 6 selected chunks.
- S3 every time: `ai495-instructor` (both languages) and `ml-precision` (Arabic).
- S4: every run for `ai495-five-vs` (English); intermittent for `se440-textbook`, `se440-weights`, `ml-precision` (English), `se440-exam-format`, `knime-missing` and `project-weights` (English).

**Claim NLI overall:** 47 of 202 generated claims were rejected, 39 as `neutral` and 8 as `contradiction`. Rejected claims inside otherwise-answered questions explain the many `PARTIAL_ANSWER` results.

## Finding

**Decision rule outcome:** no single stage reaches the plan's 70% threshold, so this run does not justify one fix for all refusals.

**One concrete mechanism dominates S4.** Claims that restate the course or document context ("SE440", "AI 495C", "syllabus", "course", Arabic مادة/منهج) were rejected 62% of the time (21/34). Claims that don't were rejected 15% of the time (26/168). (Computed from the local full trace.)

The recorded cases show why. The span quotes chosen for the requirement contain the fact but not the context the claim restates:

- A quote listing "the five V's" without "AI 495C syllabus": entailment 0.001.
- A quote naming the book without the "Textbooks:" heading or "SE440": entailment 0.001.

The claim is supported by the cited chunk, but not by the narrower premise the verifier sees.

**Violated invariant (candidate):** a generated claim that its cited evidence supports should verify. The verifier's premise (requirement span quotes) is narrower than the evidence the claim cites, so correct claims are rejected as `neutral`.

**Other causes (smaller, unresolved):**

- S2 coverage judges `se440-makeup` unsupported even though its span contains "No makeup exams …".
- S3 generator refusals despite the instructor line being in the evidence.
- The `INVALID_EVIDENCE_REFERENCE` errors.

## Proposed next step (needs approval; changes frozen Phase 4 behaviour)

Fix only the S4 premise mismatch, smallest first, with a RED regression test built from the five-V's case:

- **Option A:** add the cited chunk's trusted document title and heading to the claim-verification premise, keeping the span quotes.
- **Option B:** when the span premise is `neutral`, re-check against the full cited chunk, with bounded windows like the summary verifier. `contradiction` still rejects.

**Constraints:**

- Unanswerable questions stay 8/8 refused with 0 cited answers.
- Contradiction stays a rejection.
- The A–H selectors pass.
- The trace is re-run for 3 runs before and after.

**Expected effect:** fewer S4 refusals and partial downgrades. It does not address S2 or S3.

## After the claim-verification fix (commit `bc4d5583`)

The owner approved Option A combined with Option B's fallback, after a check on the real NLI model showed Option A alone fixed 1 of 3 recorded failing claims and the combination fixed 2:

- the claim premise now starts with the cited documents' titles;
- a *neutral* span verdict is re-checked against each full chunk the claim cites, in bounded windows;
- a contradiction still rejects.

Same manifest, same 3-run protocol (`rag-refusal-trace-2026-10-01-after-claim-fix.json`, document text removed):

| | Before | After |
|---|---|---|
| English: expected page cited (runs) | 11, 10, 12 (mean 11.0/16) | 12, 13, 13 (mean 12.7/16) |
| Arabic: expected page cited (runs) | 10, 9, 10 (mean 9.7/16) | 12, 12, 12 (mean 12.0/16) |
| Answerable refusals at S4 claim NLI | 14 | 2 |
| Answerable refusals at S2 / S3 (unchanged scope) | 9 / 8 | 9 / 7 |
| Generated claims rejected | 47 / 202 | 20 / 198 |
| Unanswerable refused / cited answers | 8/8 / 0 | 8/8 / 0 |
| Answers without the expected page | 1 | 1 (same borderline case: `knime-missing` cites the adjacent "Missing Value Node" heading page) |
| `INVALID_EVIDENCE_REFERENCE` errors | 2 | 3 (generator-side, not touched by this fix) |
| Median / max seconds per query | 27.4 / 50.1 | 26.0 / 67.5 |

**Remaining causes:**

- S2 coverage refusals (`se440-makeup` both languages, `project-weights` Arabic, which is the retrieval loss at rank 7).
- S3 generator refusals (`ai495-instructor`, `ml-precision` Arabic).
- Generator citations of evidence ids it wasn't given.

Each needs its own investigation.
