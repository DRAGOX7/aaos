# Phase I4: Arabic Retrieval — Measurement and Decision (2026-10-01)

## Question

The Phase I spec asked whether Arabic questions need (a) dense-only retrieval with adjusted sufficiency handling, or (b) a bounded model-produced English keyword rewrite for the lexical half, because the indexed documents are English and keyword search finds few hits for Arabic.

## Method

- Frozen manifest `tests/evaluation/phase_i4_arabic_retrieval_cases_v1.json` (SHA-256 `b4f01e14…3045e`): 16 answerable questions with a known document and page across all five indexed documents, plus 4 unanswerable questions. Every Arabic question is a translation of its English counterpart.
- Runner `scripts/phase_i4/arabic_retrieval_eval.py`, run from the runtime data root:
  - `retrieval`: embedding, dense, lexical and RRF only (no language model);
  - `answers`: the complete production RAG path (global scope).
- One run each, local `qwen/qwen3.5-4b`, unchanged production configuration. Raw rows: `phase-i4-arabic-retrieval-2026-10-01-retrieval.json` and `…-answers.json`.

## Results

| Case | EN fused rank | EN keyword hits | EN outcome | AR fused rank | AR keyword hits | AR outcome |
|---|---|---|---|---|---|---|
| se440-weights | 1 | 30 | refused ✗ | 1 | 5 | answer ✓ |
| se440-prerequisite | 3 | 30 | answer ✓ | 3 | 5 | answer ✓ |
| se440-makeup | 1 | 30 | refused ✗ | 3 | 5 | refused ✗ |
| se440-exam-format | 1 | 30 | partial ✓ | 2 | 5 | refused ✗ |
| se440-textbook | 1 | 30 | refused ✗ | 2 | 5 | refused ✗ |
| ai495-instructor | 1 | 30 | error INVALID_EVIDENCE_REFERENCE ✗ | 1 | 7 | refused ✗ |
| ai495-five-vs | 1 | 30 | refused ✗ | 1 | 7 | partial ✓ |
| ml-train-test | 1 | 30 | partial ✓ | 1 | 0 | partial ✓ |
| ml-pickle | 1 | 30 | partial ✓ | 1 | 3 | partial ✓ |
| ml-outlier | 1 | 30 | answer ✓ | 1 | 9 | partial ✓ |
| ml-precision | 1 | 30 | refused ✗ | 2 | 11 | refused ✗ |
| ml-kfold | 1 | 30 | answer ✓ | 1 | 17 | answer ✓ |
| knime-what | 1 | 30 | answer ✓ | 3 | 30 | answer ✓ |
| knime-kmeans | 1 | 30 | answer ✓ | 1 | 22 | answer ✓ |
| knime-missing | 2 | 30 | answer ✓ | 2 | 29 | partial ✗ |
| project-weights | 1 | 30 | answer ✓ | 7 | 30 | refused ✗ |
| none-parking | — | 30 | refused ✓ | — | 0 | refused ✓ |
| none-cafeteria | — | 30 | refused ✓ | — | 0 | refused ✓ |
| none-transformer | — | 30 | refused ✓ | — | 1 | refused ✓ |
| none-scholarship | — | 30 | refused ✓ | — | 2 | refused ✓ |

- EN: expected chunk in selected evidence (fused rank ≤ 6) 16/16; cited the expected page 10/16; refused answerable 5/16; errors 1; unanswerable refused 4/4; median 26.0 s
- AR: expected chunk in selected evidence (fused rank ≤ 6) 15/16; cited the expected page 9/16; refused answerable 6/16; errors 0; unanswerable refused 4/4; median 24.1 s

"Fused rank" is the best rank of a chunk on an expected page; the evidence selector keeps the top 6. "Keyword hits" is the lexical candidate count (capped at 30).

## Findings (facts)

1. **Arabic retrieval is not the bottleneck.** Keyword search finds far fewer hits for Arabic (0–30 vs always 30), as the spec observed, but dense retrieval (BGE-M3, multilingual) puts the expected chunk at fused rank 1–3 for 15/16 Arabic questions. Only `project-weights` (rank 7, just outside the 6 selected chunks) lost an answer to retrieval.
2. **End-to-end, English and Arabic are within one case:** 10/16 vs 9/16 answers citing the expected page. Both correctly refuse all 4 unanswerable questions; neither produced a cited answer to an unanswerable question.
3. **The shared loss is after retrieval.** 5 English and 6 Arabic answerable questions were refused even though the expected chunk was selected (mostly at rank 1). Outcomes also flip between languages on the same evidence (`se440-weights` refused in English, answered in Arabic; `ai495-five-vs` the reverse), which points at generation or verification behaviour, not retrieval.
4. One English question failed with `INVALID_EVIDENCE_REFERENCE` (the model cited an evidence id outside the supplied evidence; the validator correctly failed closed).

## Decision

**No Arabic-specific retrieval change.** Neither option is justified by the evidence:

- (a) Dense-only for Arabic: dense already carries Arabic, and sufficiency is not where Arabic questions are lost; changing it would add a language-specific path for no measured gain.
- (b) English keyword rewrite: at most it could recover `project-weights`, one case, at the cost of an extra model call per Arabic question and a new untrusted-text boundary.

I4 therefore ships the frozen measurement and runner as the regression baseline instead of a retrieval change.

## Limitations

- Single run of a local 4B model; refusals vary run to run, so differences of one case are within noise.
- 20 question pairs over 5 documents; the documents are English.

## Recommended follow-up (outside I4)

Investigate why about a third of answerable questions are refused in both languages when the right chunk is ranked first (generation refusal vs claim/citation validation), and the `INVALID_EVIDENCE_REFERENCE` failure. This touches frozen Phase 4 generation contracts, so it needs its own approved plan.
