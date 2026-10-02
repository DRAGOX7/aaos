# Plan — Table-Row Support Check for Claim Verification (2026-10-02)

**Status: PROPOSED. Not approved. No code written.**

Source investigation: `docs/evaluations/rag-coverage-flips-2026-10-02.md`.

This touches the frozen Phase 4 claim verification, in the same way as the approved claim-verification and latency work. Phase 4 is not reopened. It is a measured improvement, and the owner accepts or rejects it on the evidence gate below.

## Problem

`IndependentClaimEntailmentVerifier` (`src/aaos/rag/semantic_coverage.py`) checks each generated claim with a multilingual NLI model on the CPU. The model cannot link a sentence to a header-value table row:

| Claim | Evidence (cited chunk text) | NLI |
|---|---|---|
| "The final exam is worth 40% of the SE440 grade." | `Final Exam \r\n(40%) Final Part#2` | neutral 0.74 |
| "The machine learning implementation component is worth 20% of the project grade." | `Machine learning implementation 20%` | neutral 0.84 |

Under repeatable inputs, both claims are rejected every time. Today they pass only when sampling happens to find wording the NLI accepts.

**Invariant at stake:** a claim is accepted only when its cited evidence supports it. The fix must keep that, **plus** the fail-closed guarantee: unanswerable questions are refused, and a wrong claim is never accepted.

## Design

A deterministic **table-row support check**. It runs only as an extra way for a claim to be **accepted after the NLI says neutral**. It never overrides a contradiction, and it never runs when the NLI already entails.

### Where it runs

In `IndependentClaimEntailmentVerifier.verify`:

1. Span NLI (unchanged). ENTAILED → accept. CONTRADICTED → reject.
2. **New:** on NEUTRAL, run the table-row check over the **cited chunks only** (`bound_evidence`, the same text the bounded re-check may read). A match → accept.
3. Otherwise run the bounded NLI re-check (unchanged).

Running the check before the re-check saves 2–5 s of CPU NLI when it matches. It costs microseconds when it doesn't.

### The rule (all must hold, else no match)

1. **One percentage in the claim.**
   - The claim contains exactly one percentage (`40%`, `40 %`, `40 percent`, Arabic-Indic digits normalized) and no other number.
   - Claims without a percentage, or with several numbers, are out of scope and go to the re-check as today.
2. **Row binding.**
   - Find each occurrence of that exact percentage in a cited chunk.
   - For each occurrence, its **row label** is the text since the previous number or percentage in the chunk, whitespace and line breaks collapsed, with brackets and punctuation trimmed. This handles rows split across lines, such as `Final Exam \r\n(40%)`.
   - An empty label, or a label of more than 8 words, does not bind.
3. **Label in claim.**
   - Every content word of the row label (lower-cased, at least 2 letters, stopwords such as `and`, `of` and `the` excluded) appears in the claim.
   - Example: `Final Exam` → {final, exam}.
4. **Nothing else asserted.**
   - Remove from the claim:
     - the label's words;
     - the percentage;
     - words from the cited document titles and course code;
     - a fixed vocabulary of weight phrasing (`is, are, worth, weighs, weight, weighted, accounts, for, counts, makes, up, of, the, a, an, total, overall, grade, grading, mark, marks, score, course, project, component, part, final`, plus the course-code pattern).
   - **No content word may remain.** So "The final exam is worth 40% **and is optional**" leaves "optional" and does not match. "The final exam is **not** worth 40%" leaves "not" and does not match.
5. **Unique.**
   - Exactly one row in the cited chunks satisfies 2–4 for the claim's percentage.
   - Two rows matching (ambiguous) means no match.

### What it deliberately does not do

- **No Arabic claims in v1.** Labels are English table text, so an Arabic claim's words cannot match. Arabic claims keep today's path, unchanged. This is a known limitation, reported separately in the measurements.
- **Percentages only.** Dates, counts and other numbers keep today's path.
- **Abbreviated labels fail closed.** "Data cleaning is worth 15%" is true, but the row says "Data cleaning and preprocessing". The claim lacks "preprocessing", so the rule does not match and today's path decides. This is an accepted false negative: a refusal, never a wrong answer.
- **No new evidence.** It reads only the cited chunks, never other evidence, and it does not change citations.

### Traceability

The verifier's public result stays `ENTAILED` / `NOT_ENTAILED`, so no contract changes. An internal counter records how often the table rule decided. The evaluation tracer reads it, so the measurements show exactly which claims it accepted.

## Test plan (RED first)

**Unit tests:** pure function tests on the real extracted text of the two tables, quoted in the test file.

### Must accept

- "The final exam is worth 40% of the SE440 grade."
- "Machine learning implementation is worth 20% of the project grade."
- "The first exam accounts for 25% of the grade."
- "Report quality and explanation counts for 5%."
- The split row `Internal PSM Certification Exam (20%)`.

### Must reject (the wrong-claim set)

| Kind | Example |
|---|---|
| Wrong number | "The final exam is worth 25% of the SE440 grade." |
| Wrong row | "The second exam is worth 40%." |
| Swapped rows | "Machine learning implementation is worth 15%." |
| Extra assertion | "The final exam is worth 40% and is optional." |
| Negation | "The final exam is not worth 40%." |
| Ambiguous number | A claim whose label words fit two rows that carry the same percentage |
| Label partly present | "The exam is worth 40%." (the label word `final` is missing) |
| Two numbers | "The first and second exams are worth 25% each, 50% total." |
| Number not in the cited chunk | a correct label with a percentage that appears only in uncited evidence |
| Arabic claim | "الامتحان النهائي يساوي 40٪" stays on today's path (no match) |

**Verifier tests:**

- CONTRADICTED is never overridden.
- ENTAILED short-circuits as today.
- On NEUTRAL with a match: accept with no re-check inference spent.
- On NEUTRAL without a match: the re-check runs exactly as today.

## Evidence gate (all must hold)

1. **Frozen I4 manifest, 3-run trace** (`scripts/phase_i4/refusal_trace.py`), before and after:
   - unanswerable questions still refused 8/8 with 0 cited answers;
   - expected-page hits do not fall.
2. **Flip set** (5 cases × 5 runs, production settings):
   - `se440-weights` EN and `project-weights` EN are answered in at least 4 of 5 runs, against 3/5 and 2/5 before;
   - no other case gets worse.
3. **Repeatable-input check** (fixed IDs + generator at temperature 0, probe-only): the two cases pass 5/5. This proves the rule, not luck, accepts them.
4. **Every claim the table rule accepted** in runs 1–3 is listed in the results file and checked by hand against its row.
5. **Suites stay green:** the A–H selectors (95), the full suite, ruff and mypy.

**Stop rule:** if any wrong-claim test is accepted, or the unanswerable guard drops, stop. Do not tune the rule against the gate.

## Steps

1. Branch from `main`. Write the unit tests and the verifier tests (RED).
2. Implement the pure `table_row_supports(claim_text, chunks, titles)` function and the hook in `verify`.
3. Run the evidence gate, record the results in `docs/evaluations/`, and request owner acceptance.
4. Merge only after acceptance.

## Out of scope / follow-up

- Arabic table claims. This would need the label matched through the claim's English requirement or a translation step. A separate decision.
- `se440-makeup` (an S2 coverage capacity limit) and `project-weights` Arabic (retrieval rank 7) are unrelated failures and stay open.
