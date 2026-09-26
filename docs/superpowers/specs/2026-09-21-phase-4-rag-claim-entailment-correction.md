# Phase 4 RAG Claim-Entailment Correction

**Date:** 2026-09-21
**Status:** Corrective architecture amendment for implementation
**Scope:** Phase 4 local RAG semantic verification only

## 1. Purpose

This amendment corrects a semantic mismatch in the accepted Phase 4 local RAG architecture.

The existing architecture permits an atomic `Requirement` to remain an interrogative request such as:

`Who is the instructor for AI 495C Special topics in AI - Big Data Analytics?`

The existing independent entailment verifier then supplies that immutable
`Requirement.requirement_text` directly as the NLI hypothesis.

Natural-language-inference sequence classification requires a factual proposition that can be entailed, contradicted, or remain neutral with respect to a premise. An interrogative request is not such a proposition.

The production system can therefore falsely refuse a directly answerable question even when retrieval, evidence selection, provenance, and evidence-span selection are correct.

This amendment separates:

1. pre-generation requirement coverage; and
2. post-generation factual-claim entailment.

It does not relax grounding, provenance, citation, model-pinning, lifecycle, or fail-closed requirements.

## 2. Production evidence establishing the defect

The following real local query was executed against the configured production RAG corpus:

`Who is the instructor for AI 495C Special topics in AI - Big Data Analytics?`

The indexed authoritative source contains:

`Instructor: Prof. Sulieman Bani-Ahmad`

Observed retrieval behavior:

- dense hits: 9
- lexical hits: 9
- fused candidates: 9
- selected evidence: 4
- answer-bearing chunk: chunk 1
- chunk 1 dense rank: 1
- chunk 1 lexical rank: 1
- chunk 1 fused rank: 1
- deterministic sufficiency: `SUPPORTED`
- selected evidence count: 4
- selected evidence token count: 2201

Observed atomic semantic behavior:

- decomposition status: `OK`
- requirement count: 1
- selected span count: 1
- selected span belongs to chunk 1
- selected span contains the instructor label
- selected span contains `Sulieman Bani-Ahmad`

Using the current Phase 4 NLI contract:

- premise: correct selected evidence
- hypothesis: `Who is the instructor for AI 495C Special topics in AI - Big Data Analytics?`
- result: `NOT_ENTAILED`

Using the same premise with the declarative hypothesis:

`The instructor for AI 495C Special topics in AI - Big Data Analytics is Prof. Sulieman Bani-Ahmad.`

the pinned NLI classifier returned:

`ENTAILED`

The diagnostic modified no production data and `lms ps --json` returned `[]` afterward.


### 2.1 Post-correction live acceptance evidence

After the corrected atomic/coverage/claim-NLI composition was implemented,
the same real query progressed to post-generation claim verification.

The generated claim was:

`The instructor for the course AI 495C Special topics in AI - Big Data Analytics is Prof. Sulieman Bani-Ahmad.`

It cited exactly one trusted evidence ID.

That evidence resolved to the correct answer-bearing chunk, but the complete
chunk contained 3596 characters and the pinned mDeBERTa tokenizer produced
978 tokens for the premise/hypothesis pair. The approved NLI maximum is 512.

The classifier correctly failed closed with:

`ENTAILMENT_INPUT_OVERFLOW`

The failure therefore was not caused by multiple claim evidence IDs or by NLI
truncation. It established a second mismatch: post-generation verification
expanded a correctly cited evidence ID back to the entire chunk instead of
using the already validated exact request-local evidence span.

`lms ps --json` returned `[]` after the failed request, confirming cleanup.

## 3. Root cause

The Phase 4 contracts assign two incompatible meanings to `Requirement.requirement_text`.

The decomposition contract permits an independently answerable factual request to remain interrogative.

The NLI contract simultaneously treats that immutable requirement text as an NLI hypothesis.

Those contracts are incompatible for WH questions and other interrogative requests.

The problem is not caused by:

- BGE-M3 retrieval;
- lexical retrieval;
- reciprocal-rank fusion;
- evidence selection;
- evidence sufficiency;
- authoritative SQLite provenance;
- evidence segmentation;
- Qwen evidence-span selection;
- the pinned mDeBERTa model;
- NLI thresholds; or
- lifecycle cleanup.

No change to those components is justified by this defect.

## 4. Correct semantic responsibilities

Phase 4 semantic processing has two distinct responsibilities.

### 4.1 Pre-generation requirement coverage

Requirement coverage answers:

`Does the selected trusted evidence contain enough information to answer this requested portion?`

This stage runs before factual generation.

It may consume:

- the original question;
- immutable requirements;
- answer language;
- pinned generation identity;
- pinned content revision;
- selected trusted evidence.

It returns validated per-requirement support decisions.

Trusted code deterministically aggregates those decisions into:

- `SUPPORTED`
- `PARTIAL`
- `UNSUPPORTED`

The coverage model does not own aggregate status and does not generate factual answer claims.

### 4.2 Post-generation claim entailment

Independent NLI verification answers:

`Is this exact generated factual claim entailed by only the trusted evidence that the claim cites?`

This stage runs after `GroundedAnswerGenerator` produces `GroundedModelOutput`.

For each `RagClaim`:

- NLI hypothesis = immutable `RagClaim.text`;
- each claim `evidence_id` must first resolve to trusted request-local `RetrievedEvidence`;
- on the corrected atomic path, NLI premise = only validated exact
  `EvidenceSpan.quote` values associated with the claim's cited evidence IDs;
- validated spans are joined in deterministic requirement/span order;
- each exact quote contributes once at its first occurrence, while every cited
  evidence ID must still resolve to at least one validated span;
- whole chunk text must not replace validated spans on the corrected atomic path;
- premise and hypothesis are never reversed;
- unrelated selected evidence or spans are not appended;
- question text, metadata, explanations, outside knowledge, and source titles are not added.

`RagClaim.text` is therefore the proposition independently verified by NLI.

## 5. Corrected request path

The corrected Phase 4 text-RAG path is:

```text
RagQuery
  -> immutable query snapshot
  -> BGE-M3 dense retrieval
  -> lexical retrieval
  -> reciprocal-rank fusion
  -> authoritative evidence selection
  -> deterministic structural sufficiency
  -> atomic requirement decomposition
  -> request-local evidence segmentation
  -> per-requirement segment selection
  -> deterministic segment/provenance validation
  -> question-aware requirement coverage
  -> deterministic SUPPORTED/PARTIAL/UNSUPPORTED aggregation
  -> grounded factual generation when permitted
  -> GroundedModelOutput containing RagClaim values
  -> per-claim independent NLI verification
  -> deterministic post-NLI claim/status projection
  -> CitationValidator
  -> trusted rendering

The old production semantic rule:

Requirement.requirement_text
  -> NLI hypothesis

is superseded.

6. Requirement decomposition and evidence selection

AtomicRequirementDecomposer remains the decomposition boundary.

A requirement represents an independently answerable factual portion of the request.

A requirement is not required to be a declarative NLI proposition.

Interrogative requirements remain valid.

EvidenceSpanExtractor remains the per-requirement evidence-isolation boundary.

The following remain unchanged:

request-local deterministic segmentation;
opaque segment IDs;
exact-text resolution;
authoritative provenance validation;
repair limits;
fail-closed behavior.

Segment selection is not entailment.

7. Pre-generation requirement coverage

The existing requirement-coverage concept becomes the semantic answerability boundary for the atomic path.

Coverage receives:

canonical RequirementSet;
original question;
answer language;
pinned generation identity;
pinned content revision;
selected trusted evidence.

The classifier may decide only whether supplied evidence directly supports answering each requirement.

Trusted code owns deterministic aggregation.

A fully unsupported requirement universe prevents factual generation.

A partially supported requirement universe constrains generation to supported portions only.

Supported evidence IDs must resolve against the request-local selected evidence universe.

8. Grounded factual generation

GroundedAnswerGenerator remains the sole factual answer-generation boundary.

Every factual output claim must be represented as a RagClaim.

Each RagClaim contains:

claim ID;
declarative factual claim text;
one or more request-local evidence IDs.

Model-supplied evidence IDs remain untrusted references until trusted code validates them.

The model's free-form answer_text does not independently become trusted factual output.

9. Post-generation claim entailment

Every factual RagClaim must pass independent NLI verification before it can enter the trusted final answer.

For claim c:

premise =
    on the corrected atomic path, exact validated EvidenceSpan.quote values
    whose evidence_id is named by c.evidence_ids,
    joined in deterministic requirement/span order

hypothesis =
    c.text

The pinned classifier remains:

MoritzLaurer/mDeBERTa-v3-base-mnli-xnli

at the already approved revision and snapshot manifest.

Native argmax behavior remains unchanged:

native entailment -> ENTAILED
native neutral -> NOT_ENTAILED
native contradiction -> NOT_ENTAILED

No threshold adjustment, calibration offset, fallback model, Qwen fallback, cloud verifier, or second NLI model is introduced.

10. Claim evidence binding

A claim may be verified only against evidence explicitly named by its evidence_ids.

On the corrected atomic path, each cited evidence ID must also resolve to at
least one already validated request-local evidence span. The full chunk is not
a fallback when validated spans exist. This keeps the NLI premise bounded
without tokenizer truncation and preserves exact-source grounding.

Trusted code must reject:

unknown evidence IDs;
duplicate evidence IDs where forbidden by the existing contract;
evidence outside the pinned request snapshot;
generation/content-revision mismatch;
invalid provenance.

Unrelated evidence must not be silently added to improve entailment.

11. Deterministic post-NLI projection

NLI results are projected into trusted output deterministically.

All claims entailed

If every factual claim is ENTAILED:

retain all claims;
preserve the allowed answer status;
continue to citation validation.
Mixed results

If at least one claim is ENTAILED and at least one is NOT_ENTAILED:

discard non-entailed claims;
retain only entailed claims;
force status to PARTIAL_ANSWER;
reconstruct trusted answer content from surviving claims;
unsupported generated claim text must not survive rendering.
No claims entailed

If no factual claim is ENTAILED:

discard all factual claims;
produce trusted INSUFFICIENT_EVIDENCE;
expose no factual citations.

A model-proposed ANSWER may therefore be downgraded by trusted verification.

Trusted verification never upgrades unsupported material merely because the generator requested it.

12. Operational failures remain separate

The following remain typed operational failures and must not be converted to semantic non-support:

NLI snapshot unavailable or mismatched;
tokenizer/model initialization failure;
NLI input overflow;
NLI inference failure;
invalid label mapping;
invalid evidence reference;
provenance resolution failure;
request identity mismatch;
malformed structured generation output.

Semantic NOT_ENTAILED and operational failure remain distinct domains.

13. Citation validation

CitationValidator remains the deterministic citation/provenance trust boundary.

Post-generation NLI does not generate:

document IDs;
titles;
page numbers;
source offsets;
provenance kinds;
citation metadata.

Those remain derived exclusively from authoritative trusted evidence.

Only claims surviving post-generation NLI may reach final citation validation and rendering.

14. Unchanged model and lifecycle invariants

This amendment does not change approved model identities or device placement.

The following remain unchanged:

BGE-M3 performs embedding/retrieval;
Qwen performs requirement/coverage/generation work;
mDeBERTa performs independent NLI;
mDeBERTa remains CPU-only;
no new model download is authorized;
BGE/Qwen residency constraints remain enforced;
LM Studio lifecycle ownership remains unchanged;
cleanup remains deterministic and idempotent;
no cloud fallback is introduced.
15. Unchanged retrieval and persistence invariants

This amendment introduces no change to:

SQLite authority;
dense-index authority;
generation identity;
content revision semantics;
document versions;
source spans;
chunk exact text;
request-local evidence segments;
hybrid retrieval;
selector budgets;
sufficiency thresholds;
indexing;
Moodle ingestion;
document ingestion.

No database migration is required.

16. Required TDD regression coverage

Implementation begins with failing tests.

Required coverage includes:

interrogative requirements are not passed directly to NLI as hypotheses;
question-aware requirement coverage occurs before factual generation;
generated RagClaim.text becomes the NLI hypothesis;
claim NLI receives only evidence bound through that claim's evidence_ids;
all claims entailed preserves a full factual answer;
mixed entailment removes unsupported claims and produces PARTIAL_ANSWER;
zero entailed claims produces INSUFFICIENT_EVIDENCE;
invalid claim evidence references fail closed;
NLI operational failures remain operational failures;
citation validation receives only surviving verified claims;
unsupported generated claim text cannot survive trusted rendering;
existing retrieval, provenance, segment, lifecycle, and cleanup tests remain green.

Tests use deterministic fakes except for separately authorized local-model acceptance probes.

17. Real local acceptance

After focused and broad automated gates pass, rerun:

Who is the instructor for AI 495C Special topics in AI - Big Data Analytics?

against the already indexed local corpus.

Acceptance target:

answer-bearing chunk retrieval: PASS
requirement coverage: SUPPORTED
generated claim identifies Prof. Sulieman Bani-Ahmad
claim NLI: ENTAILED
public status: ANSWER
citation/provenance validation: PASS
cleanup: PASS
lms ps after request: []

This does not authorize Moodle writes, document mutation, reindexing, model downloads, database migration, or external provider writes.

18. Superseded Phase 4 rule

The earlier Phase 4 rule:

hypothesis = immutable requirement text

is superseded for production factual verification.

Replacement:

pre-generation:
    question-aware requirement coverage determines answerability

post-generation:
    hypothesis = immutable RagClaim.text
    premise = only trusted evidence bound to RagClaim.evidence_ids

All unaffected Phase 4 architectural rules remain in force.

19. Implementation boundary

Expected implementation surface is limited primarily to:

src/aaos/rag/service.py
src/aaos/rag/runtime.py
narrowly required semantic-verification contracts/helpers
focused RAG tests

Changes to retrieval, indexing, Moodle integration, database schema, document ingestion, model pins, or citation provenance are outside this correction unless new evidence demonstrates an independent defect.

Implementation follows:

RED -> minimum GREEN -> focused regression -> broad regression -> real local acceptance

No production Python change should precede a RED regression demonstrating the corrected semantic boundary.
