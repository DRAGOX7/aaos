# Phase 4 — Local RAG Design Specification

Status: human-approved architecture design; Phase 4 is **PLANNED / NOT STARTED**.

This document is a design specification only. It defines the implementation
boundary for local, citation-grounded retrieval and answer generation over
authorized academic document content. It does not authorize implementation,
dependency changes, migrations, live Moodle access, or a Phase 4 freeze tag.

## 1. Status and purpose

Phase 4 adds a local RAG capability that answers independently submitted
questions from locally ingested academic document content. Its complete flow is:

```text
question
  -> explicit scope resolution
  -> hybrid retrieval
  -> deterministic evidence selection
  -> deterministic structural sufficiency gate
  -> atomic requirement decomposition (question/context only)
  -> deterministic canonical requirement set
  -> extractive evidence-span identification (selected evidence only)
  -> deterministic span/provenance validation
  -> independent per-requirement entailment verification
  -> deterministic support aggregation
       -> UNSUPPORTED: deterministic INSUFFICIENT_EVIDENCE
       -> PARTIAL: grounded answer under a partial-support constraint
       -> SUPPORTED: grounded answer
  -> existing local ModelGateway for factual answer generation
  -> typed grounded answer
  -> claim-level citation validation
  -> deterministic rendering
```

The capability is local-first, stateless, read-only with respect to Moodle, and
fail-closed. Phase 4 includes final grounded LLM answer generation. It does not
introduce an agent loop, conversation state, tools, or external evaluation.

## 2. Context and frozen prerequisites

The repository records these evidence-qualified prerequisites:

- Phase 0 acceptance remains unresolved; this specification does not alter that
  governance status.
- Phase 1 is frozen at `phase-01-passed` /
  `8728aee00f4d6cddc8d1cc9b8f1d41a1c4684fef`.
- Phase 2 is accepted, not frozen, at
  `736c8498ab1fe2c1d4d425ba4625774eaf791e93`.
- Phase 3 is frozen at `phase-03-passed` /
  `12a27f7a77ed79a3e638d9fc6bf4509840a9b326`.
- The current repository checkpoint is `56d0777e41bf17a0baee40d23b4227a89883591c`;
  the working-tree `reflex` path is unrelated and must remain untouched.

Phase 4 starts only after explicit human approval of this design, a separate
implementation plan, and the repository's active-phase gate. Implementation
must consume the frozen Phase 3 contracts rather than modify them.

Current Phase 3 contracts are the source of truth for document content and
provenance: `Document`, `DocumentVersion`, `DocumentPage`, `ContentBlob`, and
their joins to the Phase 1 `RemoteFile` and `Course` identities. `DocumentPage`
text is the authoritative extracted text; RAG records are derived state.

## 3. Goals

Phase 4 must:

1. index only authorized local document content;
2. preserve exact page and source provenance through every derived record;
3. support explicit course, global, and historical scopes;
4. provide deterministic lexical, dense, and hybrid retrieval;
5. refuse clearly unsupported questions before generation;
6. produce grounded answers whose factual claims cite supplied evidence;
7. support English and Arabic cross-lingual retrieval and answering;
8. rebuild derived indexes without changing Phase 3 source truth; and
9. provide reproducible deterministic, IR, automated, multilingual, security,
   performance, and human evaluation evidence.

## 4. Non-goals

Phase 4 does not modify Phase 3 ingestion or Moodle behavior, perform OCR or
multimodal/VLM processing, expose a public RAG HTTP API, run a background
scheduler, ingest arbitrary filesystem or URL content, or write to Moodle.
It does not implement an agent loop, conversation memory, historical query
rewriting, typed action tools, approvals, dashboards, Google Calendar, GitHub
MCP, a reranker in v1, a runtime LLM citation judge, or automatic retired-index
purging. It does not index assignments, announcements, or other structured
academic records. It does not silently fall back to lexical-only retrieval or
to pretrained model knowledge.

## 5. Trust model and invariants

Trusted AAOS code controls scope resolution, corpus membership, chunking,
retrieval, evidence budgets, sufficiency, prompt construction, output parsing,
citation validation, and rendering. Document text, model output, retrieved
content, and environment observations are untrusted data.

The following are absolute invariants:

- normal query snapshots authorize only READY indexes selected from the
  appropriate ACTIVE generation; an already-created snapshot may continue to
  retrieve its frozen READY universe after that generation becomes RETIRED;
- every retrieved chunk resolves to a Phase 3 document version, page, and
  provenance identity through SQLite;
- a query pins generation, content revision, and scope for its entire lifetime;
- the generator sees only the question, bounded selected evidence, and fixed
  instructions;
- model output cannot request tools, network access, files, or another source;
- every factual claim has valid citation(s) from the supplied evidence IDs;
- no unseen citation ID, fabricated source, or structurally uncited factual claim
  is accepted;
- structural insufficiency and deterministically aggregated semantic
  `UNSUPPORTED` prevent any factual answer-generation request; atomic
  decomposition, span extraction, and entailment outputs remain untrusted
  model data and cannot override code-owned support; and
- all failures are represented by fixed safe categories without raw content.

## 6. End-to-end architecture

The proposed composition is:

```text
RagQuery (question + RagScope)
  -> ScopeResolver
  -> ActiveGenerationSnapshot
  -> SQLite corpus eligibility
  -> DenseIndex.search + SQLite FTS5/BM25 search
  -> deterministic RRF fusion
  -> EvidenceSelector
  -> StructuralSufficiencyGate
       -> structural INSUFFICIENT_EVIDENCE
  -> AtomicRequirementDecomposer (local Qwen; question/context only)
  -> deterministic canonical requirement set
  -> EvidenceSpanExtractor (local Qwen; fixed requirements + selected evidence)
  -> deterministic span and provenance validation
  -> IndependentEntailmentVerifier (one atomic requirement at a time)
  -> deterministic per-requirement support and status aggregation
       -> UNSUPPORTED -> INSUFFICIENT_EVIDENCE
       -> PARTIAL -> GroundedAnswerGenerator under PARTIAL constraint
       -> SUPPORTED -> GroundedAnswerGenerator
  -> strict RagAnswer parser
  -> CitationValidator
  -> safe renderer
```

An explicit Phase 4 runtime composes SQLite repositories, FTS5, a generation-
scoped `DenseIndex`, an `EmbeddingService`, and a grounded-answer adapter. The
runtime owns and closes these resources deterministically; imports have no
database, Chroma, model, or network side effects.

## 7. Phase boundaries

### Phase 1

Phase 1 remains the owner of canonical `Course`, `Assignment`, `Announcement`,
and `RemoteFile` identity and synchronization. Phase 4 may use course and
remote-file provenance for citations and scope resolution, but does not alter
normalization or synchronization.

### Phase 2

Phase 2 remains metadata-only, browser-policy governed, and read-only. Phase 4
does not use browser state, Moodle APIs, cookies, or session storage.

### Phase 3

Phase 3 remains frozen and owns source PDF bytes, document versions, page text,
and extraction status. Phase 4 reads those records and creates rebuildable
derived retrieval state only.

### Phase 5

Phase 5 owns orchestration, task/conversation state, follow-ups, typed academic
tools, approvals, and action selection. Phase 4 exposes a bounded query service;
it does not become an agent or tool registry.

## 8. Persistence model

SQLite is authoritative for the Phase 4 relational state. Proposed derived
records are:

- `RagIndexGeneration`: generation status and immutable configuration metadata;
- `RagDocumentIndex`: one generation/document-version indexing state;
- `RagChunk`: exact derived chunk text, identity, order, and page bounds;
- `RagChunkSourceSpan`: exact chunk-to-page character spans; and
- an FTS5 virtual table plus derived lexical-normalized fields.

`RagChunk` retains the exact text passed to retrieval and generation; the
lexical index is derived and may contain a normalized search representation.
Foreign keys resolve document versions and pages, and uniqueness prevents
duplicate generation/document/chunk identities. Phase 4 does not store PDF
bytes again and does not replace Phase 3 provenance.

## 9. Index generations and content revisions

`RagIndexGeneration` has the conceptual fields:

```text
id, status, config_fingerprint, chunker_version, chunking_config,
lexical_normalizer_version, embedding_model, embedding_revision,
embedding_dimensions, retrieval_config, content_revision,
created_at, activated_at
```

Statuses are `BUILDING`, `ACTIVE`, and `RETIRED`. At most one generation is
ACTIVE. A BUILDING generation is invisible to queries. A successful activation
atomically changes the old ACTIVE generation to RETIRED and the new generation
to ACTIVE. A failed build leaves the old ACTIVE generation unchanged.

Retired generations remain available for explicit historical queries and for
queries that pinned them while they were ACTIVE. They are not automatically
purged in v1. Preserving retired generation data is therefore part of the
in-flight query-lifetime contract, not only a debugging or history feature.

`rag index-new` identifies eligible Phase 3 `DocumentVersion` records not yet
successfully published in the active generation. It adds newer versions without
deleting older historical versions. For an ACTIVE generation, a published
document version is one whose `RagDocumentIndex` has the terminal state `READY`
or `NOT_SEARCHABLE`; `PREPARING` and `FAILED` are unpublished. A newer
unpublished version does not hide the latest successfully published version.
Explicit historical scope can still select an older published version. A
successful active-generation update increments `content_revision`.

## 10. Content-revision semantics

`content_revision` identifies the indexed corpus under a given generation's
retrieval configuration. It changes when the represented document-version
corpus changes, even if chunking, models, and retrieval settings do not. A
configuration change that is incompatible with existing derived state requires
a new generation and fingerprint.

Incremental publication against an ACTIVE generation is one short SQLite
transaction. It validates every staged `PREPARING` document, changes all staged
rows to their intended `READY` or `NOT_SEARCHABLE` terminal states, and performs
the guarded `content_revision` compare-and-swap from `R` to `R+1`. Those state
transitions and the revision increment commit or roll back together. A no-op or
failed batch leaves the revision unchanged. Publishing a newer
`NOT_SEARCHABLE` version is a corpus change because it supersedes the prior
current version even though it contributes no searchable chunks.

Phase 4 v1 does not add a per-document publication-revision column. SQLite
transaction atomicity provides the before/after publication boundary, and a
query freezes both the revision and its authorized document-version universe in
one read snapshot.

That immutable snapshot is a durable capability for the lifetime of the query,
not an expiring lease on mutable publication state. After successful snapshot
creation, a later `content_revision` increment does not invalidate the frozen
authorized universe. The pinned revision remains required for reproducibility,
diagnostics, and evaluation, but retrieval integrity checks do not require the
repository's current revision to remain equal to it.

Every query, evaluation result, and acceptance artifact records both
`generation_id` and `content_revision`. Formal evaluations also record a
canonical corpus manifest/fingerprint so that a metric cannot be interpreted
without knowing exactly which document versions were present.

## 11. Chunking and provenance

Chunking is a pure deterministic transformation of ordered Phase 3 pages and a
validated chunking configuration. It is page-, heading-, and paragraph-aware;
ordinary text is not split by blind fixed windows. A long natural section may
use a small overlap only at a split boundary.

The initial design target is approximately 500 tokens, with a practical
preferred range of roughly 450–650 and a hard maximum near 700. Final tuned
values are configuration recorded in the generation fingerprint and locked
before held-out evaluation.

Conceptual `RagChunk` fields are:

```text
internal_id, chunk_key, index_generation_id, document_version_id, ordinal,
exact_text, heading?, token_count, text_sha256, page_start, page_end
```

`RagChunkSourceSpan` records `rag_chunk_id`, `document_page_id`, page number,
source character start/end, and chunk character start/end. A multi-page chunk
therefore retains each page contribution exactly, while a user citation may
render as a compact page range.

The deterministic `chunk_key` is the SHA-256 of a canonical, sorted-field
serialization containing the configuration fingerprint, document-version
identity, ordinal, ordered span identities, and exact chunk-text hash. Canonical
serialization uses explicit field names and UTF-8 JSON with sorted keys and
fixed separators; Python `repr` and unordered serialization are forbidden.
Identical text in different document versions has different identity because
provenance participates.

Pages with no usable extracted text are skipped for text indexing. A document
index records `FULL`, `PARTIAL`, or `NONE` coverage. Zero searchable pages are
`NOT_SEARCHABLE` with `NONE`, not an indexing exception. OCR is not added.

## 12. SQLite and FTS5 architecture

SQLite remains the source of truth for chunk text, provenance, generation
membership, and index state. FTS5/BM25 supplies lexical candidates. The
lexical normalizer is deterministic, Unicode-aware, whitespace-normalizing,
and conservatively case-normalizing, with a version recorded in the generation
fingerprint. Arabic normalization is conservative; aggressive stemming is not
part of v1.

Exact technical tokens such as `BERT`, `YOLOv11`, `mAP50-95`, `CNN`, `SAHI`,
and `DBSCAN` must remain searchable. FTS rows contain stable chunk identities
that resolve back to authoritative SQLite rows before evidence is trusted.

Lexical query compilation is deterministic and literal. After the existing
Unicode-aware normalization, empty tokens are discarded and repeated tokens
are deduplicated in stable order. Each remaining token is quoted and escaped
as one literal FTS5 term, then the terms are joined with explicit `OR`
semantics. An empty usable-token query returns no lexical candidates safely.
User text is never executable FTS syntax: operators, column filters, `NEAR`,
wildcards, parentheses, quotes, and malformed punctuation remain data inside
literal terms. There is no query rewrite, semantic expansion, stemming,
translation, or hidden lexical fallback in this policy.

## 13. Chroma architecture

Chroma is an embedded/local persistent derived index. No Chroma server,
network service, Docker service, or remote vector store is required. A separate
collection/storage area is created for each trusted generation identity, under a
programmatically constructed root such as:

```text
data/rag/chroma/generation-<trusted-id>/
```

Callers cannot supply an arbitrary Chroma path or collection name. Chroma never
becomes authoritative for text, provenance, or citations; every candidate is
resolved through SQLite. Retired-generation data is retained in v1.

The domain depends on an internal abstraction:

```text
DenseIndex
  upsert(records)
  search(vector, limit)
  contains(ids)
  count()
  close()

DenseIndexFactory.open(generation_identity)
```

`ChromaDenseIndex` is the implementation adapter. Chroma-specific types and
imports do not escape the infrastructure boundary.

## 14. BGE-M3 embedding lifecycle

BGE-M3 is the approved multilingual embedding model. `EmbeddingService` owns
model identifier, revision, dimensions, device, batch settings, and lifecycle:

```text
embed_documents(texts)
embed_query(question)
model_metadata()
close()
```

Index builds may batch on the GPU and persist vectors before releasing model
resources. A query embeds once, retrieves evidence, and releases embedding
resources before answer generation so the embedding model and answer model do
not need to remain resident together on the target limited-VRAM machine.

Object deletion alone is not proof of GPU release. Implementation must measure
or otherwise verify resource release. If in-process release is unreliable, the
same `EmbeddingService` contract may be implemented by a short-lived local
worker; retrieval code must not change for that choice.

## 15. Query and scoping contracts

`RagQuery` is stateless and independently understandable. Scope is explicit and
typed rather than using an ambiguous nullable course identifier:

```text
CourseScope(course_id)
GlobalScope()
HistoricalScope(document_version_ids)
```

The normal/default scope is `CourseScope`. `GlobalScope` requires an explicit
request. `HistoricalScope` names selected immutable document-version identities.
Scope validation occurs before ranking and rejects empty, malformed, duplicate,
or unauthorized identities. Phase 4 does not infer scope from prior questions,
pronouns, chat history, or model guesses.

## 16. Authorized retrieval universe

At query start, trusted code resolves:

```text
ACTIVE generation
  -> pinned content_revision
  -> explicit current or historical version scope
  -> latest published READY/NOT_SEARCHABLE version per logical resource
  -> searchable READY DocumentVersion IDs
  -> valid Phase 3 provenance
```

Generation identity, `content_revision`, requested scope, latest-published
selection, and final authorized `DocumentVersion` IDs are resolved in one
consistent SQLite read transaction and stored in an immutable `QuerySnapshot`.
Both dense and lexical searches receive those frozen IDs. They must not
re-resolve current versions after the snapshot or retrieve globally and filter
obsolete or out-of-scope versions afterward.

The transaction that creates `QuerySnapshot` is short and closes before query
embedding, dense search, lexical search, fusion, evidence selection, or model
generation. Once created successfully, the snapshot remains the complete
freshness and authorization decision for that query. Later publication must not
trigger another ACTIVE-generation lookup, latest-version resolution, scope
resolution, or current-revision equality check.

For normal current scope, the latest published row is the newest `READY` or
`NOT_SEARCHABLE` version for the logical resource in the pinned ACTIVE
generation. A latest published `READY` version contributes its ID to the
searchable universe. A latest published `NOT_SEARCHABLE` version contributes no
ID and does not fall back to an older `READY` version. Newer `PREPARING` or
`FAILED` versions are ignored for publication visibility, so an older published
version remains current until a replacement is atomically published. Historical
versions remain preserved and are excluded unless explicitly requested.

## 17. Hybrid retrieval and RRF

Retrieval combines BGE-M3 dense candidates with SQLite FTS5/BM25 candidates.
The question's original semantic text is used for dense embedding, while the
deterministic literal-term compiler above is used for lexical search. English,
Arabic, and mixed-language tokens follow the same policy; no English-only
stopword removal is applied. Automatic translation before dense retrieval is
not required.

Candidate depths, RRF constant, and related settings are configuration. The
lexical candidate depth remains 30 and the RRF constant remains `k=60`.
Scores from dense and BM25 retrieval are never compared directly. Reciprocal
Rank Fusion combines ranks deterministically, with stable chunk-key
tie-breaking. A hybrid query may benefit from either channel, but no reranker
or dynamic channel switch is introduced.
No reranker is included in v1; added complexity must later be justified by
evaluation evidence.

If the dense index or embedding service is unavailable, production retrieval
returns an explicit safe failure. It does not silently become BM25-only.

`DenseIndexFactory.open(generation_identity)` is the generation-capable dense
boundary. `HybridRetriever` receives that factory, not a dense handle opened for
the generation that happened to be ACTIVE when the runtime was constructed. For
each query it first resolves the immutable `QuerySnapshot`, opens a dense handle
for exactly `snapshot.generation`, searches it with only
`snapshot.document_version_ids`, and closes that query-owned handle in a
`finally` boundary. The factory remains generation-independent; it may retain
only the adapter's internal bookkeeping needed by its existing `remove()`
contract. A query never uses a runtime-global ACTIVE-generation handle, and a
G1 snapshot therefore cannot receive G2 candidates. BUILDING generations are
rejected by snapshot authorization/materialization before dense results can be
accepted. Explicit historical snapshots use the corresponding RETIRED
generation's dense state without requiring runtime reconstruction.

After RRF deduplication, the repository performs snapshot-safe authoritative
materialization through this public boundary:

```python
@dataclass(frozen=True, slots=True)
class AuthoritativeRetrievalChunk:
    chunk: ChunkRecord
    document_version: DocumentVersionRecord
    source_spans: tuple[SourceSpanRecord, ...]


RagRepository.materialize_snapshot_chunks(
    snapshot: QuerySnapshot,
    chunk_keys: tuple[str, ...],
) -> tuple[AuthoritativeRetrievalChunk, ...]
```

`QuerySnapshot` and the returned repository record types remain defined in the
repository module, so this interface introduces no repository/retrieval import
cycle. The method uses one short read-only SQLite transaction for the complete
requested key tuple and returns values in exactly the requested key order.
Duplicate, malformed, missing, or ambiguously resolved keys fail with
`RETRIEVAL_INCONSISTENCY`; persistence/integrity failures are translated to the
same safe code without exposing backend text. An empty requested tuple returns
an empty tuple without broadening or re-resolving the snapshot.

Inside that transaction, SQLite verifies that the pinned generation row still
exists with the snapshot's generation ID, configuration fingerprint, and
embedding dimensions, is ACTIVE or RETIRED but never BUILDING, and that every
requested chunk belongs to that generation and to one of the snapshot's frozen
authorized document-version IDs. The row's now-current revision is deliberately
not compared with the snapshot revision. Each candidate must resolve to exactly
one authoritative `RagChunk`, a READY
`RagDocumentIndex` for that pinned generation, its generation-scoped FTS row,
its Phase 3 document-version identity, and structurally valid ordered source
spans whose page and character relationships agree with the authoritative
chunk text. Chroma and caller-supplied candidate metadata cannot authorize any
of those facts.

Materialization is an integrity check, not freshness re-authorization. It does
not require the pinned generation to remain ACTIVE, compare the repository's
current `content_revision` with the snapshot revision, ask whether an authorized
version is still latest, or re-resolve scope. Thus an R-pinned query may finish
after R+1 publication, and a G1-pinned query may finish after G1 is RETIRED and
G2 becomes ACTIVE, while G2 chunks and document versions outside the frozen
snapshot remain forbidden.

The same durable-capability rule applies to snapshot-scoped FTS retrieval before
fusion: it queries only the pinned generation ID and frozen document-version
IDs, validates authoritative READY/FTS relationships, and does not compare the
mutable current revision or require the generation to remain ACTIVE.

## 18. Evidence selection

RRF output is a candidate set, not an automatic prompt. A deterministic
`EvidenceSelector` removes duplicate logical evidence, limits excessive
neighbor overlap, preserves useful source diversity where it improves support,
and enforces both a chunk-count budget and a token budget. Relevance remains
primary; diversity is not forced artificially.

An initial engineering expectation is roughly 4–8 chunks, subject to evaluation
and context limits. Selection output is immutable for the query and carries
the generation and content revision pinned at query start.

## 19. Sufficiency gates

The first gate is deterministic and conservative. The structural gate considers
retrieval coverage, rank/fusion evidence, scope, evidence identity, and the
labeled threshold policy. It is responsible for authorization, integrity, and
minimum structural evidence conditions; it never asks a model to decide whether
evidence is sufficient. A structural failure returns
`INSUFFICIENT_EVIDENCE` and skips both semantic classification and factual
answer generation.

For structurally eligible evidence, the amended semantic path first runs a
question-only atomic requirement decomposer. Deterministic code assigns
canonical IDs and freezes that set. An extractive span step then proposes
bounded quotes tied to those fixed requirements and selected evidence;
deterministic code verifies request-local IDs, exact quote occurrence, pinned
snapshot membership, and authoritative SQLite provenance. Only validated
spans reach an independent entailment verifier, one atomic requirement at a
time. Its narrow `ENTAILED`/`NOT_ENTAILED` result is untrusted until
deterministic validation. Code maps validated entailment to per-requirement
support and aggregates zero/all/mixed support to `UNSUPPORTED`/`SUPPORTED`/
`PARTIAL`.
`UNSUPPORTED` maps to deterministic `INSUFFICIENT_EVIDENCE` and skips factual
answer generation; `PARTIAL` enters grounded generation with an explicit
partial-support constraint; `SUPPORTED` enters the normal grounded generation
path. The model does not own the final aggregate status.

For each proposed span, the model must bind the `evidence_id` to the same
selected evidence record whose supplied text contains the quote. Quotes must
be copied verbatim from that record: no paraphrase, summary, rewriting,
completion, normalization by the model, or composition across evidence
records is permitted. If no exact suitable text exists for a requirement, the
model returns `spans=[]` for that requirement while still returning its
required ID entry. The single bounded repair repeats this same-record,
verbatim-only contract. Deterministic validation remains authoritative and
never silently rebinds a quote to another evidence ID or accepts semantic
similarity.

Structural thresholds remain calibrated on development data and locked before
held-out acceptance. Atomic decomposition, span extraction, entailment, and
aggregation are separate local-only gates. Calibration reports omitted,
spurious, over-split, and under-split requirements; span
validation/provenance failures; entailment false acceptance (especially
topical-only or partial-as-full evidence); and `SUPPORTED`, `PARTIAL`, and
`UNSUPPORTED` confusion, including false support, false refusal, and
partial-to-supported or partial-to-unsupported errors.
Neither gate permits
pretrained-knowledge or unbounded retrieval fallback. Unsupported false
acceptance remains weighted as more serious than a false refusal.

Conflicting evidence is preserved rather than silently resolved. The generator
may state both supported positions with distinct citations unless a trusted
source relationship supplies precedence.

## 20. Current and historical semantics

Current retrieval selects the latest eligible version for each logical document
within the pinned corpus and scope. It must never mix old and new versions of a
document in a normal answer. Explicit historical scope may select prior
versions, including for comparison. Historical data is preserved for audit and
reproducible evaluation, not silently promoted into current answers.

## 21. Grounded generation boundary

The existing `ModelGateway` remains the local model boundary. Its current
model-independent contract is `generate(ChatRequest) -> ChatResponse`, with
validated `ChatMessage`, `TokenUsage`, and response fields; the configured
`LMStudioGateway` talks only to the validated loopback LM Studio endpoint with
redirects and proxy environment disabled.

Phase 4 composes a dedicated grounded-answer adapter over that gateway. Retrieval
does not call a raw gateway or construct arbitrary model requests. The adapter
accepts a typed request containing the original question, answer-language
policy, and bounded `RetrievedEvidence`, then emits a strict structured result.

The prompt labels all document/evidence text as untrusted data and grants no
tool, filesystem, network, source-selection, or policy authority to it. Only
the supplied evidence IDs may be cited. No model call receives credentials,
browser state, arbitrary URLs, or an unbounded corpus.

The requirement-level semantic stage is a separate typed adapter over the same
approved local model boundary. After structural evidence checks,
`AtomicRequirementDecomposer` receives only the original question and relevant
language/context. `EvidenceSpanExtractor` receives the immutable requirement
set plus bounded selected evidence, and `IndependentEntailmentVerifier`
receives one requirement plus only its deterministically validated spans.
Deterministic code validates every requirement, evidence ID, quote, provenance
link, and entailment result before aggregating internal support. These calls
are never a second retrieval path and never replace the grounded factual-answer
adapter. Semantic outputs are not factual-answer content and cannot be rendered
to the user.

## 22. Typed `RagAnswer`

The public result is a validated immutable value, conceptually:

```text
RagAnswer
  status: ANSWER | PARTIAL_ANSWER | INSUFFICIENT_EVIDENCE
  answer_text: non-blank for ANSWER or PARTIAL_ANSWER
  language: validated language code
  citations: ordered claim/page citations
  generation_id
  content_revision
```

`ANSWER` means the requested factual substance is supported by the supplied
evidence and every factual academic claim satisfies the citation contract.
`PARTIAL_ANSWER` means some requested substance is supported; supported
portions may be returned with valid claim-level citations, while unsupported
requested portions must be explicitly identified as unsupported and never
invented. `INSUFFICIENT_EVIDENCE` means no useful factual answer can be
grounded; no factual answer generation or pretrained-knowledge fallback occurs.
The atomic decomposition, span, and entailment steps may have run before this
status was selected, but their outputs and aggregate are not themselves
user-facing factual content.

Operational failures are not public answer-status variants. They remain typed
error outcomes such as `MODEL_UNAVAILABLE`, `MODEL_TIMEOUT`,
`INVALID_STRUCTURED_OUTPUT`, `INVALID_EVIDENCE_REFERENCE`, `UNCITED_CLAIM`,
and `PROVENANCE_RESOLUTION_FAILED`. Every successful answer status requires a
non-blank answer, valid language, pinned generation/revision, and citation
validation. The model identifier may be retained as non-secret metadata for
evaluation, but raw prompts, evidence text, and raw model output are not normal
CLI/log output.

## 23. Claim-level citation contract

Generation output is parsed into claims or claim-bearing answer segments. Each
factual claim must reference one or more query-local evidence IDs supplied in
the prompt. The deterministic validator rejects unseen IDs, malformed IDs,
citations whose evidence does not resolve through SQLite, and structurally
uncited factual claims. Citation page spans derive from `RagChunkSourceSpan`,
not from model-provided page numbers.

The renderer emits only validated answer text plus deterministic citations. It
never accepts a model-invented source, URL, document path, or evidence excerpt.

## 24. Prompt-injection defenses

Stored documents and retrieved chunks are untrusted data. Instructions such as
“ignore previous instructions”, “reveal other documents”, “call this API”, or
“execute this command” remain text to be quoted or ignored; they cannot change
system policy, scope, citations, tools, or source access. No Phase 4 generation
path exposes a tool registry, shell, browser, credentials, or network client.

Adversarial fixtures are required in evaluation. A passing result demonstrates
that the answer remains bounded by supplied evidence and that no hidden-secret
or unauthorized-source behavior occurs.

## 25. Runtime and service boundaries

Phase 4 uses explicit composition and deterministic lifecycle ownership. A
runtime owns its SQLite session/engine, FTS access, generation-independent
`DenseIndexFactory`, embedding service, and grounded gateway adapter, and
supports idempotent `close()` plus a context manager. Services do not own
resources created by their caller. Generation-specific dense handles are
query-owned by `HybridRetriever` for the duration of one retrieval and are not
closed again by the runtime. The injected `ModelGateway` is the sole answer
boundary; the composition root must retain an explicit close/async-close
obligation for any gateway implementation that owns a client, while the
runtime owns the grounded adapter that invokes it.

Runtime construction does not resolve or require an ACTIVE generation merely to
bind retrieval. It constructs the repository, embedding infrastructure, dense
factory, lexical/retrieval services, grounded adapter, and facade without
opening a generation-specific dense handle. Query execution resolves
`snapshot.generation` and obtains the matching dense state through the factory.
Thus one long-lived runtime can serve a G1 snapshot after G1 is RETIRED, serve a
later G2 snapshot after activation without restart, and serve an explicitly
authorized historical RETIRED snapshot. BUILDING generations remain
query-invisible.

Construction uses an explicit ownership ledger: each successfully acquired
resource is registered immediately, and a failed build closes all registered
resources exactly once in reverse acquisition order. Cleanup attempts every
resource even after one close fails. The original construction failure remains
primary, while any cleanup failure is surfaced only through the existing
sanitized `RagOperationalError` boundary (for example,
`DENSE_INDEX_UNAVAILABLE`); raw backend exceptions, paths, SQL, model-cache
details, and secrets are never rendered or logged. A successfully built runtime
uses the same deterministic, idempotent close rule and never double-closes a
query-owned dense handle.

No import or default application startup creates a database, Chroma directory,
index, model, migration, or LM Studio connection. No Phase 4 module imports
Moodle browser/authentication code. Test and acceptance composition injects
disposable SQLite and Chroma roots. BGE-M3 remains lazy at construction, keeps
the approved identity, and releases material embedding resources before any
answer-model generation.

## 26. CLI surface

Phase 4 exposes narrow, explicit commands rather than a generic authenticated
Moodle or RAG API:

```text
aaos rag index-new
aaos rag rebuild
aaos rag status
aaos rag ask --course-id <internal-course-id>
aaos rag ask --global
aaos rag ask --document-version-id <internal-document-version-id>
```

The three `ask` scopes are mutually exclusive: course, global, or historical
document version. There is no ambiguous null scope. After parsing the explicit
scope, the question is read interactively (stdin/console); ordinary academic
question text is not required in a command-line argument. Exact input handling
and prompt termination are implementation details, but there is no arbitrary
path, URL, file, `moodle-cmid`, SQL, model prompt, tool, or function selector.
CLI output is fixed and privacy-safe: status, bounded answer text when
authorized, citations, scope identity, generation/revision, and safe error
codes. It never prints credentials, browser state, raw prompts, raw evidence,
raw JSON, or unrestricted model output.

## 27. Concurrency and locking

Only one Phase 4 index writer may operate at a time. `rag index-new` and
`rag rebuild` are mutually exclusive and fail before work if the active write
lease is unavailable.

`index-new` uses an exclusive active-generation write lease and publishes a new
content revision atomically after integrity verification. It acquires exactly
one outer lease for candidate discovery, serial document staging, and final
publication. Lease-aware staging validates that same live owner capability and
does not reacquire the non-reentrant lock. The lock remains nonce- and
file-identity-bound, non-reentrant, and fail-closed for released, foreign, or
replaced leases. Queries may pause or fail safely during publication. `rebuild`
writes a separate BUILDING generation while the old ACTIVE generation serves
queries, then activates it in one state transition.

Each query pins generation, content revision, and scope before candidate
retrieval. Activation during a query does not change that query's corpus;
subsequent queries use the new active snapshot. A query already pinned to the
old generation may complete after activation from preserved RETIRED data; it
does not require the old generation to remain ACTIVE and cannot admit candidates
from the replacement generation.

## 28. Cross-store failure and recovery

SQLite and Chroma are not a distributed transaction. Per-document preparation
has two explicit modes.

For a BUILDING generation, standalone preparation owns a writer lease and may
finish one document as `READY` or `NOT_SEARCHABLE`; the generation remains
invisible until Task 13 activation. For ACTIVE-generation `index-new`,
lease-aware staging accepts the already-owned batch lease and performs:

```text
Phase 3 pages
  -> SQLite PREPARING chunks/spans/lexical rows commit
  -> embedding generation
  -> idempotent Chroma upsert
  -> vector-ID/dimension verification
  -> verified SQLite PREPARING state with intended READY/NOT_SEARCHABLE result
```

After every required ACTIVE-generation document is staged, one SQLite
transaction validates the pinned generation/revision and every staged identity,
coverage, count, and `PREPARING` state; changes all staged rows to their intended
terminal states; and performs `content_revision` CAS `R -> R+1`. Any failed
validation, persistence operation, or CAS rolls back both terminal transitions
and revision change.

`PREPARING` and `FAILED` records are invisible to retrieval. If one document in
a batch fails, successfully staged siblings remain `PREPARING`, the revision is
unchanged, and the previously published corpus remains current. A crash before
Chroma leaves retryable SQLite-derived state. A crash after Chroma but before
publication reuses deterministic IDs and verifies/upserts idempotently. Harmless
unpublished Chroma vectors may remain derived state; no duplicate logical chunks
are created, and no partially indexed document is presented as published.

Before activation, integrity checks reconcile SQLite chunks, FTS rows, expected
Chroma IDs and dimensions, READY counts, generation membership, and all page/
document-version provenance. Failed activation leaves the previous ACTIVE
generation intact.

## 29. Configuration and fingerprinting

Index-critical configuration is canonicalized into deterministic UTF-8 JSON
(sorted keys, fixed separators, explicit version fields) and hashed with
SHA-256. It includes chunker algorithm/version, sizes/overlap, lexical
normalizer version, embedding model/revision/dimensions, dense/lexical depths,
RRF constant, evidence budgets, and sufficiency settings that affect retrieval.

Changing any incompatible value produces a new generation or requires a full
rebuild. The fingerprint is stored on the generation and in evaluation
artifacts. Python `repr`, unordered mappings, local paths, secrets, and model
prompts are never fingerprint inputs.

## 30. Safe errors and observability

Indexing uses fixed categories such as:

`RAG_INDEX_LOCKED`, `NO_ELIGIBLE_DOCUMENTS`, `DOCUMENT_INDEX_FAILED`,
`EMBEDDING_FAILED`, `DENSE_INDEX_WRITE_FAILED`, `FTS_INDEX_FAILED`,
`INDEX_INTEGRITY_FAILED`, and `GENERATION_ACTIVATION_FAILED`.

Retrieval uses `NO_ACTIVE_GENERATION`, `INVALID_QUERY_SCOPE`,
`QUERY_EMBEDDING_FAILED`, `DENSE_INDEX_UNAVAILABLE`, `LEXICAL_INDEX_FAILED`,
`RETRIEVAL_INCONSISTENCY`, and `INSUFFICIENT_EVIDENCE`.

Generation and citation validation use `MODEL_UNAVAILABLE`, `MODEL_TIMEOUT`,
`INVALID_STRUCTURED_OUTPUT`, `INVALID_EVIDENCE_REFERENCE`, `UNCITED_CLAIM`,
and `PROVENANCE_RESOLUTION_FAILED`.

Logs and normal output contain fixed categories, counts, bounded identifiers,
timings, and generation/revision metadata only. They do not contain question
text, page text, raw model output, credentials, browser state, arbitrary URLs,
or exception messages.

## 31. Evaluation methodology

Acceptance has four complementary layers.

### Deterministic correctness

Tests must prove deterministic chunk keys, exact provenance, valid-only
citations, no uncited claims, READY-state visibility, failed-rebuild safety,
scope/version isolation, no LLM fallback, Chroma rebuildability, and
fingerprint determinism.

### Classical information retrieval

Report Recall@1, Recall@3, Recall@5, Recall@10, and MRR; use nDCG for graded or
multi-source relevance. Compare dense-only, FTS5-only, and hybrid RRF with the
same corpus and labeled queries. Report subgroup metrics, not only a global
score.

### Automated evaluation

RAGAS is supplementary and must be reproducible. Candidate metrics include
faithfulness/groundedness, answer relevance, context precision, and context
recall, subject to the version actually pinned during implementation. RAGAS
never replaces deterministic citation checks, classical IR, or human review.

### Human held-out review

Human gold review is authoritative for semantic correctness, completeness,
citation appropriateness, Arabic quality, partial answers, conflicts, and
unsupported expansion.

The initial gold set targets approximately 80–120 manually verified cases,
with an approximately 70% calibration/development portion and 30% locked
held-out portion. Cases cover lexical and paraphrased questions, multi-source
and conflicting evidence, partial and unsupported questions, current versus
historical scope, course isolation, and the English, Arabic, Arabic-to-English,
and English-to-Arabic subgroups. The exact corpus-constrained count is recorded
in the evaluation manifest rather than silently weakening held-out separation.

Any benchmark whose retrieval output informed a policy diagnosis or correction
is a diagnostic version, not a pristine final held-out set. It remains in the
audit history, but final acceptance must prepare a new benchmark version and
fingerprint, obtain human review of its qrels, and lock a new unseen held-out
split before retrieval output is inspected. The final unseen set may not be
used for further tuning or policy selection.

Sufficiency labels are `SUPPORTED`, `PARTIALLY_SUPPORTED`, and `UNSUPPORTED`.
The atomic entailment pipeline's internal `PARTIAL` aggregate maps to the
existing evaluation label `PARTIALLY_SUPPORTED`; it must not replace the
public `RagAnswer` status contract. Evaluation reports supported acceptance,
unsupported rejection, false acceptance, and false refusal rates. Generation
reports factual correctness, answer completeness, citation
correctness/completeness, partial-answer behavior, and unsupported expansion
separately from retrieval scores.

## 32. RAGAS evaluation policy

RAGAS execution is local by default. Its version, evaluator model, evaluator
embedding model, metric set/version, dataset version, and run identity are
recorded. Real university/course content must not be sent to OpenAI, Gemini, or
any other external evaluator implicitly. External evaluation requires sanitized
data and separate explicit human authorization; there is no external/cloud
fallback.

RAGAS results are reported alongside, never instead of, deterministic
provenance/citation gates, Recall/MRR, and human gold judgments. The final
implementation plan must pin a compatible version after checking its installed
API; this design intentionally does not add that dependency now.

## 33. Multilingual evaluation

Retrieval is multilingual and must measure English, Arabic, Arabic-question to
English-source, and English-question to Arabic-source subgroups separately.
Answers default to the question's language while citations retain the source's
original identity and page labels. Arabic material is not silently translated
in storage or citation metadata.

The evaluation report includes each subgroup and overall results so a strong
aggregate cannot hide weak Arabic behavior.

## 34. Performance evaluation

Measure locally where practical: indexing throughput, embedding time, cold and
warm query embedding latency, dense and FTS latency, fusion/selection latency,
generation latency, total latency, RAM, GPU memory, and index storage size.
Cold-start cost is reported separately. No arbitrary production SLA is chosen
by this design; implementation records measured baselines and hardware context.

The target machine has limited VRAM. Evaluation must show that embedding and
answer-model lifecycles do not require simultaneous residency and must record
the observed release behavior.

## 35. Acceptance and freeze gates

Phase 4 may be considered for a freeze tag only after this ordered path:

```text
implementation complete
  -> deterministic tests
  -> index integrity
  -> calibrated configuration
  -> locked held-out configuration
  -> classical IR evaluation
  -> RAGAS evaluation
  -> human held-out review
  -> multilingual review
  -> security/prompt-injection gates
  -> local performance review
  -> controlled local/live acceptance
  -> explicit human approval
  -> consider phase-04-passed
```

Acceptance must prove English and Arabic retrieval, valid source/page
citations, appropriate refusal of unsupported questions, untrusted document
instructions, current/historical isolation, reproducible fingerprints, and
safe failure/cleanup. No Phase 4 tag is created by this document.

## 36. Security and privacy considerations

Phase 4 is local-first. SQLite, FTS5, Chroma, embeddings, and answer generation
operate locally under explicit composition. The default runtime does not contact
Moodle, a browser, or a cloud evaluator. The local ModelGateway is restricted
to the configured loopback LM Studio boundary.

Document text is sensitive academic content and remains behind bounded,
scope-authorized evidence. Credentials, tokens, cookies, session state, private
access keys, and browser artifacts never enter RAG records or model prompts.
Raw document text, raw questions, raw prompts, raw model output, and source
paths are not written to ordinary logs. Temporary and test artifacts use
disposable roots and are cleaned deterministically.

No model output can broaden scope, access another generation, call a tool,
execute a command, or trigger a write. A provenance or citation uncertainty
fails closed.

## 37. Migration and storage implications

Phase 4 will eventually require an Alembic migration for its generation,
document-index, chunk, span, and FTS infrastructure. This design does not
create that migration. The eventual migration must preserve the shared SQLAlchemy
`Base`, Phase 3 document immutability, Phase 3 content-blob/page semantics, and
Phase 1 foreign-key/provenance identities.

Normal conceptual paths are `data/database/aaos.db` and `data/rag/chroma/`, but
imports and startup never create them. Explicit index/query commands own all
side effects. Tests and acceptance use disposable databases, Chroma roots, and
sanitized fixtures. Retired generations are retained unless a separately
approved retention design is introduced. Any future retired-generation garbage
collection must first define an in-flight snapshot/reference lifetime policy;
Phase 4 v1 adds neither garbage collection nor reference counting.

## 38. Phase 5 integration contract

Phase 5 may call Phase 4 through a typed query service that accepts a question
and explicit `RagScope`, then returns `RagAnswer`. It may use the validated
answer/citation result as one bounded observation in its future orchestration.

Phase 5 owns conversation history, pronoun resolution, follow-up context,
task state, typed academic tools, action selection, and approvals. Phase 4
does not import or depend on those facilities, does not reinterpret prior
turns, and does not expose a generic model/tool/browser surface.

## 39. Out of scope (explicit)

The following remain outside Phase 4:

- Phase 3 modifications;
- OCR, scanned-page recognition, and VLM/multimodal processing;
- agent loops, conversation memory, and history-based query rewriting;
- typed action tools, approvals, and write workflows;
- Google Calendar, GitHub MCP, dashboards, and public RAG HTTP APIs unless
  separately approved;
- background schedulers and automatic retired-index purge;
- arbitrary filesystem or URL ingestion;
- Moodle writes or browser automation;
- reranker models in v1;
- runtime LLM citation judging;
- silent BM25-only fallback;
- external/cloud evaluator fallback; and
- Phase 5 task/conversation orchestration.

## 40. Open implementation details (non-architectural only)

The following may be selected during implementation without reopening the
architecture, provided each choice is versioned, tested, and recorded:

- the final chunk token counter and overlap within the approved bounds;
- candidate depths, RRF constant, evidence budgets, and calibrated sufficiency
  thresholds;
- the exact FTS5 schema/index-maintenance statements;
- the compatible pinned RAGAS version and local evaluator model;
- the concrete BGE-M3 revision, embedding batch size, and verified device
  release mechanism;
- exact SQLAlchemy/Alembic revision identifiers and migration column names;
- exact fixed CLI grammar and safe rendering labels; and
- benchmark hardware/run-count conventions.

These details cannot expand scope, add a network dependency, expose content, or
change the trust, lifecycle, persistence, scope, citation, or phase-boundary
invariants above. Any issue that would require such a change is an architecture
review gate, not an implementation detail.

### Phase 4 runtime amendment — bounded completion and non-overlapping model residency

The following narrow amendment records the correction required by target-hardware
calibration. It changes neither the answer schema nor the `ModelGateway`
boundary, and it must be implemented and re-verified before any held-out answer
cases are executed.

#### Bounded structured completion

The earlier 1024-token plain-JSON bound was insufficient in calibration: the
initial response consumed all 1024 completion tokens and ended with incomplete
JSON. The single repair stopped below that bound but returned markdown-fenced
invalid JSON, so a larger budget alone is not sufficient. The approved bounded
Phase 4 contract is **2048 completion tokens** for both the initial response and
the one permitted repair. This remains below the configured 8192-token context
and the generator's 4096-token maximum. Before each request, the composed prompt
token allowance plus 2048 completion tokens must fit the configured context;
the implementation must fail closed rather than silently truncate evidence or
requirements, enlarge context, switch models, or fall back to a cloud service.

Both calls use LM Studio's locally confirmed `response_format` JSON Schema mode
with a deterministic strict schema representing exactly the existing
`RagAnswer` shape: `status`, `answer_text`, `language`, and `claims`, where each
claim contains exactly `claim_id`, `text`, and `evidence_ids`. The schema is
infrastructure-owned and cannot be changed by question or evidence text. JSON
Schema constrains syntax only; the existing strict application parser,
request-local evidence-ID checks, provenance/citation validator, and trusted
renderer remain authoritative. Additional fields, malformed claims, unknown
evidence IDs, markdown/prose, and incomplete output remain failures.

Calibration must prove strict completion and schema-mode success at 2048 before
held-out execution. The maximum remains one repair attempt; no regex extraction,
prose stripping, third attempt, schema relaxation, or automatic increase to
3072/4096 is allowed. If a legitimate calibration response reaches 2048 and
truncates, or JSON Schema mode or strict validation fails after the one repair,
the result is a human review gate.

#### Deterministic local model lifecycle

The target machine's installed LM Studio CLI exposes `ps --json`, `load`, and
`unload`; `load` supports fixed GPU and context options, and an optional TTL.
No supported model-management REST endpoint or JIT/on-demand guarantee has been
confirmed locally. Phase 4 therefore uses a narrowly scoped deterministic local
model-runtime controller, owned by `Phase4RagRuntime`, rather than extending
`ModelGateway` or granting any model/tool capability to the answer model.

For the configured answer model only, the controller may inspect residency with
`ps --json`, unload it with the fixed `unload <configured-model>` command, and
load it with the fixed `load <configured-model> --gpu max --context-length
<configured-context> -y` command. The model identifier and context are taken
from validated application settings; callers cannot supply a command, model,
URL, or arbitrary arguments. The controller never uses `--all`, starts or
reconfigures the server, downloads a model, or manages unrelated user models.
Command output is discarded and translated to fixed local error codes.

The request lifecycle is:

```text
ensure configured answer model unloaded and verify
  -> BGE-M3 load/embed/retrieve
  -> BGE-M3 release and verify
  -> ensure configured answer model loaded and verify
  -> one bounded generation request (plus at most one schema repair)
  -> typed validation and trusted rendering
  -> unload before a later embedding request or runtime close
```

An unload, load, or postcondition-verification failure is a typed
`MODEL_UNAVAILABLE` failure. The request fails closed: it does not proceed with
uncertain residency, change device/model, invoke a cloud endpoint, or retry the
operation. The controller is idempotent and closes deterministically; process
termination is not the cleanup mechanism. Existing embedding release and
retriever ordering remain mandatory, and no generation call may occur before
that release boundary.

Acceptance on the RTX 4050 Laptop GPU (6 GB) must record safe aggregate memory
observations proving that the configured answer model is not resident before
BGE work, BGE resources are released before answer-model load/generation, and
the two models are never simultaneously GPU-resident. A failed or unmeasurable
postcondition is a blocker, even if a run happens to fit in available VRAM.
Normal tests use injected fakes and never invoke LM Studio, CUDA, or model
downloads.

## 41. Architecture amendment — requirement-level semantic sufficiency

**Status:** historical human-approved two-step contract retained for audit.
The atomic/extractive/entailment amendment in Section 42 supersedes this
two-step decomposition/coverage design for future implementation. Existing
Tasks 14–19 remain the current baseline until a separately authorized
implementation task applies Section 42; this documentation amendment does not
authorize held-out answer execution, RAGAS, Checkpoint G, tagging, or Phase 5.
All schemas, bounds, and ordering stated below are historical and are not
normative for the amended implementation.

### Decision and evidence

The completed 56-case calibration was mechanically healthy (temperature zero,
all calls complete, no malformed outputs, no repairs, and no status variance)
but unsafe as a whole-question decision boundary. The observed confusion
included false support and recognized none of the expected partial cases.
Dense similarity, lexical coverage, question-part signals, rank/fusion
features, and source diversity overlap materially across the three classes.
Further threshold or prompt tuning of that abstraction is therefore rejected.
Structural authorization and integrity remain deterministic; semantic coverage
is replaced by bounded requirement-level judgments followed by code-owned
aggregation.

### Placement and responsibility

The amended query path is:

```text
RagQuery + immutable QuerySnapshot
  -> hybrid retrieval and deterministic EvidenceSelector
  -> existing deterministic structural sufficiency gate
  -> RequirementDecomposer (question/context only)
  -> RequirementCoverageClassifier (fixed requirements + selected evidence)
  -> strict typed requirement validation
  -> deterministic status aggregation
       -> UNSUPPORTED: deterministic INSUFFICIENT_EVIDENCE
       -> PARTIAL: grounded generation with a partial-support constraint
       -> SUPPORTED: grounded generation
  -> strict RagAnswer parsing, citation validation, provenance resolution,
     and deterministic rendering
```

The decomposer consumes only the original question and language/context needed
to understand user intent; retrieved evidence is not visible. Deterministic
code assigns canonical IDs (`r1` through `rN`) and freezes the resulting
requirement set. The coverage classifier then consumes only that immutable set,
answer-language policy, pinned generation/revision metadata, and the already
selected bounded evidence. It may not add, remove, merge, or rewrite the
immutable requirement universe. Neither step is a retrieval path, source selector, citation
resolver, or answer renderer. The LLM produces bounded data; trusted code owns
requirement identity, validation, final status, and every authorization
decision.

### Requirement identification decision

The approved approach is **two bounded structured calls** within one loaded
Qwen lifecycle: a question-only `RequirementDecomposer`, followed by a
`RequirementCoverageClassifier` that sees the frozen requirements and selected
evidence. Separating decomposition from coverage prevents evidence availability
from silently redefining the requirement universe. A single combined call is
rejected because it can omit an unsupported requested portion and then appear
fully supported. Deterministic extraction remains limited to narrow,
already-defined forms and is not the general English/Arabic contract.

### Requirement decomposition contract

The question-only decomposition schema is closed and contains no evidence or
coverage fields:

```json
{
  "type": "object",
  "properties": {
    "requirements": {
      "type": "array",
      "minItems": 1,
      "maxItems": 6,
      "items": {
        "type": "object",
        "properties": {
          "requirement_text": {
            "type": "string",
            "minLength": 1,
            "maxLength": 240
          }
        },
        "required": ["requirement_text"],
        "additionalProperties": false
      }
    }
  },
  "required": ["requirements"],
  "additionalProperties": false
}
```

The decomposer receives no retrieved evidence. It must preserve every material
requested fact, retain explicit multi-part requests, avoid artificial
over-splitting, and add nothing not requested. After strict parsing,
deterministic code assigns canonical contiguous IDs (`r1` through `rN`) and
normalizes/validates bounded nonblank text. Model-supplied IDs are neither
accepted nor trusted. The immutable requirement set is the only requirement
universe for the remainder of the request.

### Requirement coverage contract

The coverage call receives the immutable requirement IDs/text plus selected
request-local evidence. Its closed schema contains exactly one judgment for
each fixed requirement:

```json
{
  "type": "object",
  "properties": {
    "requirements": {
      "type": "array",
      "minItems": 1,
      "maxItems": 6,
      "items": {
        "type": "object",
        "properties": {
          "requirement_id": {
            "type": "string",
            "pattern": "^r[1-6]$"
          },
          "support": {
            "type": "string",
            "enum": ["SUPPORTED", "UNSUPPORTED"]
          },
          "evidence_ids": {
            "type": "array",
            "maxItems": 8,
            "items": {
              "type": "string",
              "minLength": 1,
              "maxLength": 128
            }
          }
        },
        "required": ["requirement_id", "support", "evidence_ids"],
        "additionalProperties": false
      }
    }
  },
  "required": ["requirements"],
  "additionalProperties": false
}
```

The coverage response may not add, remove, merge, or rewrite the requirement
universe. Response array order is not semantically authoritative: deterministic
code requires exactly one judgment for each expected ID and normalizes valid
permutations to canonical ID order. The response has no requirement text,
confidence, rationale, citation
string, source metadata, tool request, chain-of-thought, or answer field.
Evidence IDs remain opaque request-local control identifiers; the schema does
not invent their format.

### Deterministic validation and aggregation

Before judging coverage, deterministic code requires the returned requirement
count to equal the immutable requirement count and the returned ID set to equal
the immutable ID set exactly. Missing, duplicate, unknown, or rewritten
requirements are integrity errors and fail closed; a valid permutation of the
expected IDs is normalized to canonical order. For every
`SUPPORTED` requirement, deterministic code verifies that each evidence ID
exists in the current request, was selected for the pinned `QuerySnapshot`, and
resolves through authoritative SQLite provenance. A missing, duplicate,
cross-request, out-of-snapshot, or unresolved evidence ID is an integrity error
and is never downgraded into trusted support. For every `UNSUPPORTED`
requirement, `evidence_ids` must be empty. Any schema deviation is a typed
semantic-coverage failure.

After validation, code derives the internal status and the model cannot
override it:

- zero supported requirements (with a valid non-empty requirement list) ->
  `UNSUPPORTED`;
- every material requirement supported -> `SUPPORTED`;
- at least one supported and at least one unsupported -> `PARTIAL`.

`UNSUPPORTED` maps to deterministic `INSUFFICIENT_EVIDENCE` and sets
`FACTUAL_ANSWER_GENERATION_INVOKED=NO`. `SUPPORTED` and `PARTIAL` continue to
the existing grounded answer boundary. Internal coverage statuses remain
distinct from public `RagAnswer` statuses.

### Partial propagation

For deterministic `PARTIAL`, `GroundedAnswerGenerator` receives the original
question, the supported requirement subset, only the validated evidence IDs and
text for those supported requirements, and bounded descriptions/identities of
the unsupported requirements. Unsupported requirements are constraints to be
identified as unanswered, never factual source material. The generator may
answer supported portions only, must explicitly mark unsupported requested
portions, must not infer missing facts, and must cite every factual claim using
the existing strict citation/provenance validator. The renderer remains fully
trusted and deterministic.

### Model, bounds, and failure behavior

Use only the approved local `qwen/qwen3.5-4b` through the existing loopback
`ModelGateway`; no cloud provider, external network, tool, agent, memory, or
fallback is permitted. The proposed completion bounds are **512 tokens for
decomposition** and **512 tokens for coverage**: decomposition is materially
above the old 256-token whole-question bound, while coverage remains small and
bounded. Both remain below the existing 4096 maximum and must be calibrated
before implementation approval. Before each call, its prompt allowance plus
the corresponding bound must fit the configured model context; otherwise fail
closed without truncating evidence, changing models, or enlarging context. Use
local JSON-Schema response-format mode and strict application parsing for both
steps.

Each semantic step permits only one bounded same-schema repair. Transport,
model, lifecycle, and policy errors are not retried. Malformed or semantically
invalid output after a step's repair fails closed with a typed operational
error and never falls through to factual generation. There is no third call,
prose stripping, regex extraction, schema relaxation, or silent threshold
fallback. `temperature=0` applies to both steps.
The approved factual grounded-answer timeout remains 60 seconds and the
2048-token factual completion contract is unchanged.

### Prompt-injection, provenance, and lifecycle boundaries

For decomposition, only the question/request context is interpreted as user
intent; retrieved evidence is absent. For coverage, retrieved document text
and the fixed requirement text remain untrusted data. Instructions explicitly
separate policy from data; evidence cannot add, remove, or rewrite
requirements, alter schema, scope, tools, secrets, model/runtime configuration,
or network behavior. No tools are exposed. Evidence IDs are supplied only by
trusted request-local context and are validated against authoritative SQLite
provenance.

Per request, the runtime ensures Qwen is not resident, performs BGE-M3
embedding/retrieval, releases and verifies BGE, then loads/verifies Qwen for
decomposition. Qwen remains loaded for coverage and any subsequent factual
generation; it is unloaded only during deterministic cleanup. There is no
unload/reload boundary between semantic steps or before factual generation.
BGE and Qwen may never overlap on the GPU. Any lifecycle/postcondition failure
stops the request with the existing safe model-unavailable error. The runtime
remains the sole lifecycle owner.

### Calibration and held-out protection

Calibration may use only the already-consumed 56 cases with expected classes
`SUPPORTED=40`, `PARTIAL=4`, and `UNSUPPORTED=12`. First evaluate decomposition
quality against the human-reviewed request intent, especially all four partial
and twelve unsupported cases. Report `MATERIAL_REQUIREMENT_OMITTED`,
`SPURIOUS_REQUIREMENT_ADDED`, `OVER_SPLIT`, and `UNDER_SPLIT` errors without
changing benchmark labels. Then evaluate coverage and deterministic
aggregation, reporting requirement counts, judgments, aggregate statuses,
false support, false refusal, partial-to-supported, and partial-to-unsupported
outcomes with English/Arabic breakdowns. Require zero false support across all
12 unsupported cases and materially functional partial behavior across all four
partial cases, without benchmark-specific exceptions. Also prove local-only
model use, strict schema/parse behavior, one-repair maximum per semantic step,
and BGE-before-Qwen non-overlapping residency. Poor calibration results are
reported, never hidden by changing retrieval or normalizing labels.

The final 24 answer-heldout cases remain pristine:
`HELDOUT_ANSWER_CASES_EXECUTED=0` and
`ANSWER_HELDOUT_RESULTS_VISIBLE=NO` during this amendment. No held-out answer,
RAGAS, Checkpoint G, or Phase 5 action is authorized until this design is
approved, implemented, calibrated, reviewed, and all existing pre-heldout
gates pass.

### Proposed implementation surface (not authorized by this amendment)

Production files proposed for a later implementation task:

- `src/aaos/rag/contracts.py`: add immutable requirement and coverage-judgment
  types while retaining the existing public `RagAnswer` statuses;
- `src/aaos/rag/semantic_coverage.py`: replace whole-question parsing with the
  local-only `RequirementCoverageClassifier`, schema, strict validation,
  context-budget check, and one-repair bound;
- `src/aaos/rag/service.py`: perform deterministic validation/aggregation after
  structural gating and propagate `PARTIAL`/`UNSUPPORTED` without duplicating
  retrieval or citation logic; and
- `src/aaos/rag/generation.py`: accept the typed partial-support constraint
  while preserving the strict `RagAnswer` contract and existing citation
  boundary.

Proposed test surface:

- `tests/test_rag_semantic_coverage.py` for schema, requirement decomposition,
  evidence-ID/provenance validation, aggregation, context limits, injection
  data, no-tools/no-network, and one-repair behavior;
- extensions to `tests/test_rag_service.py` and `tests/test_rag_generation.py`
  for structural/semantic ordering, partial propagation, unsupported
  no-factual-call behavior, and strict public status preservation; and
- acceptance-only calibration coverage under the existing guarded Task 26
  harness, with no held-out execution.

Phase 3 remains read-only and unchanged. No schema, storage, retrieval scope,
citation authority, public answer status, or persistence contract is changed by
this design amendment.

## 42. Architecture amendment — atomic requirements, extractive evidence, and independent entailment

**Status:** human-approved in principle for design and later implementation
planning only. This narrow amendment addresses the audited semantic failures;
it does not authorize production changes, retrieval/selector changes,
held-out answer execution, RAGAS, Checkpoint G, tagging, or Phase 5.

### Confirmed failure boundary

The audited calibration cases were `v2-p005`, `v2-p020`, `v2-p021`,
`v2-p022`, `v2-p035`, `v2-p041`, and `v2-p047`. Evidence showed four
under-split decompositions, zero omitted requirements, two coverage false
entailments, two topical-similarity false entailments, two partial-evidence-as-
full-support decisions, and one separate selector/retrieval boundary issue.
The persistent dangerous false-support cases were `v2-p022` and `v2-p047`.
The dominant diagnosis is topical similarity being treated as entailment at the
coverage boundary, compounded by under-split decomposition. The production
model remains `qwen/qwen3.5-4b`.

The current two-step coverage model is insufficient because a valid,
request-local evidence ID does not prove that its text entails the requested
fact. Retrieval and deterministic selection remain frozen; this amendment adds
semantic checks after the existing structural gate without changing the
retrieval or selector configuration.

### Amended pipeline

The semantic section is replaced by this ordered, single-retrieval path:

```text
RagQuery -> QuerySnapshot -> hybrid retrieval -> deterministic selector
  -> deterministic structural sufficiency gate
  -> release BGE-M3 and verify release
  -> load qwen/qwen3.5-4b
  -> atomic requirement decomposition (question/context only)
  -> deterministic canonical requirement IDs and immutable requirement set
  -> extractive evidence-span identification
  -> deterministic span/request/snapshot/provenance validation
  -> independent entailment verification (one atomic requirement at a time)
  -> deterministic per-requirement support
  -> deterministic SUPPORTED / PARTIAL / UNSUPPORTED aggregation
  -> UNSUPPORTED: deterministic INSUFFICIENT_EVIDENCE
  -> PARTIAL or SUPPORTED: constrained factual generation
  -> strict RagAnswer validation -> citation/provenance validation -> rendering
  -> Qwen cleanup
```

No step performs another retrieval. The model outputs bounded proposals only;
trusted code owns IDs, immutability, validation, support, aggregate status,
citations, and rendering.

### Atomic requirement schema and contract

The question-only decomposition output is a closed object with no evidence,
coverage, citation, confidence, rationale, or chain-of-thought fields:

```json
{
  "type": "object",
  "properties": {
    "requirements": {
      "type": "array",
      "minItems": 1,
      "maxItems": 6,
      "items": {
        "type": "object",
        "properties": {
          "requirement_text": {
            "type": "string",
            "minLength": 1,
            "maxLength": 240
          }
        },
        "required": ["requirement_text"],
        "additionalProperties": false
      }
    }
  },
  "required": ["requirements"],
  "additionalProperties": false
}
```

Each requirement is the smallest materially answerable factual portion of the
request. Explicit independent aspects, `and`-joined facts, comparison
dimensions, and named factual categories are split when each can be supported
or unsupported independently. Independently verifiable facts are not combined;
an indivisible proposition is not artificially fragmented; no requested fact
may be added or omitted. Retrieved evidence is invisible to decomposition.
The model proposes text only. Deterministic code assigns contiguous `r1`...
`rN` IDs, validates the closed schema and bounded count, and freezes the
immutable requirement set. Code does not claim to semantically prove
atomicity; atomicity quality is a calibration metric.

### Extractive span schema and validation

The span step receives only the immutable requirements and selected bounded
evidence. It does not decide support or render citations. Its closed schema is:

```json
{
  "type": "object",
  "properties": {
    "requirements": {
      "type": "array",
      "minItems": 1,
      "maxItems": 6,
      "items": {
        "type": "object",
        "properties": {
          "requirement_id": {
            "type": "string",
            "pattern": "^r[1-6]$"
          },
          "spans": {
            "type": "array",
            "maxItems": 4,
            "items": {
              "type": "object",
              "properties": {
                "evidence_id": {
                  "type": "string",
                  "minLength": 1,
                  "maxLength": 128
                },
                "quote": {
                  "type": "string",
                  "minLength": 1,
                  "maxLength": 400
                }
              },
              "required": ["evidence_id", "quote"],
              "additionalProperties": false
            }
          }
        },
        "required": ["requirement_id", "spans"],
        "additionalProperties": false
      }
    }
  },
  "required": ["requirements"],
  "additionalProperties": false
}
```

There must be exactly one entry for every immutable requirement ID, while zero
spans is valid when no candidate support exists. Evidence IDs must be selected,
request-local IDs in the pinned snapshot. Deterministic code rejects missing,
duplicate, unknown, cross-request, or out-of-snapshot requirement/evidence IDs;
unresolved SQLite provenance; and quotes that do not occur in the corresponding
selected evidence text (prefer exact normalized substring matching). A model
paraphrase is never accepted as an extractive quote. Any span or provenance
failure fails closed and cannot become support. A valid span may combine a small
number of short quotes only when the proposition genuinely requires them.

### Independent entailment schema and contract

For each immutable requirement, the verifier receives only that requirement,
its validated span text, and minimal fixed instructions. It receives no
unrelated chunks and cannot perform retrieval. Its closed output is:

```json
{
  "type": "object",
  "properties": {
    "result": {
      "type": "string",
      "enum": ["ENTAILED", "NOT_ENTAILED"]
    }
  },
  "required": ["result"],
  "additionalProperties": false
}
```

No requirement-level `PARTIAL`, confidence, rationale, evidence IDs,
chain-of-thought, or citation text is permitted. If no validated span exists,
deterministic code assigns `NOT_ENTAILED` without a model call. Otherwise the
preferred implementation performs one bounded request per requirement while
keeping Qwen loaded; it must not silently batch unrelated requirements.
`ENTAILED` requires the span to directly establish the complete atomic
proposition. Topic/entity overlap, headings, contextual similarity, related
prognosis for a treatment request, partial evidence for a broad proposition,
plausible inference, and outside knowledge are `NOT_ENTAILED`.

### Support, aggregation, and partial propagation

Trusted code maps a validated entailment result to per-requirement support:
`ENTAILED` -> `SUPPORTED`; `NOT_ENTAILED` -> `UNSUPPORTED`. Neither the span
extractor nor factual generator can override this mapping. The aggregate is
code-owned: all supported -> `SUPPORTED`, all unsupported -> `UNSUPPORTED`,
and any mixture -> `PARTIAL`. `UNSUPPORTED` deterministically becomes public
`INSUFFICIENT_EVIDENCE` and skips factual generation. Internal statuses remain
distinct from public `RagAnswer` statuses.

For `PARTIAL`, factual generation receives the original question, supported
immutable requirements, validated entailing spans/chunks for those requirements,
and descriptions of unsupported requirements. It answers supported portions
only, explicitly identifies unavailable portions, never infers unsupported
facts, and cites every factual claim through the existing trusted validator.
This makes partial behavior a mechanical consequence of support judgments.

### Bounds, repair, model, and failure behavior

The model remains `qwen/qwen3.5-4b` with `temperature=0`. Proposed bounded
completion limits for calibration are **512 tokens for atomic decomposition**
(retaining the existing proven bound), **384 tokens for span extraction**, and
**64 tokens for entailment**. These are separate limits and must fit the
configured context with prompt allowance; they are not permission to truncate
requirements/evidence or enlarge context. Each step permits at most one
same-schema repair; no third call, prose stripping, schema relaxation, or
fallback model/cloud path is allowed. Any malformed/transport output,
context overflow, invalid requirement set, invalid ID/quote/provenance,
entailment failure, lifecycle failure, or failed repair fails closed before
factual generation.

#### Atomic decomposition and span-completeness prompt contract amendment

The decomposition contract preserves the closed statuses `OK` and
`TOO_MANY_REQUIREMENTS`, with a maximum of six requirements. A material
requirement is the smallest independently answerable factual portion needed to
satisfy the request. The model must preserve every independently answerable
material part, but must not split a coherent fact merely because it contains
descriptive words, qualifiers, examples, attributes, clauses, noun phrases, or
supporting details. It must prefer the minimum complete set of independently
supportable/refusable requirements: one coherent fact normally remains one
requirement; explicit independently answerable parts, comparison dimensions,
or separately answerable categories may remain separate.

`TOO_MANY_REQUIREMENTS` is valid only when, after applying those
anti-over-splitting rules, the request genuinely contains more than six
independently answerable material factual requirements. It is not a length,
uncertainty, evidence-difficulty, conservative-refusal, or qualifier shortcut.
When the request fits faithfully within one through six requirements, status
must be `OK`; `OK` with zero requirements and `TOO_MANY_REQUIREMENTS` with any
requirements remain invalid combinations.

For every extractive span request, the prompt explicitly states
`EXPECTED_REQUIREMENT_COUNT=N` and the exact immutable ID set
`[r1,...,rN]`. The model must return exactly one entry for every listed ID,
including an entry with `spans=[]` when no usable evidence exists; missing,
duplicate, or unknown IDs are forbidden. Array order may vary and trusted code
normalizes by ID. The single permitted span repair repeats the exact count and
ID set and identifies incomplete coverage explicitly. Prompt compliance does
not replace deterministic validation: trusted code still enforces exact ID
coverage, request-local evidence membership, pinned-snapshot membership, quote
occurrence, and SQLite provenance.

### Prompt-injection, retrieval, and lifecycle boundaries

Retrieved evidence and extracted spans are untrusted data. They cannot modify
requirements, schemas, scope, model/runtime configuration, tools, network
behavior, or secrets. No tools, agents, memory, or external fallback are
exposed. The retrieval and selector configuration is unchanged. In particular,
`v2-p035` remains a documented recall/selector limitation: if useful evidence
was not selected, the semantic path returns `UNSUPPORTED` rather than broadening
retrieval. The `v2-p022`/`v2-p047` observations motivate the generic
topical-similarity-versus-entailment rule only; no case-specific production
rules are introduced.

The accepted GPU lifecycle is unchanged: Qwen absent during BGE work; BGE
released and verified; Qwen loaded once for decomposition, span extraction,
independent entailment checks, and any allowed factual generation; Qwen cleaned
up deterministically. BGE and Qwen must never be simultaneously resident, and
an unverifiable postcondition fails closed.

### Calibration and held-out protection

After a separate implementation authorization, evaluate only the already-
consumed 56 calibration cases (`SUPPORTED=40`, `PARTIAL=4`, `UNSUPPORTED=12`)
before touching the pristine 24 answer-heldout cases. Report decomposition
timeouts, truncations, malformed outputs, repairs, under/over-split, omitted,
and spurious requirements; span cases, validation/provenance failures,
invalid/nonlocal IDs, quote-not-found, and average/max spans; entailment checks,
timeouts, malformed/repair counts, topical-only acceptance, and
partial-as-full acceptance; and the deterministic aggregate confusion matrix
with English/Arabic breakdowns. The hard pre-heldout safety gate is
`FALSE_SUPPORT_COUNT=0`, with materially functional `PARTIAL` behavior. Poor
recall or false refusal is reported, not hidden by changing retrieval, labels,
or thresholds. Held-out answer cases remain unexecuted and invisible until all
implementation, calibration, lifecycle, citation, and other pre-heldout gates
pass.

### Proposed future implementation surface (not authorized here)

The smallest expected production change is to evolve the existing
`src/aaos/rag/semantic_coverage.py` boundary into typed atomic decomposition,
span extraction/validation, and independent entailment adapters; extend
`src/aaos/rag/contracts.py` with immutable requirement/span/entailment values;
update `src/aaos/rag/service.py` for deterministic support aggregation and
partial/unsupported propagation; and update `src/aaos/rag/generation.py` only
for the typed partial-support input. No retrieval or selector file changes are
expected. Proposed tests are
`tests/test_rag_semantic_coverage.py` (or a narrowly renamed replacement) for
the three schemas, exact validation, fail-closed behavior, and one-repair
bounds, plus focused service/generation/runtime/import and guarded calibration
tests using fakes. No code or tests are changed by this documentation task.

## 43. Design self-review checklist

This specification was checked for:

- no unresolved placeholder decisions;
- explicit ACTIVE/BUILDING/RETIRED and content-revision semantics;
- pinned query consistency during rebuild/index-new;
- SQLite authority over Chroma and provenance;
- no hidden model, lexical, cloud, browser, or network fallback;
- no Phase 3 redesign or Phase 5 agent scope leakage;
- explicit prompt-injection, citation, privacy, and safe-error behavior;
- no requirement to add dependencies or run a model in this documentation task;
- alignment with current `DocumentVersion`/`DocumentPage`, SQLAlchemy/Alembic,
  local `ModelGateway`/`LMStudioGateway`, configuration, and CLI lifecycle
  contracts; and
- explicit separation of architecture from later non-architectural tuning.
