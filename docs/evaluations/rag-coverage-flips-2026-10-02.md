# RAG Answer Misses and Run-to-Run Flips — Investigation (2026-10-02)

Follow-up to `reranker-spike-2026-10-02.md`, which found that retrieval is not where answerable questions are lost. No production code was changed. The evaluation tools were fixed; see "Tooling".

## Failure definition

The latest 3-run trace (bounded claim re-check) has 96 answerable rows from the frozen Phase I4 manifest. **80** cite the expected page. This investigation asks why the other **16** miss.

## Where the 16 misses come from

| Case | Misses | Cause | Evidence |
|---|---|---|---|
| `se440-makeup` (EN 2, AR 3) | 5 | Coverage check (S2) refuses although the policy line is in the evidence. This is a known capacity limit of the local 4B model. | `rag-coverage-refusal-2026-10-01.md`, experiments 1–9 |
| `project-weights` AR | 3 | Retrieval: the expected chunk is fused rank 7, just outside the 6 evidence chunks. | `reranker-spike-2026-10-02.md` |
| `ml-precision` (EN 1, AR 3) | 4 | **Not a miss.** The answers cite page 23. That page holds `precision = TP / (TP + FP) if (TP + FP) > 0 else 0`, and the Arabic answers repeat it, including the zero rule. The frozen manifest lists only pages 25–26 (the concept table). | chunk text of version 3, page 23 |
| `project-weights` EN, `se440-weights` EN, `knime-missing` AR, `ai495-five-vs` AR | 4 | **Run-to-run flips:** the same question is answered in some runs and refused in others. | experiment below |

**Correction to an earlier claim:** coverage refusals are **not** a broad Arabic weakness. Of the 9 S2 refusals, 5 are `se440-makeup` in both languages and 3 are the `project-weights` AR retrieval loss.

**Adjusted count:** the 4 `ml-precision` rows are correct answers. That puts the trace at **84/96**. The systematic losses are two known cases. The remaining misses are flips.

## Experiment: does the generator's unset temperature cause the flips?

**Observation:** the generator's `ChatRequest` sets no temperature, so LM Studio's default applies. Every coverage and decomposition call uses `temperature=0.0`. Nothing in `docs/` records why. The temperature was never set.

**Method:**

- The 5 flipping cases, 5 runs each, under two conditions:
  - production;
  - the generator's requests carrying `temperature=0.0`, patched inside the probe process only.
- Everything else is production, traced with `scripts/phase_i4/refusal_trace.py`.

| Case | Production (default temperature) | Generator at temperature 0 |
|---|---|---|
| `ai495-five-vs` AR | 4/5 (1× S4) | 5/5 |
| `knime-missing` AR | 5/5 | 5/5 |
| `project-weights` EN | 4/5 (1× S4) | 2/5 (2× S4, 1× S2) |
| `se440-makeup` EN | 1/5 (4× S2) | 1/5 (4× S2) |
| `se440-weights` EN | 3/5 (2× S4) | **0/5 (5× S4)** |
| **Total** | **17/25** | **13/25** |

## Conclusions

- **Temperature 0 does not remove the flips and is not better.**
  - `project-weights` still flips at temperature 0, including an S2 refusal. S2 already runs at temperature 0, so the variation enters elsewhere.
  - `se440-weights` became a steady S4 failure: with greedy decoding, the generator's wording is one the claim check always rejects.
- **No temperature change is recommended.**
- **The remaining failures are at the claim check (S4) and the coverage check (S2).** The S4 losses show up as flips.

## Experiment 2: random evidence IDs

**Hypothesis:** variation that survives temperature 0 comes from inputs that change between runs. The evidence selector is the only random source in the RAG pipeline: it names each chunk with `token_urlsafe(18)` (`retrieval.py`), and those IDs appear in the coverage and generator prompts.

**Method:**

- In the probe only, a seeded ID sequence of the same shape (24 URL-safe characters) is reset before every question. The same evidence therefore gets the same IDs in every run.
- Same 5 cases × 5 runs, under two conditions:
  - fixed IDs with the generator at temperature 0, so every model input is identical across runs;
  - fixed IDs with production's generator temperature.

| Case | Fixed IDs + generator at temperature 0 | Fixed IDs + production temperature |
|---|---|---|
| `ai495-five-vs` AR | 5/5 | 5/5 |
| `knime-missing` AR | 5/5 | 5/5 |
| `project-weights` EN | **0/5 (5× S4)** | 3/5 (2× S4) |
| `se440-makeup` EN | 0/5 (5× S2) | 0/5 (5× S2) |
| `se440-weights` EN | **0/5 (5× S4)** | 2/5 (3× S4) |
| **Total** | **10/25** | **15/25** |

### What it shows

- **The flips have two sources:** random evidence IDs and generator sampling.
  - With both fixed, every case returns the same outcome in all 5 runs.
  - With only the temperature fixed (experiment 1), `project-weights` still flipped.
- **Making the pipeline deterministic lowers the score** (10/25 against 15–17/25). The flips were not noise around a correct answer. Some runs got lucky with wording that passes the claim check.
- **Deterministic IDs are not recommended:** they would lock in the failures. The defect is in the claim check.

## Root cause of the S4 flips: claim NLI on table rows

Both deterministic S4 failures are **true claims** whose premise is a table flattened into text:

| Claim (generator) | What the evidence says | NLI verdict |
|---|---|---|
| "The final exam is worth 40% of the SE440 grade." | `Final Exam (40%) Final Part#2` in the SE440 syllabus assessment table | neutral 0.74, entailment 0.25 |
| "The machine learning implementation component is worth 20% of the project grade." | `Machine learning implementation 20%` in the KNIME project's criteria table | neutral 0.84, entailment 0.15 |

- **Violated assumption:** the claim check assumes its premise reads as prose. The multilingual NLI model cannot connect "X is worth 20%" to a header-value row like "X 20%".
- **What sampling did:** sometimes the generator happened to phrase a claim that the NLI accepted.
- **Why this is safe but costly:** it fails closed, refusing rather than misleading, but it loses correct answers about grade weights, the questions students ask most.

## Candidate fixes (none implemented; each needs approval and the full gate)

1. **Present table rows to NLI as sentences.** For example, restate `Machine learning implementation 20%` as "Machine learning implementation: 20%." when the premise span comes from a table row. Low risk, but it may not move the NLI enough.
2. **A narrow table check:** accept a claim when the same premise row contains both the claim's item label and its exact number. This is deterministic and fast. The risk is false acceptance (for example the wrong row, or a number used for something else), so it needs the unanswerable guard and a contradiction test set.
3. **Ask the local model to re-judge NLI-neutral claims on table premises.** It reads tables far better than the NLI model, but costs ~2–3 s per claim, and it makes model judgment part of verification.

Option 2 or 1 first. Measure on the frozen manifest plus a set of deliberately wrong table claims (wrong number, wrong row), which must all still be rejected.

## Tooling

- `scripts/phase_i4/arabic_retrieval_eval.py` and `refusal_trace.py` used `GlobalScope`. Since the Summer courses were archived, that covers current courses only, so the tools found no documents.
- They now pin `evaluation_scope()`: every version in the active index. That is the candidate set the earlier unscoped runs used. Their results remain comparable with earlier runs.
- A pinned version that is not in the active generation fails with `RETRIEVAL_INCONSISTENCY` at evidence selection. The agent never builds such a scope, because it pins only READY versions. That was confirmed on the live database.

## Recommended follow-up (each needs approval)

1. Fix the claim check for table premises (candidate fixes above). This addresses the S4 flips.
2. Publish a v2 of the I4 manifest that accepts page 23 for `ml-precision`. The v1 manifest is frozen and stays unchanged.
