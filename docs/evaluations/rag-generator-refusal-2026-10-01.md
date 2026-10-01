# RAG Generator Refusals — Investigation (2026-10-01)

Follow-up to `rag-refusal-trace-2026-10-01.md` (stage S3). No production code was changed.

## Failure definition

After the claim-verification fix, the generator refused (stage S3) in two cases even though coverage judged the question supported:

- `ai495-instructor`: English and Arabic, in every run.
- `ml-precision`: Arabic, in most runs.

The same two cases produced all three `INVALID_EVIDENCE_REFERENCE` errors in the after-fix trace.

## Evidence

An evaluation-only probe (session scratchpad) recorded three things for these cases through the production `ask` path:

1. what the generator was given and its raw output;
2. the evidence ids coverage cited;
3. the evidence ids span extraction chose.

| Case (runs) | Coverage cited | Spans chosen from | Evidence the generator saw | Outcome |
|---|---|---|---|---|
| `ai495-instructor` en/ar (4) | the syllabus chunk of document version 1 | the identical chunk of version 6 (a duplicate upload) | **none** | refused |
| `ml-precision` ar (2) | two ML-notes chunks | a third ML-notes chunk | **none** | refused |
| `ml-precision` en (2) | same chunk as the spans | — | that chunk | answered / claim-NLI |

In every refusal, the generator's raw output says the evidence is missing. One Arabic answer states the evidence list is empty.

## Root cause

`_build_messages` (`src/aaos/rag/generation.py`) builds the generator's evidence with two successive filters:

1. keep evidence ids cited by the **coverage model** for supported requirements;
2. keep only evidence that has **span quotes** for supported requirements, replacing its text with those quotes.

Coverage citations and span selection are separate model calls. When they choose different chunks — including identical text under different ids from a duplicate document — the intersection is empty. The generator is then called with no evidence and can only refuse.

**Violated invariant:** a requirement judged SUPPORTED must reach the generator with non-empty evidence. Nothing checks this today.

**Misleading symptom:** this looked like model timidity (S3 "generator refusal"), but the model was answering correctly to an empty evidence list.

**Hypothesis (not reproduced in this probe):** when given no evidence, the model sometimes invents an evidence id, which the validator rejects as `INVALID_EVIDENCE_REFERENCE`. All three such errors occurred in these two cases.

## Proposed fix (needs approval; changes frozen Phase 4 behaviour)

1. Build the generator's evidence from the validated span quotes of supported requirements. These are exact substrings of selected evidence, already checked by `_validate_atomic_spans`. Coverage-cited ids are no longer required to coincide with them.
2. If no supported requirement has any span evidence, return the fixed refusal **without calling the generator**, so it can't be asked to answer from nothing.

**Unchanged:**
- Coverage still gates generation (an UNSUPPORTED requirement contributes nothing).
- Claim verification, citation validation and the trust boundary are unchanged.

**Verification:**
- A RED regression test from the duplicate-document case.
- The full suite and the frozen A–H selectors.
- A 3-run trace re-measure, keeping unanswerable questions 8/8 refused with no citations.
