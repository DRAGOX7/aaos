# Reranker Spike — Result (2026-10-02)

**Outcome: STOPPED at step 1, under the plan's stop rule (negligible possible gain).** The reranker was not downloaded. No production code was changed.

Plan: `docs/superpowers/plans/2026-10-02-reranker-and-speculative-decoding.md`, section A.

## What was measured

**Question:** can reordering retrieval candidates put the right passage into the evidence more often?

**The ceiling:** a cross-encoder only reorders the fused candidates. So its best possible effect is to move the expected chunk into the 6-chunk evidence when fusion ranked it 7th or lower.

**Method (evaluation-only):**

- **Questions:** the frozen Phase I4 manifest `tests/evaluation/phase_i4_arabic_retrieval_cases_v1.json`, with 16 answerable questions and 4 unanswerable, each in English and Arabic.
- **Pipeline:** today's retrieval, made of dense search, lexical search and RRF (k=60), with no language model.
- **Scope:** every indexed document version (`HistoricalScope`), because the Summer courses are now archived. This is the same candidate set the earlier global runs used.
- **Run from:** the runtime data root.

## Result

The fused rank of the expected chunk, for the 16 answerable questions:

| | Rank 1 | Rank 2–3 | Rank 4–6 | Rank ≥ 7 (outside evidence) |
|---|---|---|---|---|
| English | 14 | 2 | 0 | 0 |
| Arabic | 9 | 6 | 0 | 1 (`project-weights`, rank 7) |

This is identical to the 2026-10-01 Phase I4 run, so retrieval has not changed since. A reranker could rescue **at most 1 of 32** answerable cases.

## Where answers actually fail

The latest 3-run trace, after the bounded claim re-check, has 96 answerable rows. **80** cite the expected page. The 16 misses are:

| Stage where it fails | English | Arabic |
|---|---|---|
| Coverage check refuses (S2) | 3 | 6 |
| Answered, but cited a different page | 1 | 4 |
| Generator refuses (S3) | 0 | 1 |
| Claim check rejects (S4) | 1 | 0 |
| Retrieval misses the chunk | 0 | 0 |

The expected chunk is already in the evidence. The losses come later, mostly at the **coverage check**, where the model decides the evidence does not cover the question. Arabic questions lose there twice as often as English ones.

## Recommendation

- Do not add a reranker now. It would cost ~2.3 GB of RAM and 1–5 s per question for at most one case.
- Point quality work at the S2 coverage check, especially for Arabic. That is a separate investigation, and it needs its own approval.
