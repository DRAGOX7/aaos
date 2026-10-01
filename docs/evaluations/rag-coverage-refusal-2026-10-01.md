# RAG Coverage Refusals — Investigation (2026-10-01)

Follow-up to `rag-refusal-trace-2026-10-01.md` (stage S2: atomic requirement coverage). No production code was changed.

## Failure definition

Two answerable cases were refused at S2 in every traced run:

- **`project-weights` (Arabic):** a retrieval loss, not a coverage error. The expected chunk ranked 7th, just outside the 6 selected chunks, so the evidence the model saw truly lacked the answer. The refusal is correct given its input.
- **`se440-makeup` (English and Arabic):** "Are makeup exams allowed in SE440?" The selected evidence contains the syllabus's makeup-exam policy line (makeup exams are not given unless a valid excuse is presented), yet `RequirementCoverageClassifier` judges the single requirement UNSUPPORTED, so the generator never runs.

## Experiments

All runs use the production local model at temperature 0 and the real selected evidence (5 chunks, about 2,350 tokens). Each was evaluation-only: probes in the session scratchpad replayed the production coverage call.

| # | Variant (one change each) | Verdict |
|---|---|---|
| 1 | Production input (5 chunks), repeated 3× | UNSUPPORTED 3/3 |
| 2 | Only the policy chunk | **SUPPORTED** 3/3 |
| 3 | Same 5 chunks, requirement phrased "Are makeup exams prohibited in SE440?" | **SUPPORTED** 3/3 |
| 4 | Same 5 chunks, "What is the makeup exam policy for SE440?" | UNSUPPORTED 3/3 |
| 5 | Policy chunk moved first / last | UNSUPPORTED (it was already first) |
| 6 | Policy chunk paired with each other chunk | SUPPORTED with 3 of 4; UNSUPPORTED with the syllabus page-1 chunk, which contains no exam text |
| 7 | Leave one chunk out (4 chunks) | UNSUPPORTED in all 4 |
| 8 | Coverage over the extracted span quotes instead of full chunks (all 40 manifest questions) | makeup still UNSUPPORTED; **one unanswerable question became SUPPORTED** |
| 9 | One added prompt sentence: negative or conditional answers count as support (all 40 questions) | all 40 verdicts identical to production |

## Conclusions

- **Not ordering, not a conflicting chunk, not negation alone** (experiments 4, 5 and 9). The verdict depends on how much unrelated evidence surrounds the policy line (2, 6, 7) and on wording that closely matches it (3). This is a capacity limit of the local 4B model on this input. No code invariant is violated; the classifier receives exactly the evidence it should.
- **Rejected candidate fixes:**
  - Span-quote coverage (8) breaks fail-closed: an unanswerable question became supported.
  - The prompt sentence (9) has no effect.
- **No change is recommended.** The failure is one question in a 20-question set. The fail-closed outcome (a refusal rather than a wrong answer) is the safe direction, and every candidate either did nothing or weakened the unanswerable guarantee.

## Options if this matters later (each needs its own approval and a full re-measure)

- A larger coverage model.
- Fewer or shorter chunks shown to the coverage step. Experiment 2 suggests this helps, but it also trades off recall and needs the unanswerable guard re-checked.
- Better retrieval for `project-weights` (Arabic), which is the other S2 case.
