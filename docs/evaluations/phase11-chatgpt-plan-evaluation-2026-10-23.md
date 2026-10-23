# Phase 11 §10 Evaluation: ChatGPT Plan vs Local Model (2026-10-23)

The Phase 11 spec §10 asks for at least three runs of the frozen Phase I4 answer evaluation on the
ChatGPT-plan model, recorded beside the local model's results. This report covers that run.

- **Evidence only.** No production code was changed for this evaluation, and the default model
  stays local.
- **Consent.** The owner consented to sending the evaluation's course excerpts to OpenAI.

## Setup

| | |
|---|---|
| Model | `gpt-6.1-sol` on the owner's ChatGPT plan (Sign in with ChatGPT), through `ChatGPTPlanGateway` |
| Code | `main` at `0d829d3a` |
| Questions | Frozen manifest `tests/evaluation/phase_i4_arabic_retrieval_cases_v1.json`: 16 answerable questions and 4 unanswerable ones, each in English and Arabic |
| Manifest hash | SHA-256 `b4f01e14…3045e` for the committed content. The working copy hashes differently only because of CRLF line endings. |
| Procedure | `scripts/phase_i4/refusal_trace.py`'s recording proxies and stage rules, unchanged. Answerable cases run 3 times and unanswerable cases once: 96 answerable rows and 8 unanswerable rows. |
| Driver | An evaluation-only driver that also keeps each answer's cited pages, so the `ml-precision` page-23 rule can be applied as in the local baseline |
| Scope | `evaluation_scope()`, the same candidate set as the local runs, on the real index |
| Local parts | Embeddings (BGE-M3, CPU) and the NLI claim checker ran on this computer, as with every model |

**Where the raw data is.** The raw trace stays outside the repository, because it holds question text
and short span excerpts:
- path: `aaos-data/data/evaluations/phase11-chatgpt-plan-trace-2026-10-23.json`;
- SHA-256: `5ea9b91e2313f8c4ae45598312f9469537d7ad870c89d4a9a074972b5dd70db2`.

This report has counts only.

**Before the run.** The local embedding model BGE-M3 was missing from this computer's Hugging Face
cache, so every document question failed for every provider. It was re-downloaded at the pinned
revision `89f985cb…`, with the owner's approval, and confirmed to load offline.

## Results

| | ChatGPT plan (`gpt-6.1-sol`) | Local model (`qwen/qwen3.5-4b`) |
|---|---|---|
| Answerable rows citing an expected page | **86/96 (90%)** | 78/96 (81%) |
| Same, counting `ml-precision` page-23 answers as correct | **86/96** | 81/96 |
| Per run | 29/32, 29/32, 28/32 | not recorded per run |
| English / Arabic | 44/48 / 42/48 | not recorded |
| Unanswerable rows refused, and cited answers among them | **8/8 refused, 0 cited** | 8/8 refused, 0 cited |
| Errors | 0 | 0 |
| Seconds per question | median 21.1, mean 23.0, max 49.5 | not comparable (a different machine load) |

**Where the local figures come from:** the table-claim-check gate
(`table-claim-check-gate-2026-10-02.md`), a 3-run trace of the same manifest and procedure. The
local RAG path has not changed since that run; the later commits only add the opt-in cloud
providers. That gate is also where the `ml-precision` page-23 adjustment (81/96) is stated.

## Where the answers were lost

Of the ChatGPT plan's 10 misses:

| Case | Hits | Stage | Notes |
|---|---|---|---|
| `project-weights` AR | 0/3 | S2 (no supporting spans) | **Retrieval, not the model.** The local model loses the same case: the expected chunk is fused rank 7, just outside the 6 evidence chunks (`reranker-spike-2026-10-02.md`). |
| `ai495-five-vs` EN | 1/3 | S4 (claim check) | Flip |
| `ml-pickle` EN | 1/3 | S4 (claim check) | Flip |
| `ml-train-test` AR | 1/3 | Answered, but cited pages 19–20 rather than the expected 6–7 | Flip. Not checked by hand, so whether pages 19–20 also hold the answer is unknown. |
| `se440-makeup` AR | 2/3 | S4 (claim check) | Flip |

All other 27 case-and-language pairs were answered with an expected page in 3 of 3 runs.

## Comparison by case

**Better with the ChatGPT plan:**
- **`se440-makeup`:** the local model's systematic loss. Its coverage check (S2) refuses although
  the policy line is in the evidence, which the flips report calls a capacity limit of the 4B model.
  - Local: 5 misses in 6 rows.
  - ChatGPT plan: English 3/3, Arabic 2/3, with the one miss at the claim check, not coverage.
- **`ml-precision`:** the local model's answers cited page 23 (4 rows). With the ChatGPT plan, all
  6 rows cite the expected pages 25–26.
- **`se440-weights` EN:** still flipped locally after the table rule (3/5 in the gate's 5-run check).
  3/3 here.
- **`project-weights` EN:** flipped locally before the table rule (5/5 after it). 3/3 here.

**Unchanged:**
- **`project-weights` AR** is lost in both, at retrieval. A stronger model can't fix it.
- **The claim check (S4)** is still where flips come from: 5 of the 10 misses. That check runs
  locally and is the same for every model.

**New flips:** `ai495-five-vs` EN, `ml-pickle` EN and `ml-train-test` AR. Each succeeded in at least
one run, so these are run-to-run variation, not systematic losses. The spec expected this, since the
preview ignores `temperature`.

## Facts and interpretation

**Facts.** With the same retrieval, prompts, checks and frozen questions, the ChatGPT-plan model:
- cited an expected page in 86 of 96 answerable rows;
- refused all 8 unanswerable rows with no citations, so the fail-closed guard held;
- produced no errors.

**Interpretation.**
- The gain over the local model (+8 rows raw, +5 adjusted) comes mainly from the coverage step,
  which the local model was too small for (`se440-makeup`).
- The remaining losses are mostly in parts no model change touches: retrieval for
  `project-weights` AR, and the local claim check (S4).
- Three runs are a small sample. A difference of 5–8 rows out of 96 is larger than the run-to-run
  variation seen locally (78–80 across traces), but it is one evaluation on one day.

## Limitations

- One model (`gpt-6.1-sol`), one day, and three runs.
- The local figures come from a run on 2026-10-02, not a fresh side-by-side run.
- Answer wording was not graded, only whether an expected page is cited. The ChatGPT-plan answers
  that cited other pages were not read by hand.
- Plan usage for the run (about 104 questions, several model calls each) was not measured from
  OpenAI's side.
- Speed depends on OpenAI's service at the time, and the local model's speed was not measured in
  the same conditions.

## Decision

None is made here. The default stays the local model. This is evidence for the owner's Phase 11
acceptance decision, as spec §10 sets out.
