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
  -> deterministic sufficiency gate
  -> existing local ModelGateway
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
- insufficient evidence prevents any factual answer-generation request; and
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
  -> SufficiencyGate
       -> INSUFFICIENT_EVIDENCE
       -> GroundedAnswerGenerator (existing ModelGateway)
  -> RagAnswer parser
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

## 19. Sufficiency gate

The sufficiency gate is deterministic and conservative. It considers retrieval
coverage, rank/fusion evidence, scope, and labeled threshold policy. The LLM
cannot declare evidence sufficient, and no answer-generation request is made
when the gate returns insufficient evidence.

The result is `INSUFFICIENT_EVIDENCE`, with no pretrained-knowledge fallback.
Thresholds are calibrated on a development set, then locked before held-out
acceptance. Unsupported false acceptance is weighted as more serious than a
false refusal.

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
grounded; no unsupported generation or pretrained-knowledge fallback occurs.

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
Evaluation reports supported acceptance, unsupported rejection, false
acceptance, and false refusal rates. Generation reports factual correctness,
answer completeness, citation correctness/completeness, partial-answer
behavior, and unsupported expansion separately from retrieval scores.

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

## 41. Design self-review checklist

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
