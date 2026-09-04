# Phase 4 Local RAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local, stateless, document-only RAG subsystem over frozen Phase 3 `DocumentVersion` and `DocumentPage` records, with deterministic hybrid retrieval, exact provenance, grounded claim-level citations, local-model generation, and evaluation-backed acceptance.

**Architecture:** SQLite is authoritative for generations, document readiness, chunks, exact spans, FTS5/BM25, and provenance. Chroma is a rebuildable per-generation dense index whose results are trusted only after SQLite resolution. BGE-M3 is hidden behind an application-owned `EmbeddingService`; the existing asynchronous `ModelGateway` is the only generation boundary. One writer builds or mutates indexes, queries pin an immutable generation/revision/scope snapshot, and `LocalRagService` exposes only typed, citation-validated answers. Phase 4 reads frozen Phase 3 persistence and never depends on `Phase3ServiceRuntime`.

**Tech Stack:** Python 3.11, frozen dataclasses and `StrEnum`, SQLAlchemy 2.x, Alembic, SQLite/FTS5, Chroma embedded persistence, BGE-M3 through a verified Windows/CUDA-compatible adapter, existing `ModelGateway`/`LMStudioGateway`, pytest, Ruff, strict mypy, uv, and evaluation-only RAGAS behind an explicit local evaluator.

**Spec:** `docs/superpowers/specs/2026-08-28-phase-4-local-rag-design.md`

## Frozen Boundaries

- Phase 3 remains frozen and read-only. Phase 4 consumes persisted `DocumentVersionORM` and `DocumentPageORM` data and does not call Moodle, browser, downloader, PDF processor, or Phase 3 runtime services.
- The corpus contains document content only. Assignments, announcements, course metadata, browser state, credentials, and structured academic facts are excluded.
- Phase 4 is stateless across questions: no chat history, memories, agents, tools, actions, or Phase 5 orchestration.
- Normal paths `data/database/aaos.db` and `data/rag/chroma/` are never used by automated tests. Real-corpus indexing is a separately authorized controlled acceptance action.
- Public answer statuses are exactly `ANSWER`, `PARTIAL_ANSWER`, and `INSUFFICIENT_EVIDENCE`. Operational failures remain typed errors outside `RagAnswer`.
- There is no hidden dense-only, lexical-only, ordinary-chat, cloud-model, or ungrounded fallback.
- No implementation task creates or moves `phase-04-passed`; freeze requires separate human authorization after all gates.
- The pre-existing untracked `reflex` path is never modified or staged.

## Existing Contracts Reused Exactly

| Existing contract | Phase 4 use |
| --- | --- |
| `aaos.persistence.database.create_database_engine(Path) -> Engine` | Create one explicitly owned SQLite engine. |
| `aaos.persistence.database.create_session_factory(Engine)` | Inject session factories; no global sessions. |
| `aaos.persistence.document_models.DocumentVersionORM` | Immutable source version and SHA-256 identity. |
| `aaos.persistence.document_models.DocumentPageORM` | Exact page text and page-number provenance. |
| `aaos.persistence.document_models.DocumentORM` plus academic `RemoteFileORM`/`CourseORM` | Resolve current, course, and historical eligibility without adding Moodle persistence. |
| `aaos.inference.gateway.ModelGateway` | Existing asynchronous `generate(ChatRequest) -> ChatResponse` and availability boundary. |
| `aaos.inference.models.ChatMessage`, `ChatRequest`, `ChatResponse` | Build the grounded prompt and consume local-model output. |
| Existing inference errors | Translate connection, timeout, protocol, backend, and availability failures to safe Phase 4 operational errors. |
| `Phase3ServiceRuntime` lifecycle convention | Model explicit runtime ownership, idempotent `close()`, context manager behavior, and construction-failure cleanup; Phase 4 does not depend on the runtime itself. |
| `IngestionLock` ownership semantics | Pattern for a distinct `RagIndexLock`; no lock file or type is shared with Phase 3. |
| `src/aaos/cli.py` lazy dispatch | Add only the approved `rag` command family without import-time construction. |

## Dependency Plan and Pre-Implementation Gate

No Phase 4 package is currently present in `pyproject.toml` or `uv.lock`. Task 1 must verify current official metadata before choosing exact pins; the plan intentionally does not guess versions.

| Package/capability | Role | Dependency group | Compatibility and governance gate |
| --- | --- | --- | --- |
| `chromadb` | Embedded, persistent, rebuildable dense index | Production | Select one exact version compatible with Python 3.11 and Windows; inspect transitive telemetry/network defaults, persistence behavior, license, and SQLite coexistence; disable telemetry in explicit app configuration. |
| `FlagEmbedding` | Preferred official BGE-M3 adapter candidate | Production | Verify the selected version supports `BAAI/bge-m3`, Python 3.11, Windows 11, installed CUDA/PyTorch combination, local model caching, and the package license. If it is not viable, stop for human architecture review rather than silently switching libraries. |
| Transitive `torch`/`transformers` stack selected by the BGE-M3 adapter | Model execution/tokenization | Production/transitive | Record exact resolved versions, CUDA capability, wheel source, licenses, disk footprint, and expected RTX 4050 6 GB VRAM use. No model is downloaded during dependency installation or ordinary tests. |
| `ragas` | Supplementary evaluation only | Dev/test | Select one exact version after checking its current metric APIs, Python compatibility, license, evaluator abstractions, and cloud-provider defaults. It must not enter runtime imports and must have an explicit local-evaluator/no-cloud adapter. |
| SQLite FTS5 | Lexical index/BM25 | Standard library/runtime | Add no package. Verify `sqlite_compileoption_used('ENABLE_FTS5')` in migration/tests and fail safely if unavailable. |

Task 1 records the accepted versions and licenses in test assertions plus lockfile metadata. Any package conflict, unsupported Windows/CUDA path, non-local-only evaluator constraint, or unacceptable license is a blocking dependency-governance result, not permission to substitute a new architecture.

## Proposed Final File Structure

### Production files

| Path | Change | Responsibility | Inputs / outputs | Task |
| --- | --- | --- | --- | --- |
| `pyproject.toml`, `uv.lock` | Modify | Exact verified runtime/evaluation dependency pins. | Package metadata -> reproducible environment. | 1 |
| `src/aaos/rag/__init__.py` | Create | Export only narrow public Phase 4 contracts/facade. | Public imports. | 2, 19 |
| `src/aaos/rag/contracts.py` | Create | Closed enums, scopes, queries, indexing results, evidence, claims, answers, safe errors. | Frozen typed values. | 2, 17, 18 |
| `src/aaos/rag/config.py` | Create | Validated paths, v1 parameters, canonical JSON fingerprint. | Injected data root/config -> immutable settings/fingerprint. | 2 |
| `src/aaos/persistence/rag_models.py` | Create | Generation, document-index, chunk, and span ORM mappings. | Shared `Base`. | 3 |
| `migrations/env.py` | Modify | Register Phase 4 ORM metadata. | Alembic metadata. | 3 |
| `migrations/versions/0004_phase4_local_rag.py` | Create | Reversible tables, constraints, indexes, and FTS5 virtual table. | Revision `0003` -> `0004`. | 3 |
| `src/aaos/rag/repository.py` | Create | Generation/revision transactions, Phase 3 eligibility, chunks/spans/FTS, durable query snapshots, snapshot-safe authoritative materialization, integrity resolution. | Session factory -> authoritative SQLite operations. | 4, 6, 11, 14–15 |
| `src/aaos/rag/chunking.py` | Create | Deterministic structure-aware chunker and exact page/character spans. | Phase 3 pages -> `ChunkDraft` values. | 5 |
| `src/aaos/rag/lexical.py` | Create | Conservative versioned normalizer and BM25 query adapter. | Text/query -> normalized lexical terms/results. | 6, 14 |
| `src/aaos/rag/dense.py` | Create | `DenseIndex`/factory protocols and typed vector records/hits. | Generation vectors and query vector -> keys/ranks. | 7 |
| `src/aaos/rag/chroma.py` | Create | Embedded Chroma per-generation implementation. | Disposable/persistent generation path. | 8 |
| `src/aaos/rag/embeddings.py` | Create | `EmbeddingService`, deterministic fake boundary, and BGE-M3 lifecycle adapter. | Text batches -> vectors/model metadata. | 9 |
| `src/aaos/inference/lm_studio_lifecycle.py` | Create | Fixed-command local answer-model residency controller; no generic model-management surface. | Validated settings -> typed load/unload lifecycle. | 17, 20, 26 amendment |
| `src/aaos/rag/lock.py` | Create | Ownership-safe Phase 4 single-writer lease. | Absolute lock path -> lease. | 10 |
| `src/aaos/rag/indexing.py` | Create | Per-document indexing, `index-new`, rebuild, verification, activation, recovery. | Repository/chunker/embedding/dense/lock -> typed results. | 11–13 |
| `src/aaos/rag/retrieval.py` | Create | Scope resolution, pinned query snapshot, dense/lexical retrieval, RRF, evidence selection/sufficiency. | `RagQuery` -> trusted selected evidence. | 14–16 |
| `src/aaos/rag/semantic_coverage.py` | Proposed amended semantic boundary | Evolve the local-only semantic stage into atomic requirement decomposition, extractive span identification/validation, independent entailment checks, and deterministic support aggregation after structural gating; strict bounded output and no-fallback behavior. The decomposition prompt balances completeness, atomicity, and minimality; `TOO_MANY_REQUIREMENTS` is reserved for genuinely more-than-six independently answerable requirements. Span prompts state the exact expected requirement count/ID set, including the same immutable completeness target in the one repair. | Question/context, then selected evidence, then one requirement with validated spans + `ModelGateway` -> immutable requirements, validated spans, entailment results -> internal `SUPPORTED`/`PARTIAL`/`UNSUPPORTED`. | Future amendment authorization |
| `src/aaos/rag/generation.py` | Create | Grounded prompt, structured parsing/repair, citation validation and deterministic rendering. | Evidence + `ModelGateway` -> `RenderedRagResponse`. | 17–18 |
| `src/aaos/rag/service.py` | Create | `LocalRagService` facade and no-fallback orchestration. | `RagQuery` -> rendered response. | 19 |
| `src/aaos/rag/runtime.py` | Create | `Phase4RagRuntime` composition/lifecycle ownership. | Explicit paths/settings/gateway -> services. | 20 |
| `src/aaos/rag/cli.py` | Create | Exact index/rebuild/status/interactive ask commands and safe rendering. | argv/stdin -> safe output/exit code. | 21 |
| `src/aaos/cli.py` | Modify | Lazy dispatch for `rag`. | Top-level argv. | 21 |
| `src/aaos/rag/evaluation/__init__.py` | Create | Evaluation-only public surface. | Safe tooling imports. | 22 |
| `src/aaos/rag/evaluation/contracts.py` | Create | Gold cases, split/run manifests, sanitized result records. | Curated metadata -> validated manifests. | 22 |
| `src/aaos/rag/evaluation/metrics.py` | Create | Recall/MRR/nDCG, subgroup, sufficiency, ablation calculations. | Ranked results/labels -> aggregates. | 22 |
| `src/aaos/rag/evaluation/ragas_adapter.py` | Create | Explicit local-only RAGAS adapter with no cloud fallback. | Sanitized/private local evaluation inputs -> supplementary metrics. | 23 |
| `src/aaos/rag/evaluation/reports.py` | Create | Human, injection, performance, fingerprint, and acceptance summaries. | Safe records -> sanitized artifacts. | 24, 26 |

### Test and acceptance files

| Path | Change | Responsibility | Task |
| --- | --- | --- | --- |
| `tests/test_rag_dependencies.py` | Create | Import/version/license/group/side-effect checks. | 1 |
| `tests/test_rag_contracts.py`, `tests/test_rag_config.py` | Create | Closed types, validation, paths, fingerprint determinism. | 2 |
| `tests/test_rag_migration.py`, `tests/test_database.py` | Create/Modify | `0004` upgrade/downgrade, FTS5, constraints, head revision. | 3 |
| `tests/test_rag_repository.py` | Create | Eligibility, generation/revision, readiness, provenance, FTS transactions. | 4, 6, 11, 14 |
| `tests/test_rag_chunking.py` | Create | Structure, size bounds, overlap, Unicode, spans, keys. | 5 |
| `tests/test_rag_lexical.py` | Create | Conservative normalization and BM25 behavior. | 6 |
| `tests/test_rag_dense.py`, `tests/test_rag_chroma.py` | Create | Protocol/fake behavior and disposable Chroma isolation. | 7–8 |
| `tests/test_rag_embeddings.py` | Create | Fake and BGE-M3 adapter metadata/lifecycle without model download. | 9 |
| `tests/test_lm_studio_lifecycle.py` | Create | Deterministic answer-model unload/load ordering and fail-closed postconditions. | 17, 20, 26 amendment |
| `tests/test_rag_lock.py` | Create | Exclusive ownership and safe release. | 10 |
| `tests/test_rag_indexing.py`, `tests/test_rag_rebuild.py` | Create | Per-document state, publication, rebuild, integrity, recovery. | 11–13 |
| `tests/test_rag_retrieval.py` | Create | Scope/current/history, snapshot, candidate verification, RRF. | 14–15 |
| `tests/test_rag_evidence.py` | Create | Selection, budgets, IDs, sufficiency, conflict retention. | 16 |
| `tests/test_rag_semantic_coverage.py` | Proposed amended semantic boundary | Atomic requirement, span extraction/validation, independent entailment, deterministic aggregation, bounded repair, context/privacy/failure behavior. | Future amendment authorization |
| `tests/test_rag_generation.py` | Create | Prompt isolation, structured retry, answer/citation contract. | 17–18 |
| `tests/test_rag_service.py` | Create | End-to-end facade/no-fallback behavior using fakes. | 19 |
| `tests/test_rag_runtime.py`, `tests/test_rag_imports.py` | Create | Ownership, cleanup, construction failure, side-effect-free imports. | 20 |
| `tests/test_rag_cli.py` | Create | Exact grammar, stdin question, scope exclusivity, safe output. | 21 |
| `tests/test_rag_evaluation.py`, `tests/test_rag_ragas.py` | Create | Dataset/splits/metrics/ablation/privacy/local evaluator. | 22–23 |
| `tests/test_rag_reports.py` | Create | Human/safety/performance sanitized artifacts. | 24 |
| `tests/test_rag_integration.py`, `tests/test_rag_crash_recovery.py` | Create | Disposable full pipeline and injected cross-store failures. | 25 |
| `tests/live/test_phase4_local_rag_acceptance.py` | Create | Explicitly opted-in real BGE-M3/local ModelGateway/hardware acceptance only. | 26 |

## Core Interface Ledger

```python
class GenerationStatus(StrEnum):
    BUILDING = "BUILDING"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class DocumentIndexStatus(StrEnum):
    PREPARING = "PREPARING"
    READY = "READY"
    NOT_SEARCHABLE = "NOT_SEARCHABLE"
    FAILED = "FAILED"


class CoverageStatus(StrEnum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    NONE = "NONE"


class RagAnswerStatus(StrEnum):
    ANSWER = "ANSWER"
    PARTIAL_ANSWER = "PARTIAL_ANSWER"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass(frozen=True, slots=True)
class CourseScope:
    course_id: int


@dataclass(frozen=True, slots=True)
class GlobalScope:
    pass


@dataclass(frozen=True, slots=True)
class HistoricalScope:
    document_version_ids: tuple[int, ...]


RagScope = CourseScope | GlobalScope | HistoricalScope


@dataclass(frozen=True, slots=True)
class RagRuntimePaths:
    database_path: Path
    chroma_root: Path
    index_lock_path: Path
    evaluation_output_root: Path


@dataclass(frozen=True, slots=True)
class RagIndexConfig:
    chunk_target_tokens: int
    chunk_preferred_min_tokens: int
    chunk_preferred_max_tokens: int
    chunk_hard_max_tokens: int
    chunk_overlap_tokens: int
    dense_candidate_limit: int
    lexical_candidate_limit: int
    rrf_k: int
    max_evidence_chunks: int
    max_evidence_tokens: int
    chunker_version: str
    lexical_normalizer_version: str
    sufficiency_policy_version: str


@dataclass(frozen=True, slots=True)
class RagQuery:
    question: str
    scope: RagScope
    answer_language: str | None = None


@dataclass(frozen=True, slots=True)
class RagGenerationIdentity:
    generation_id: int
    config_fingerprint: str
    content_revision: int
    embedding_dimensions: int


@dataclass(frozen=True, slots=True)
class EmbeddingModelMetadata:
    model_identifier: str
    model_revision: str
    dimensions: int
    device: str


@dataclass(frozen=True, slots=True)
class DenseVectorRecord:
    chunk_key: str
    document_version_id: int
    vector: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class DenseSearchHit:
    chunk_key: str
    rank: int
    distance: float


class EmbeddingService(Protocol):
    def model_metadata(self) -> EmbeddingModelMetadata: ...
    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]: ...
    def embed_query(self, text: str) -> tuple[float, ...]: ...
    def release_model(self) -> None: ...
    def close(self) -> None: ...


class DenseIndex(Protocol):
    def upsert(self, records: tuple[DenseVectorRecord, ...]) -> None: ...
    def search(
        self,
        vector: tuple[float, ...],
        *,
        limit: int,
        allowed_document_version_ids: tuple[int, ...],
    ) -> tuple[DenseSearchHit, ...]: ...
    def contains(self, chunk_keys: tuple[str, ...]) -> bool: ...
    def count(self) -> int: ...
    def close(self) -> None: ...


class DenseIndexFactory(Protocol):
    def open(self, generation: RagGenerationIdentity) -> DenseIndex: ...
    def remove(self, generation: RagGenerationIdentity) -> None: ...


@dataclass(frozen=True, slots=True)
class RagChunkSourceSpanValue:
    document_page_id: int
    page_number: int
    source_char_start: int
    source_char_end: int
    chunk_char_start: int
    chunk_char_end: int


@dataclass(frozen=True, slots=True)
class RetrievedEvidence:
    evidence_id: str
    chunk_key: str
    exact_text: str
    heading: str | None
    document_version_id: int
    document_title: str
    course_id: int
    source_spans: tuple[RagChunkSourceSpanValue, ...]
    dense_rank: int | None
    lexical_rank: int | None
    fused_rank: int
    generation_id: int
    content_revision: int


@dataclass(frozen=True, slots=True)
class RagClaim:
    claim_id: str
    text: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Citation:
    claim_id: str
    document_version_id: int
    document_title: str
    page_start: int
    page_end: int


@dataclass(frozen=True, slots=True)
class RagAnswer:
    status: RagAnswerStatus
    answer_text: str
    language: str
    claims: tuple[RagClaim, ...]
    citations: tuple[Citation, ...]
    generation_id: int
    content_revision: int


@dataclass(frozen=True, slots=True)
class RenderedRagResponse:
    answer: RagAnswer
    rendered_text: str


@dataclass(frozen=True, slots=True)
class IndexRunResult:
    safe_status: str
    generation_id: int
    content_revision: int
    indexed_documents: int
    not_searchable_documents: int


@dataclass(frozen=True, slots=True)
class RagIndexStatus:
    active_generation: RagGenerationIdentity | None
    ready_documents: int
    partial_documents: int
    not_searchable_documents: int
    chunks: int
    dense_index_healthy: bool


class LocalRagService:
    async def ask(self, query: RagQuery) -> RenderedRagResponse: ...


class RagIndexingService:
    def index_new(self, document_version_id: int | None = None) -> IndexRunResult: ...
    def rebuild(self) -> IndexRunResult: ...
    def status(self) -> RagIndexStatus: ...


@dataclass(slots=True)
class Phase4RagRuntime(AbstractContextManager["Phase4RagRuntime"]):
    service: LocalRagService
    indexing: RagIndexingService

    def close(self) -> None: ...
```

All public constructors validate positive internal IDs, nonblank bounded text, tuples rather than mutable collections, UTC timestamps, closed enums, monotonic character spans, and finite vectors. Safe `RagOperationalError` subclasses carry a fixed `RagErrorCode`; raw backend text is chained internally only.

## Persistence, Fingerprint, Revision, and FTS5 Decisions

- Migration `0004_phase4_local_rag` has `down_revision = "0003_phase3_document_ingestion"` and creates `rag_index_generations`, `rag_document_indexes`, `rag_chunks`, `rag_chunk_source_spans`, then `rag_chunks_fts`.
- `rag_index_generations` stores status, canonical config JSON, SHA-256 config fingerprint, embedding model identifier/revision/dimension, chunker/normalizer versions, `content_revision >= 0`, and UTC timestamps. A partial unique index permits only one `ACTIVE` row.
- `rag_document_indexes` uniquely binds `(generation_id, document_version_id)`, records PREPARING/READY/NOT_SEARCHABLE/FAILED, FULL/PARTIAL/NONE coverage, page/chunk counts, and safe error code.
- `rag_chunks` stores deterministic `chunk_key`, generation/document/ordinal/heading/exact text/token count/page bounds; keys hash canonical fields including source version SHA-256, chunker version, ordinal, exact text hash, and exact span manifest.
- `rag_chunk_source_spans` stores ordered page ID/number and `[page_char_start, page_char_end)` plus `[chunk_char_start, chunk_char_end)` constraints. No fuzzy page provenance is inferred.
- FTS5 is an ordinary derived virtual table with `normalized_text`, `generation_id UNINDEXED`, and `document_version_id UNINDEXED`; its `rowid` equals authoritative `rag_chunks.id`. Repository-controlled insert/delete occurs in the same SQLite transaction as chunk publication. No triggers or external-content magic obscure synchronization.
- Lexical retrieval joins FTS rowids back through authoritative chunk/document/generation tables and applies generation, READY, current/historical, and course predicates before accepting BM25 ranks.
- Canonical config fingerprint is `sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))`; payload contains every retrieval-affecting version/parameter and excludes machine paths/timestamps.
- `index-new` owns one non-reentrant writer lease for the whole batch and calls the public lease-aware `RagDocumentIndexer.stage_document_for_publication(...)` boundary. Staging commits PREPARING chunk/span/FTS state and Chroma vectors but no terminal document state. Its final single SQLite transaction verifies every staged document, flips every staged row to READY/NOT_SEARCHABLE, and performs guarded `content_revision` CAS exactly once when the represented corpus changes, including a newly published NOT_SEARCHABLE version. Failed/no-op runs do not increment it.
- Queries open one short SQLite read transaction and atomically pin the ACTIVE generation ID/fingerprint/revision, requested scope, latest published READY/NOT_SEARCHABLE version per logical resource, and final authorized READY document-version IDs. That transaction closes before retrieval. Dense and lexical search use the resulting immutable capability for the entire query. Its revision records the selected publication state but is not an expiring lease: later publication does not trigger current-revision, latest-version, scope, or ACTIVE-generation revalidation. A latest published NOT_SEARCHABLE version suppresses older READY versions in normal scope; newer PREPARING/FAILED versions do not hide the previous published version. Unpublished Chroma vectors cannot resolve and cause a safe integrity failure rather than partial retrieval.
- Rebuild writes a separate BUILDING generation and Chroma directory. Only a passed cross-store integrity transaction retires the old ACTIVE and activates the new generation. Failures preserve the prior ACTIVE generation; RETIRED generations are never automatically purged and remain available to already-pinned snapshots.

## Implementation Tasks

### Task 1: Verify and Pin Phase 4 Dependencies

**Files**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `tests/test_rag_dependencies.py`

**Interfaces**
- Consumes: current Python 3.11/Windows/CUDA environment and dependency policy.
- Produces: exact accepted `chromadb`, `FlagEmbedding` stack, and dev-only `ragas` pins for every later task.

**Requirements**
- Inspect official package metadata/changelogs/licenses and test resolution in a disposable uv context before editing.
- Record package purpose, group, selected version, license, Python/Windows/CUDA compatibility, network/model-download behavior, privacy defaults, and transitive conflicts in test comments/assertions.
- Ensure production modules cannot import `ragas`; dependency import checks must not download/load a model or create normal paths.

- [ ] **Step 1 — Write failing test:** add assertions that required distributions are installed at the chosen exact versions, `ragas` is in the dev group only, prohibited `PyMuPDF`/OCR packages remain absent, and importing packages creates no AAOS storage. Representative assertion after the version gate: `assert importlib.metadata.version("chromadb") == EXPECTED_CHROMADB_VERSION`.
- [ ] **Step 2 — Run RED:** `uv run pytest tests/test_rag_dependencies.py -v`; expect missing-distribution/version failures.
- [ ] **Step 3 — Minimal implementation:** after the compatibility/license gate, add exact production pins for `chromadb` and the approved BGE-M3 adapter stack, exact dev pin for `ragas`, run `uv lock`, and document the verified decision in the test module.
- [ ] **Step 4 — Run GREEN:** `uv run pytest tests/test_rag_dependencies.py -v`; expect PASS without model/network access.
- [ ] **Step 5 — Focused regression:** `uv run pytest tests/test_inference_imports.py tests/test_document_imports.py tests/test_rag_dependencies.py -v`.
- [ ] **Step 6 — Static/lock checks:** `uv lock --check`; `uv run ruff check tests/test_rag_dependencies.py`; `uv run mypy tests/test_rag_dependencies.py`.
- [ ] **Step 7 — Commit:** `git add pyproject.toml uv.lock tests/test_rag_dependencies.py`; `git commit -m "build(rag): pin local rag dependencies"`.

### Task 2: Add Typed RAG Contracts, Configuration, and Fingerprint

**Files**
- Create: `src/aaos/rag/__init__.py`
- Create: `src/aaos/rag/contracts.py`
- Create: `src/aaos/rag/config.py`
- Create: `tests/test_rag_contracts.py`
- Create: `tests/test_rag_config.py`

**Interfaces**
- Consumes: frozen Phase 3 IDs and existing dataclass/error conventions.
- Produces: the Core Interface Ledger types, `RagIndexConfig`, `RagRuntimePaths`, `compute_config_fingerprint()`.

**Requirements**
- Encode closed statuses/scopes/results/errors, validated retrieval/index parameters, disposable-injectable absolute paths, and canonical fingerprint fields.
- Include chunker, lexical normalizer, embedding model/revision/dimension, candidate depths, RRF constant, evidence budgets, and sufficiency policy version in the fingerprint; exclude storage paths and timestamps.

- [ ] **Step 1 — Write failing tests:** instantiate every enum/scope/query, reject invalid IDs/text/path escapes, prove semantically identical mappings fingerprint identically and every retrieval-affecting change alters the hash. Representative assertion: `assert compute_config_fingerprint(config_a) == compute_config_fingerprint(config_b)` for differently ordered equivalent mappings.
- [ ] **Step 2 — Run RED:** `uv run pytest tests/test_rag_contracts.py tests/test_rag_config.py -v`; expect import failures.
- [ ] **Step 3 — Minimal implementation:** add immutable types and canonical JSON SHA-256 implementation exactly as specified above.
- [ ] **Step 4 — Run GREEN:** rerun the RED command; expect PASS.
- [ ] **Step 5 — Focused regression:** `uv run pytest tests/test_config.py tests/test_document_config.py tests/test_rag_contracts.py tests/test_rag_config.py -v`.
- [ ] **Step 6 — Static checks:** `uv run ruff format --check src/aaos/rag tests/test_rag_contracts.py tests/test_rag_config.py`; `uv run ruff check src/aaos/rag tests/test_rag_contracts.py tests/test_rag_config.py`; `uv run mypy src/aaos/rag tests/test_rag_contracts.py tests/test_rag_config.py`.
- [ ] **Step 7 — Commit:** `git add src/aaos/rag/__init__.py src/aaos/rag/contracts.py src/aaos/rag/config.py tests/test_rag_contracts.py tests/test_rag_config.py`; `git commit -m "feat(rag): add typed contracts and configuration"`.

### Task 3: Add the Phase 4 Persistence Schema and FTS5 Migration

**Files**
- Create: `src/aaos/persistence/rag_models.py`
- Modify: `migrations/env.py`
- Create: `migrations/versions/0004_phase4_local_rag.py`
- Create: `tests/test_rag_migration.py`
- Modify: `tests/test_database.py`

**Interfaces**
- Consumes: shared `Base`, `UTCDateTime`, Phase 3 document/course foreign keys, revision `0003`.
- Produces: ORM mappings and migration schema described in Persistence Decisions.

**Requirements**
- Enforce exact foreign keys, closed status/coverage checks, positive dimensions/counts, unique generation/document and chunk identities, ordered spans, and one ACTIVE generation.
- Create/drop the ordinary FTS5 virtual table explicitly and verify FTS5 availability before migration use.

- [ ] **Step 1 — Write failing tests:** upgrade `0003 -> head`, inspect tables/indexes/checks/FKs/FTS5, reject invalid rows, verify downgrade to `0003`, and update only the legitimate head-schema expectation. Representative assertion: `assert "rag_chunks_fts" in sqlite_master_names(connection)`.
- [ ] **Step 2 — Run RED:** `uv run pytest tests/test_rag_migration.py tests/test_database.py -v`; expect missing revision/table failures.
- [ ] **Step 3 — Minimal implementation:** add mappings/import registration and explicit reversible Alembic operations; do not modify Phase 3 tables.
- [ ] **Step 4 — Run GREEN:** rerun the RED command; expect PASS.
- [ ] **Step 5 — Focused regression:** `uv run pytest tests/test_academic_migration.py tests/test_document_migration.py tests/test_rag_migration.py tests/test_database.py -v`.
- [ ] **Step 6 — Static checks:** `uv run ruff check src/aaos/persistence/rag_models.py migrations tests/test_rag_migration.py tests/test_database.py`; `uv run mypy src/aaos/persistence/rag_models.py tests/test_rag_migration.py`.
- [ ] **Step 7 — Commit:** `git add src/aaos/persistence/rag_models.py migrations/env.py migrations/versions/0004_phase4_local_rag.py tests/test_rag_migration.py tests/test_database.py`; `git commit -m "feat(rag): add generation and chunk persistence"`.

### Task 4: Implement the Authoritative RAG Repository

**Files**
- Create: `src/aaos/rag/repository.py`
- Create: `tests/test_rag_repository.py`

**Interfaces**
- Consumes: session factory, Phase 3 document mappings, Phase 4 ORM/contracts.
- Produces: `RagRepository`, `GenerationBuild`, `QuerySnapshot`, eligible-version queries, transactional generation/revision operations.

**Requirements**
- Read only active Phase 3 versions/pages; resolve current course/global and explicit historical versions separately.
- Expose no raw SQL/session/ORM objects above the repository; all mutation methods require a generation identity and expected revision.

- [ ] **Step 1 — Write failing tests:** prove current-version selection, superseded exclusion, historical explicit inclusion, course isolation, no eligible documents, one ACTIVE generation, revision compare-and-swap, and Phase 3 immutability. Representative assertion: `assert repository.resolve_current_versions(CourseScope(course_id=1)) == (current_version,)`.
- [ ] **Step 2 — Run RED:** `uv run pytest tests/test_rag_repository.py -v`; expect missing repository failures.
- [ ] **Step 3 — Minimal implementation:** add typed repository methods with short transactions and safe error translation.
- [ ] **Step 4 — Run GREEN:** rerun the RED command; expect PASS.
- [ ] **Step 5 — Focused regression:** `uv run pytest tests/test_document_persistence.py tests/test_rag_migration.py tests/test_rag_repository.py -v`.
- [ ] **Step 6 — Static checks:** `uv run ruff check src/aaos/rag/repository.py tests/test_rag_repository.py`; `uv run mypy src/aaos/rag/repository.py tests/test_rag_repository.py`.
- [ ] **Step 7 — Commit:** `git add src/aaos/rag/repository.py tests/test_rag_repository.py`; `git commit -m "feat(rag): add authoritative rag repository"`.

### Task 5: Implement Deterministic Structure-Aware Chunking and Provenance

**Files**
- Create: `src/aaos/rag/chunking.py`
- Create: `tests/test_rag_chunking.py`

**Interfaces**
- Consumes: ordered immutable `DocumentPage` text and `RagIndexConfig` chunk settings.
- Produces: `ChunkDraft` and exact `ChunkSourceSpanDraft` tuples with deterministic keys.

**Requirements**
- Prefer headings/paragraphs, target about 500 tokens, prefer 450–650, hard cap about 700, and use overlap only when splitting long sections.
- Preserve exact Unicode text and page/character mappings; FULL/PARTIAL/NONE must be deterministic, with empty/no-usable-text documents returning NONE rather than fabricated chunks.

- [ ] **Step 1 — Write failing tests:** cover headings, paragraphs, cross-page sections, long-section overlap, Arabic/English, empty pages, deterministic reruns, exact reconstruction from spans, keys, and FULL/PARTIAL/NONE. Representative assertion: `assert rebuild_text_from_spans(chunk, pages) == chunk.text`.
- [ ] **Step 2 — Run RED:** `uv run pytest tests/test_rag_chunking.py -v`; expect import failures.
- [ ] **Step 3 — Minimal implementation:** implement a versioned deterministic tokenizer boundary and pure chunker; no model/network dependency.
- [ ] **Step 4 — Run GREEN:** rerun the RED command; expect PASS.
- [ ] **Step 5 — Focused regression:** `uv run pytest tests/test_document_pdf.py tests/test_document_persistence.py tests/test_rag_chunking.py -v`.
- [ ] **Step 6 — Static checks:** `uv run ruff check src/aaos/rag/chunking.py tests/test_rag_chunking.py`; `uv run mypy src/aaos/rag/chunking.py tests/test_rag_chunking.py`.
- [ ] **Step 7 — Commit:** `git add src/aaos/rag/chunking.py tests/test_rag_chunking.py`; `git commit -m "feat(rag): add deterministic chunking and provenance"`.

### Task 6: Add Conservative Lexical Normalization and FTS5/BM25

**Files**
- Create: `src/aaos/rag/lexical.py`
- Modify: `src/aaos/rag/repository.py`
- Create: `tests/test_rag_lexical.py`
- Modify: `tests/test_rag_repository.py`

**Interfaces**
- Consumes: exact chunk text, normalized query, authorized `QuerySnapshot`.
- Produces: versioned normalized text, transactional FTS rows, scoped `LexicalSearchHit` ranks.

**Requirements**
- Normalize Unicode width/case/whitespace conservatively while retaining Arabic, digits, course codes, formulas, punctuation-bearing technical tokens, and original exact text in SQLite.
- FTS writes share the chunk transaction; BM25 results must join to READY authoritative rows and the pinned scope before ranking is accepted.
- Compile normalized natural-language queries deterministically: discard empty tokens, deduplicate repeated tokens in stable order, quote/escape every token as a literal FTS5 term, and join terms with explicit `OR` semantics. Empty usable-token queries return zero safely. Operators, column filters, `NEAR`, wildcards, parentheses, quotes, and malformed punctuation are data, never executable FTS syntax; no rewrite, stemming, translation, semantic expansion, or hidden fallback is allowed.
- The lexical candidate depth remains 30. This query compiler is multilingual (including Arabic and mixed Arabic/Latin) and does not apply an English-only stopword policy.

- [ ] **Step 1 — Write failing tests:** technical tokens/Arabic preservation, deterministic versioning, pure normalization, literal escaping, natural-language English/Arabic/mixed-language subset matching, repeated-token deduplication, empty-query safety, FTS insert/delete parity, BM25 ranks, course/history/generation filtering, and stale row rejection. Include syntax-like data (`OR`, `AND`, `NOT`, `NEAR`, parentheses, column syntax, wildcards, quotes, and malformed punctuation) and prove it cannot alter the query. Representative assertion: `assert lexical_search(query, snapshot).hits == (expected_authorized_hit,)`.
- [ ] **Step 2 — Run RED:** `uv run pytest tests/test_rag_lexical.py tests/test_rag_repository.py -v`; expect missing lexical/FTS behavior.
- [ ] **Step 3 — Minimal implementation:** add pure normalizer and parameterized FTS SQL owned by repository transactions.
- [ ] **Step 4 — Run GREEN:** rerun the RED command; expect PASS.
- [ ] **Step 5 — Focused regression:** `uv run pytest tests/test_rag_migration.py tests/test_rag_repository.py tests/test_rag_lexical.py -v`.
- [ ] **Step 6 — Static checks:** `uv run ruff check src/aaos/rag/lexical.py src/aaos/rag/repository.py tests/test_rag_lexical.py`; `uv run mypy src/aaos/rag/lexical.py src/aaos/rag/repository.py`.
- [ ] **Step 7 — Commit:** `git add src/aaos/rag/lexical.py src/aaos/rag/repository.py tests/test_rag_lexical.py tests/test_rag_repository.py`; `git commit -m "feat(rag): add scoped fts5 retrieval substrate"`.

## Checkpoint A — Persistence and Chunking Substrate

Require `uv run pytest tests/test_rag_contracts.py tests/test_rag_config.py tests/test_rag_migration.py tests/test_rag_repository.py tests/test_rag_chunking.py tests/test_rag_lexical.py -v`, relevant Phase 3 persistence regressions, Ruff, strict mypy, `uv lock --check`, disposable-path audit, migration upgrade/downgrade review, and a human review confirming Phase 3 tables/content are read-only before Task 7.

### Task 7: Define Dense Index Abstractions and Deterministic Fakes

**Files**
- Create: `src/aaos/rag/dense.py`
- Create: `tests/test_rag_dense.py`

**Interfaces**
- Consumes: generation identity, fixed-dimension vectors, deterministic chunk keys.
- Produces: `DenseIndex`, `DenseIndexFactory`, `DenseVectorRecord`, `DenseSearchHit`, in-memory fake.

**Requirements**
- Keep Chroma types/private IDs behind the protocol; validate finite dimensions and deterministic rank ordering.

- [ ] **Step 1 — Write failing tests:** protocol fake upsert/search/contains/count/close, allowed-version filtering, dimension mismatch, nonfinite vector, duplicate key, and deterministic ties. Representative assertion: `assert index.search(vector, limit=2, allowed_document_version_ids=(7,)) == (hit_a, hit_b)`.
- [ ] **Step 2 — Run RED:** `uv run pytest tests/test_rag_dense.py -v`; expect import failure.
- [ ] **Step 3 — Minimal implementation:** add protocols, values, and deterministic fake used by later unit tests.
- [ ] **Step 4 — Run GREEN:** rerun RED command; expect PASS.
- [ ] **Step 5 — Focused regression:** `uv run pytest tests/test_rag_contracts.py tests/test_rag_dense.py -v`.
- [ ] **Step 6 — Static checks:** `uv run ruff check src/aaos/rag/dense.py tests/test_rag_dense.py`; `uv run mypy src/aaos/rag/dense.py tests/test_rag_dense.py`.
- [ ] **Step 7 — Commit:** `git add src/aaos/rag/dense.py tests/test_rag_dense.py`; `git commit -m "feat(rag): define dense index boundary"`.

### Task 8: Implement the Disposable Per-Generation Chroma Adapter

**Files**
- Create: `src/aaos/rag/chroma.py`
- Create: `tests/test_rag_chroma.py`

**Interfaces**
- Consumes: `DenseIndexFactory`, validated generation path/identity and model dimension.
- Produces: `ChromaDenseIndexFactory` and private `ChromaDenseIndex`.

**Requirements**
- One isolated collection/path per generation, telemetry disabled, caller-provided embeddings only, no Chroma embedding/network function, no cross-generation query.
- Tests use temporary directories and verify close/delete behavior without normal paths.

- [ ] **Step 1 — Write failing tests:** disposable open/reopen, exact vector count, query ranks, generation isolation, wrong dimension, telemetry config, close idempotence, and no normal-directory creation. Representative assertion: `assert factory.open(generation_b).count() == 0` after writing only generation A.
- [ ] **Step 2 — Run RED:** `uv run pytest tests/test_rag_chroma.py -v`; expect missing adapter failure.
- [ ] **Step 3 — Minimal implementation:** wrap only required embedded Chroma calls and translate exceptions to safe dense-index errors.
- [ ] **Step 4 — Run GREEN:** rerun RED command; expect PASS.
- [ ] **Step 5 — Focused regression:** `uv run pytest tests/test_rag_dense.py tests/test_rag_chroma.py -v`.
- [ ] **Step 6 — Static checks:** `uv run ruff check src/aaos/rag/chroma.py tests/test_rag_chroma.py`; `uv run mypy src/aaos/rag/chroma.py tests/test_rag_chroma.py`.
- [ ] **Step 7 — Commit:** `git add src/aaos/rag/chroma.py tests/test_rag_chroma.py`; `git commit -m "feat(rag): add isolated chroma dense index"`.

### Task 9: Implement EmbeddingService and BGE-M3 Lifecycle

**Files**
- Create: `src/aaos/rag/embeddings.py`
- Create: `tests/test_rag_embeddings.py`

**Interfaces**
- Consumes: selected Task 1 BGE-M3 package, local model identifier/cache configuration.
- Produces: `EmbeddingService`, `EmbeddingModelMetadata`, `FakeEmbeddingService`, `BgeM3EmbeddingService`.

**Requirements**
- Lazily load only on first embed, validate model/revision/dimension, batch within 6 GB VRAM, and implement idempotent `release_model()` that drops model/tokenizer references, runs Python GC, calls CUDA cache release when available, and permits a later explicit lazy reload. `close()` permanently closes the adapter and calls `release_model()` once.
- Ordinary tests monkeypatch the backend and never download a model or contact LM Studio. The runtime amendment below adds a deterministic lifecycle controller only to explicit Phase 4 runtime/acceptance composition; it does not make simultaneous LM Studio residency a requirement.

- [ ] **Step 1 — Write failing tests:** lazy load, metadata fingerprint inputs, deterministic fake, batch/dimension checks, `release_model()`/reload, close sequence/idempotence, construction failure cleanup, CPU-safe behavior, and no import-time model/cache/network action. Representative assertion: `assert backend_factory.calls == 0` immediately after service construction.
- [ ] **Step 2 — Run RED:** `uv run pytest tests/test_rag_embeddings.py -v`; expect import failure.
- [ ] **Step 3 — Minimal implementation:** build the protocol adapter with injected backend factory and explicit lifecycle.
- [ ] **Step 4 — Run GREEN:** rerun RED command; expect PASS with fake backend.
- [ ] **Step 5 — Focused regression:** `uv run pytest tests/test_rag_dependencies.py tests/test_rag_embeddings.py tests/test_inference_imports.py -v`.
- [ ] **Step 6 — Static checks:** `uv run ruff check src/aaos/rag/embeddings.py tests/test_rag_embeddings.py`; `uv run mypy src/aaos/rag/embeddings.py tests/test_rag_embeddings.py`.
- [ ] **Step 7 — Commit:** `git add src/aaos/rag/embeddings.py tests/test_rag_embeddings.py`; `git commit -m "feat(rag): add bge m3 embedding lifecycle"`.

### Task 10: Add the Exclusive RAG Index Writer Lock

**Files**
- Create: `src/aaos/rag/lock.py`
- Create: `tests/test_rag_lock.py`

**Interfaces**
- Consumes: absolute confined `RagRuntimePaths.index_lock_path`.
- Produces: `RagIndexLock.acquire() -> RagIndexLease` and `RagIndexLock.assert_owned(lease: RagIndexLease) -> None`.

**Requirements**
- Mirror the proven nonce/file-identity/reparse-safe ownership semantics without sharing Phase 3 lock state; all index-new and rebuild mutations require one lease.
- `assert_owned()` re-reads the marker and accepts only an unreleased lease whose lock path is this exact `RagIndexLock` path and whose nonce plus file identity still match the marker. It raises the existing safe `RagIndexLockUnavailable` for a released lease, another lock's lease, a missing/replaced/malformed marker, or nonce/identity mismatch. It never acquires, adopts, renews, or releases a lease and does not make the lock reentrant.

- [ ] **Step 1 — Write failing tests:** exclusive acquisition, malformed/reparse paths, owner-only idempotent release, replaced lock refusal, mutation not started on contention, and ownership validation. Add `test_assert_owned_accepts_current_owner`, `test_assert_owned_rejects_released_lease`, `test_assert_owned_rejects_foreign_lock_lease`, and `test_assert_owned_rejects_replaced_marker`; assert `with pytest.raises(RagIndexLockUnavailable): lock.assert_owned(lease)` for every invalid capability.
- [ ] **Step 2 — Run RED:** `uv run pytest tests/test_rag_lock.py -k "assert_owned" -v`; expect `RagIndexLock` to lack the ownership-validation method.
- [ ] **Step 3 — Minimal implementation:** retain the existing distinct lock/lease types and add only the `assert_owned()` revalidation described above.
- [ ] **Step 4 — Run GREEN:** rerun RED command; expect PASS.
- [ ] **Step 5 — Focused regression:** `uv run pytest tests/test_document_lock.py tests/test_rag_lock.py -v`.
- [ ] **Step 6 — Static checks:** `uv run ruff check src/aaos/rag/lock.py tests/test_rag_lock.py`; `uv run mypy src/aaos/rag/lock.py tests/test_rag_lock.py`.
- [ ] **Step 7 — Commit:** `git add src/aaos/rag/lock.py tests/test_rag_lock.py`; `git commit -m "feat(rag): add exclusive index writer lock"`.

### Task 11: Implement Per-Document Cross-Store Indexing

**Files**
- Create: `src/aaos/rag/indexing.py`
- Modify: `src/aaos/rag/repository.py`
- Create: `tests/test_rag_indexing.py`
- Modify: `tests/test_rag_repository.py`

**Interfaces**
- Consumes: repository, chunker, embedding service, dense factory, `RagIndexLock`, optional already-owned `RagIndexLease`, and exact generation identity.
- Produces:
  - `RagDocumentIndexer.prepare_document(generation: RagGenerationIdentity, document_version_id: int) -> DocumentIndexResult` for standalone BUILDING-generation preparation.
  - `RagDocumentIndexer.stage_document_for_publication(lease: RagIndexLease, generation: RagGenerationIdentity, document_version_id: int) -> StagedDocumentIndexResult` for ACTIVE-generation staging under an outer owner.
  - `RagDocumentIndexer.release_embedding_model() -> None`, delegating only the existing injected `EmbeddingService.release_model()` boundary so one batch can reuse the model and then reclaim it deterministically without closing the runtime-owned service.
  - `StagedDocumentIndexResult(generation: RagGenerationIdentity, document_version_id: int, intended_status: DocumentIndexStatus, coverage_status: CoverageStatus, searchable_page_count: int, chunk_count: int, verification: DocumentVerificationRecord | None)`; `intended_status` is exactly READY or NOT_SEARCHABLE, and `verification` is required only for READY.
  - Define `StagedDocumentIndexResult` in `src/aaos/rag/repository.py` beside `DocumentVerificationRecord` so repository staging/publication primitives can consume it without an `indexing`/`repository` import cycle.

**Requirements**
- `prepare_document()` acquires/releases its own lease, requires a BUILDING generation, performs the existing cross-store verification, and may publish READY/NOT_SEARCHABLE because BUILDING is not query-visible. Task 13 rebuild uses this mode.
- `stage_document_for_publication()` calls `RagIndexLock.assert_owned(lease)` before every mutation boundary, requires the exact ACTIVE generation identity/revision, and never reacquires or releases the caller's lease. It shares the deterministic Task 11 preparation pipeline but leaves the row PREPARING after successful chunk/span/FTS, embedding, dense upsert, expected-key verification, and authoritative count/provenance verification.
- Add `RagRepository.record_staged_document(identity: RagGenerationIdentity, *, expected_revision: int, document_version_id: int, intended_status: DocumentIndexStatus, coverage_status: CoverageStatus, searchable_page_count: int, chunk_count: int, verification: DocumentVerificationRecord | None) -> DocumentIndexRecord`. This short SQLite transaction validates exact state/counts and records coherent PREPARING metadata without changing the terminal status.
- Searchable staging returns intended READY with FULL/PARTIAL coverage and matching verification. Empty/no-text staging remains PREPARING with intended NOT_SEARCHABLE, NONE coverage, zero counts, and no verification. A failure uses the existing sanitized FAILED/compensation path and never returns a staged result.
- Task 12 calls `release_embedding_model()` exactly once after its last staging attempt on success or failure and before any successful publication transaction. A release failure prevents publication and leaves the revision unchanged; runtime ownership and final `EmbeddingService.close()` remain outside Task 12.

- [ ] **Step 1 — Write failing tests:** preserve standalone FULL/PARTIAL/NONE and failure tests, then add lease-aware staging tests: valid owner accepted without a second acquisition; released/foreign/replaced lease rejected before mutation; ACTIVE required; searchable and empty results remain PREPARING; intended terminal type is closed; exact counts/provenance survive; failure records FAILED; caller retains/release ownership; and no Phase 3 mutation. Representative assertions: `assert stored.status is DocumentIndexStatus.PREPARING` and `assert staged.intended_status is DocumentIndexStatus.READY`.
- [ ] **Step 2 — Run RED:** `uv run pytest tests/test_rag_indexing.py -k "stage_document or standalone" -v`; expect missing lease-aware staging and ownership validation while existing standalone tests remain green.
- [ ] **Step 3 — Minimal implementation:** preserve the public standalone locking boundary, extract only a shared private preparation pipeline inside `RagDocumentIndexer`, and add the public lease-aware staging boundary plus repository PREPARING metadata validation. Task 12 calls only the public staging method, never the private helper.
- [ ] **Step 4 — Run GREEN:** rerun RED command; expect PASS.
- [ ] **Step 5 — Focused regression:** `uv run pytest tests/test_rag_chunking.py tests/test_rag_chroma.py tests/test_rag_embeddings.py tests/test_rag_indexing.py -v`.
- [ ] **Step 6 — Static checks:** `uv run ruff check src/aaos/rag/lock.py src/aaos/rag/indexing.py src/aaos/rag/repository.py tests/test_rag_lock.py tests/test_rag_indexing.py`; `uv run mypy src/aaos/rag/lock.py src/aaos/rag/indexing.py src/aaos/rag/repository.py`.
- [ ] **Step 7 — Commit:** `git add src/aaos/rag/indexing.py src/aaos/rag/repository.py tests/test_rag_indexing.py tests/test_rag_repository.py`; `git commit -m "feat(rag): add cross store document indexing"`.

### Task 12: Implement Atomic `index-new` Publication

**Files**
- Modify: `src/aaos/rag/lock.py`
- Modify: `src/aaos/rag/indexing.py`
- Modify: `src/aaos/rag/repository.py`
- Modify: `tests/test_rag_lock.py`
- Modify: `tests/test_rag_indexing.py`
- Modify: `tests/test_rag_repository.py`

**Interfaces**
- Consumes: `RagRepository`, `RagDocumentIndexer`, `RagIndexLock`, exact ACTIVE generation, eligible current Phase 3 versions, and optional positive internal document-version ID.
- Produces: `RagIndexingCoordinator.index_new(document_version_id: int | None = None) -> IndexRunResult`. Task 12 intentionally implements only this method; Task 13 adds `rebuild()` and `status()` before the coordinator is exposed through the complete `RagIndexingService` protocol.
- Repository publication boundary: `RagRepository.publish_staged_documents(identity: RagGenerationIdentity, *, expected_revision: int, staged_documents: tuple[StagedDocumentIndexResult, ...]) -> RagGenerationIdentity`.

**Requirements**
- `index_new()` acquires exactly one outer lease and keeps it through ACTIVE-generation pinning, deterministic candidate discovery, serial staging, final publication, and cleanup. It passes the same lease to `stage_document_for_publication()`; it never calls standalone `prepare_document()`, reacquires the lock, releases between documents, or parallelizes work.
- Candidate discovery uses trusted Phase 3 current/latest versions in stable `(document_id, version_number, document_version_id)` order. With no explicit ID, stage current versions not successfully published in the pinned ACTIVE generation, including retryable PREPARING/FAILED versions. With one ID, require that positive ID to be the current eligible version. Skip unchanged READY/NOT_SEARCHABLE versions. Do not index historical superseded versions or crawl external state.
- After every candidate returns a valid staged result, call `publish_staged_documents()` once. In one short SQLite transaction it must: require the exact ACTIVE generation and revision `R`; require a nonempty, duplicate-free staged tuple whose identities match that generation; re-read every row; require PREPARING plus exact staged coverage/counts; re-run authoritative SQLite/FTS/provenance verification for intended READY; require NONE/zero/no verification for intended NOT_SEARCHABLE; transition all rows to their intended terminal states; execute guarded CAS `R -> R+1`; flush; and return the `R+1` identity. Any error rolls back every terminal transition and the CAS together.
- Refactor the existing Task 4 guarded revision update into one private session-scoped CAS helper used by both `bump_content_revision()` and `publish_staged_documents()`; do not duplicate or weaken its generation ID, fingerprint, dimensions, expected-revision, and one-row update checks.
- Do not hold the final SQLite transaction across chunking, embedding, model lifecycle, Chroma, or network. SQLite is authoritative; unpublished deterministic Chroma vectors may remain after failure and converge on retry.
- No candidates means deterministic no-op: no staging, embeddings, terminal transitions, or revision increment. A newly published NOT_SEARCHABLE version is a corpus change and increments once because it supersedes the previous current version. Multiple successful candidates increment once per batch, never per document.
- If any staging fails, do not call publication: successful siblings remain PREPARING, failed rows remain sanitized FAILED, revision stays `R`, and previously published versions remain current. CAS mismatch or injected publication failure rolls back all staged terminal transitions and leaves `R` unchanged. Always release the outer lease; do not add retries or Task 13 recovery.
- No URL/path/cmid input, generation activation/retirement, rebuild, query retrieval, schema migration, or partial terminal publication.

- [ ] **Step 1 — Write failing tests:** add exact tests for all eligible/one current ID, invalid/ineligible/historical ID, stable order, READY/NOT_SEARCHABLE skip, PREPARING/FAILED retry, PARTIAL acceptance, two-document single increment, no-op +0, new current V2, V2 NOT_SEARCHABLE supersession, lock contention, same-lease composition, document failure at first/middle/final position, failure after all staging before publication, CAS mismatch, publication persistence failure, rollback of every terminal row, one model release on success/failure before publication, release failure blocking publication, lease release, idempotent retry, no duplicate chunks/vectors, Phase 3 immutability, and no Task 13 symbols. Representative assertions:
  - `assert calls == [(lease, first_id), (lease, second_id)]` and both calls receive the identical lease object;
  - `assert repository.get_generation(generation.id).identity.content_revision == revision + 1` after a successful nonempty batch;
  - `assert revision_after == revision_before` and every successfully staged sibling remains PREPARING after any failed batch;
  - after V2 NOT_SEARCHABLE publication, current published selection returns V2 as the superseding version while its searchable authorized ID set is empty.
- [ ] **Step 2 — Run RED:** `uv run pytest tests/test_rag_indexing.py -k "index_new or content_revision" -v`; expect orchestration failures.
- [ ] **Step 3 — Minimal implementation:** add `assert_owned`, typed staging, ACTIVE candidate discovery, `RagIndexingCoordinator.index_new()`, and the single repository publication transaction exactly as specified above. Reuse Task 11 cross-store code; do not call private `_prepare_locked()` from Task 12 or duplicate its pipeline.
- [ ] **Step 4 — Run GREEN:** rerun RED command; expect PASS.
- [ ] **Step 5 — Focused regression:** `uv run pytest tests/test_rag_lock.py tests/test_rag_repository.py tests/test_rag_indexing.py -v`.
- [ ] **Step 6 — Static checks:** `uv run ruff check src/aaos/rag/lock.py src/aaos/rag/indexing.py src/aaos/rag/repository.py tests/test_rag_lock.py tests/test_rag_indexing.py`; `uv run mypy src/aaos/rag/lock.py src/aaos/rag/indexing.py src/aaos/rag/repository.py`.
- [ ] **Step 7 — Commit:** `git add src/aaos/rag/lock.py src/aaos/rag/indexing.py src/aaos/rag/repository.py tests/test_rag_lock.py tests/test_rag_indexing.py tests/test_rag_repository.py`; `git commit -m "feat(rag): add atomic incremental indexing"`.

### Task 13: Implement Blue/Green Rebuild, Integrity, and Recovery

**Files**
- Modify: `src/aaos/rag/indexing.py`
- Modify: `src/aaos/rag/repository.py`
- Create: `tests/test_rag_rebuild.py`

**Interfaces**
- Consumes: writer lease, full eligible current corpus, new config fingerprint.
- Produces: `RagIndexingCoordinator.rebuild()`, `RagIndexingCoordinator.status()`, `verify_generation()`, `recover_building_generations()`, and the complete `RagIndexingService` protocol surface.

**Requirements**
- Build isolated BUILDING SQLite/Chroma state, verify counts/dimensions/keys/FTS/provenance/document statuses, atomically RETIRE old and ACTIVE new, never purge retired data.
- Preserve the RETIRED generation's SQLite, FTS, and generation-isolated Chroma state so a query that pinned it before activation can complete. This does not make BUILDING query-visible and does not add automatic purge or in-flight reference counting.
- Rebuild uses standalone `RagDocumentIndexer.prepare_document()` for the BUILDING generation; individual READY/NOT_SEARCHABLE rows are safe because BUILDING is query-invisible. It does not use ACTIVE-generation staging or Task 12's content-revision publication transaction.
- Startup/status identifies abandoned BUILDING/PREPARING state; recovery may mark it failed/clean derived Chroma only under the writer lease and never changes ACTIVE.

- [ ] **Step 1 — Write failing tests:** successful activation, failed embedding/Chroma/FTS/integrity preserving ACTIVE, activation transaction failure, abandoned BUILDING recovery, cross-store mismatch, and no retired purge. Representative assertion: `assert repository.get_active_generation().id == old_active.id` after injected rebuild failure.
- [ ] **Step 2 — Run RED:** `uv run pytest tests/test_rag_rebuild.py -v`; expect missing rebuild/recovery behavior.
- [ ] **Step 3 — Minimal implementation:** add explicit integrity report and one atomic activation transaction.
- [ ] **Step 4 — Run GREEN:** rerun RED command; expect PASS.
- [ ] **Step 5 — Focused regression:** `uv run pytest tests/test_rag_indexing.py tests/test_rag_rebuild.py tests/test_rag_chroma.py -v`.
- [ ] **Step 6 — Static checks:** `uv run ruff check src/aaos/rag/indexing.py src/aaos/rag/repository.py tests/test_rag_rebuild.py`; `uv run mypy src/aaos/rag/indexing.py src/aaos/rag/repository.py`.
- [ ] **Step 7 — Commit:** `git add src/aaos/rag/indexing.py src/aaos/rag/repository.py tests/test_rag_rebuild.py`; `git commit -m "feat(rag): add blue green rebuild and recovery"`.

## Checkpoint B — Index Infrastructure

Require all Tasks 7–13 tests, Checkpoint A tests, disposable Chroma inspection, injected failure/crash review, no normal-path artifacts, `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy src tests`, `uv lock --check`, and human confirmation that GPU lifecycle acceptance remains deferred before Task 14.

### Task 14: Implement Typed Scope Resolution and Pinned Retrieval Snapshots

**Files**
- Create: `src/aaos/rag/retrieval.py`
- Modify: `src/aaos/rag/repository.py`
- Create: `tests/test_rag_retrieval.py`

**Interfaces**
- Consumes: `RagQuery`, active generation/revision, Phase 3 current/historical relationships.
- Produces: immutable `QuerySnapshot`, normalized query, authorized chunk universe, dense and lexical candidate lists.

**Requirements**
- In one SQLite read transaction, resolve ACTIVE generation identity, content_revision, requested scope, latest successfully published READY/NOT_SEARCHABLE version per logical resource, and final authorized READY document-version IDs; freeze all of them in one immutable `QuerySnapshot`. Dense and lexical search must use those frozen IDs and must not re-resolve current versions after the snapshot.
- Course/global current scope ignores newer PREPARING/FAILED versions. Latest published READY contributes its version ID. Latest published NOT_SEARCHABLE contributes no searchable ID and must suppress fallback to an older READY version. Historical requires explicit version IDs: prefer the ACTIVE generation when it contains every requested published version; otherwise pin the newest RETIRED generation containing every requested published version. Reject a request whose versions do not share one trusted generation; never merge generations or leak current/sibling course records.
- Treat the immutable `QuerySnapshot` as a durable in-flight query capability. Its short creation transaction closes before embedding and retrieval. Later revision publication or generation activation does not invalidate it, and no later step re-resolves current scope, latest versions, ACTIVE generation, or current revision. Task 15 owns snapshot-safe authoritative chunk materialization; unresolved Chroma keys still fail with `RETRIEVAL_INCONSISTENCY`.
- Call `EmbeddingService.release_model()` in a `finally` boundary immediately after query embedding/dense search; lexical fusion/evidence handling then proceed without an embedding model resident, and no generation call can begin before release completes.

- [ ] **Step 1 — Write failing tests:** no ACTIVE, invalid scope, course isolation, global current-only, explicit history, mixed unauthorized history, atomic generation/revision/version-universe pinning, stale vector, PREPARING/FAILED exclusion, V1 READY plus V2 PREPARING/FAILED selecting V1, V1 READY plus V2 NOT_SEARCHABLE selecting no searchable ID, publication between two queries producing separate consistent snapshots, and query normalization. Representative assertions: `assert snapshot.document_version_ids == (authorized_current_version_id,)` and `assert (q1.generation.content_revision, q1.document_version_ids) == (revision_r, old_ids)` while a later `q2` equals `(revision_r_plus_1, new_ids)`.
- [ ] **Step 2 — Run RED:** `uv run pytest tests/test_rag_retrieval.py -v`; expect missing retrieval service.
- [ ] **Step 3 — Minimal implementation:** add one repository snapshot method that resolves generation, revision, scope, latest-published selection, and authorized IDs inside one read transaction; then add `HybridRetriever.retrieve_candidates(query) -> RetrievalCandidateSet` with injected embedding/`DenseIndexFactory`/repository/lexical services. After resolving the immutable snapshot, open the dense handle for exactly `snapshot.generation`, pass only `snapshot.document_version_ids` into `DenseIndex.search(...)`, close that query-owned handle in `finally`, and pass the snapshot's SQL predicates into lexical search before either rank list is produced. Never bind a construction-time ACTIVE-generation handle.
- [ ] **Step 4 — Run GREEN:** rerun RED command; expect PASS.
- [ ] **Step 5 — Focused regression:** `uv run pytest tests/test_rag_repository.py tests/test_rag_lexical.py tests/test_rag_dense.py tests/test_rag_retrieval.py -v`.
- [ ] **Step 6 — Static checks:** `uv run ruff check src/aaos/rag/retrieval.py src/aaos/rag/repository.py tests/test_rag_retrieval.py`; `uv run mypy src/aaos/rag/retrieval.py src/aaos/rag/repository.py`.
- [ ] **Step 7 — Commit:** `git add src/aaos/rag/retrieval.py src/aaos/rag/repository.py tests/test_rag_retrieval.py`; `git commit -m "feat(rag): add scoped retrieval snapshots"`.

### Task 15: Add Deterministic RRF and Authoritative Candidate Verification

**Files**
- Modify: `src/aaos/rag/retrieval.py`
- Modify: `src/aaos/rag/repository.py`
- Modify: `tests/test_rag_retrieval.py`
- Modify: `tests/test_rag_repository.py`

**Interfaces**
- Consumes: ranked `DenseSearchHit`/`LexicalSearchHit` lists, pinned authorized universe, and a generation-capable `DenseIndexFactory`.
- Produces: ordered `FusedCandidate` tuples with dense/lexical/fused ranks.
- `HybridRetriever` accepts `dense_index_factory: DenseIndexFactory` rather than a
  generation-specific `dense_index`. After `QuerySnapshot` resolution it calls
  `dense_index_factory.open(snapshot.generation)`, constrains the search to
  `snapshot.document_version_ids`, and closes that query-owned handle in a
  `finally` boundary. The factory is generation-independent and callers never
  share ownership of an opened handle.
- Repository materialization boundary:

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

  The returned tuple is ordered exactly like `chunk_keys`. `QuerySnapshot` and
  all returned record types are repository-owned, avoiding a circular import.

**Requirements**
- Score `sum(1 / (rrf_k + rank))`, rank from one, combine no raw cosine/BM25 values, break ties by best component rank then deterministic chunk key.
- Preserve lexical depth 30 and RRF `k=60`; add a regression where a natural-language query produces authorized lexical candidates and hybrid fusion receives both dense and lexical ranked lists without combining raw scores.
- `materialize_snapshot_chunks()` returns `()` for an empty key tuple without re-resolving the snapshot. Otherwise it uses one short read-only SQLite transaction for the complete deduplicated key tuple. It validates unique nonblank requested keys; the pinned generation row's ID/fingerprint/embedding dimensions; ACTIVE-or-RETIRED but never BUILDING generation status; exact authoritative chunk identity and pinned-generation membership; frozen authorized document-version membership; READY index state in that pinned generation; exact generation-scoped FTS membership; immutable Phase 3 document-version identity; and ordered page/character provenance whose relationships reproduce the authoritative chunk text. Missing, duplicate, ambiguous, corrupted, unauthorized, cross-generation, or persistence failures are translated to `RETRIEVAL_INCONSISTENCY` without backend text.
- Materialization is integrity-only. It must not require the repository's current `content_revision` to equal the snapshot revision, require the snapshot generation to remain ACTIVE, ask whether a frozen document version is still latest, perform another ACTIVE-generation lookup, or re-resolve scope. Chroma metadata, dense-hit metadata, and lexical-hit document IDs are never authoritative.
- Remove the same mutable revision/ACTIVE revalidation from snapshot-scoped `RagRepository.lexical_search()`. Its short FTS transaction must use only the pinned generation ID and frozen authorized document-version IDs while retaining READY-index and generation-scoped FTS joins. This is not a new lexical policy; it makes the existing Task 14 search honor the durable snapshot capability.
- Every fused candidate must correspond one-to-one with the authoritative tuple returned in fused candidate order. A candidate from another generation or outside `snapshot.document_version_ids` fails the whole query safely. No long SQLite transaction spans embedding, dense/lexical search, RRF, selection, or generation.
- Dense retrieval is selected by the pinned snapshot, not by runtime construction or a later ACTIVE lookup: G1 snapshots use G1 dense state, G2 snapshots use G2 state, explicitly authorized RETIRED historical snapshots use their RETIRED dense state, and BUILDING generations are rejected. A G1 snapshot can finish after G1 is retired and a later G2 query can run through the same runtime. The query owns and closes only its own opened dense handle; the runtime owns the generation-independent factory.

- [ ] **Step 1 — Write failing tests:** preserve both-list, dense-only, lexical-only, ties, config change, duplicate hits, stale key, wrong generation/scope, missing provenance, and deterministic rerun coverage. Add repository materialization tests for exact requested order, malformed/duplicate/missing keys, BUILDING rejection, ACTIVE and RETIRED acceptance, READY/FTS/provenance integrity, wrong generation, and unauthorized version. Add two deterministic full-retrieval race regressions with explicit state transitions rather than sleeps: (a) create S1 at G1/R/{V1}, publish V2 with Task 12 semantics to G1/R+1, prove S1's FTS search and materialization still use V1 without scope/revision revalidation and a later S2 pins R+1/new IDs; (b) create S1 at G1, activate G2 with Task 13 semantics, prove S1's FTS search and materialization still use only G1 after retirement, reject G2 candidates, and a later S2 pins G2. Representative assertions: `assert reciprocal_rank_fusion(dense, lexical, rrf_k=60)[0].chunk_key == shared_key`, `assert materialized[0].chunk.chunk_key == requested_keys[0]`, and `assert s1.generation.content_revision == revision_r` after publication to `revision_r_plus_1`.
- [ ] **Step 2 — Run RED:** `uv run pytest tests/test_rag_retrieval.py -k "rrf or candidate" -v`; expect missing fusion/verification.
- [ ] **Step 3 — Minimal implementation:** preserve the pure `reciprocal_rank_fusion()` work, make existing snapshot-scoped lexical retrieval independent of mutable current revision/ACTIVE status, add `AuthoritativeRetrievalChunk` and the exact repository materialization method above, then make retrieval verify/materialize fused chunk keys through that method before returning candidates. The method reads all requested keys and provenance in one short transaction, preserves requested order, and translates every integrity failure to `RETRIEVAL_INCONSISTENCY`; neither read path calls revision-sensitive current-state repository APIs.
- [ ] **Step 4 — Run GREEN:** rerun RED command; expect PASS.
- [ ] **Step 5 — Focused regression:** `uv run pytest tests/test_rag_repository.py tests/test_rag_retrieval.py tests/test_rag_indexing.py tests/test_rag_rebuild.py -v`.
- [ ] **Step 6 — Static checks:** `uv run ruff check src/aaos/rag/repository.py src/aaos/rag/retrieval.py tests/test_rag_repository.py tests/test_rag_retrieval.py`; `uv run mypy src/aaos/rag/repository.py src/aaos/rag/retrieval.py tests/test_rag_repository.py tests/test_rag_retrieval.py`.
- [ ] **Step 7 — Commit:** `git add src/aaos/rag/repository.py src/aaos/rag/retrieval.py tests/test_rag_repository.py tests/test_rag_retrieval.py`; `git commit -m "feat(rag): add deterministic hybrid fusion"`.

### Task 16: Implement Evidence Selection and Deterministic Sufficiency

**Files**
- Modify: `src/aaos/rag/retrieval.py`
- Create: `tests/test_rag_evidence.py`

**Interfaces**
- Consumes: verified fused candidates and configured evidence/sufficiency policy.
- Produces: `RetrievedEvidence` with query-local IDs and `SufficiencyDecision`.

**Requirements**
- Deduplicate logical evidence, suppress excessive adjacent overlap, preserve useful source diversity while relevance leads, and enforce both chunk and token budgets.
- Evidence IDs are unpredictable request-local capabilities mapped only to evidence supplied in that request; retain conflicting valid sources.
- The deterministic structural gate returns sufficient/partially sufficient/insufficient facts; structural insufficiency prevents semantic classification and factual answer generation. Structurally eligible evidence is passed to the separately scoped, local-only atomic decomposition, extractive span, and independent entailment stages defined by the approved amendment; deterministic code validates and aggregates their judgments into internal `SUPPORTED`/`PARTIAL`/`UNSUPPORTED` without making that internal status a public answer status.

- [ ] **Step 1 — Write failing tests:** duplicate/neighbor suppression, multi-document diversity, 4–8 configurable chunk target, token cap, deterministic ordering, query-local ID isolation, conflict retention, and conservative sufficiency/false-accept cases. Representative assertion: `assert sum(item.token_count for item in selected) <= config.max_evidence_tokens`.
- [ ] **Step 2 — Run RED:** `uv run pytest tests/test_rag_evidence.py -v`; expect missing selector/gate.
- [ ] **Step 3 — Minimal implementation:** add `EvidenceSelector.select()` and versioned `EvidenceSufficiencyGate.evaluate()` pure logic with no LLM decision. The later approved semantic amendment composes atomic decomposition, extractive span validation, and independent entailment after this structural gate; it must not change this gate's authorization or integrity responsibilities.
- [ ] **Step 4 — Run GREEN:** rerun RED command; expect PASS.
- [ ] **Step 5 — Focused regression:** `uv run pytest tests/test_rag_retrieval.py tests/test_rag_evidence.py -v`.
- [ ] **Step 6 — Static checks:** `uv run ruff check src/aaos/rag/retrieval.py tests/test_rag_evidence.py`; `uv run mypy src/aaos/rag/retrieval.py tests/test_rag_evidence.py`.
- [ ] **Step 7 — Commit:** `git add src/aaos/rag/retrieval.py tests/test_rag_evidence.py`; `git commit -m "feat(rag): select and gate trusted evidence"`.

## Checkpoint C — Hybrid Retrieval

Require deterministic dense/lexical/RRF tests, current/historical/course-isolation tests, stale-vector fail-closed tests, evidence-budget and sufficiency review, no-reranker/no-fallback assertions, Checkpoints A–B regressions, Ruff, strict mypy, and a human review of the complete authorized-universe-before-ranking path before Task 17.

### Task 17: Implement Grounded Structured Generation Through ModelGateway

**Files**
- Modify: `src/aaos/rag/contracts.py`
- Create: `src/aaos/rag/generation.py`
- Create: `tests/test_rag_generation.py`

**Interfaces**
- Consumes: existing `ModelGateway`, `ChatMessage`/`ChatRequest`, `RagQuery`, selected evidence, structural sufficiency decision, and (after the approved semantic amendment) validated atomic requirements, extractive spans, and per-requirement entailment support.
- Produces: validated internal `GroundedModelOutput` and safe generation errors.

**Requirements**
- `GroundedAnswerRequest` contains question, detected/explicit answer language, pinned generation/revision, selected evidence, and (after semantic support is deterministically aggregated) an explicit partial-support constraint. It never accepts model-supplied citations or scope changes.
- System prompt labels evidence as untrusted data and grants no tools/actions; model returns closed JSON with status, claims, and evidence IDs.
- Retry exactly once only for schema-invalid output using a constrained repair request; do not retry transport/model/citation/policy failures.

- [ ] **Step 1 — Write failing tests:** valid first response, malformed then valid repair, malformed twice, invalid status/missing fields, Arabic/English language policy, injection strings remaining evidence data, conflict response citing both, and no factual-generation call after structural insufficiency or deterministically aggregated `UNSUPPORTED`. Representative assertion: `assert fake_gateway.generate_calls == 2` for one malformed output followed by one valid repair.
- [ ] **Step 2 — Run RED:** `uv run pytest tests/test_rag_generation.py -k "structured or prompt or language" -v`; expect missing generator.
- [ ] **Step 3 — Minimal implementation:** build trusted messages, call `await ModelGateway.generate(ChatRequest(...))`, parse strict JSON without adapter-specific coupling, and enforce one repair maximum.
- [ ] **Step 4 — Run GREEN:** rerun RED command; expect PASS with fake gateway.
- [ ] **Step 5 — Focused regression:** `uv run pytest tests/test_inference_models.py tests/test_model_gateway.py tests/test_rag_evidence.py tests/test_rag_generation.py -v`.
- [ ] **Step 6 — Static checks:** `uv run ruff check src/aaos/rag/contracts.py src/aaos/rag/generation.py tests/test_rag_generation.py`; `uv run mypy src/aaos/rag/contracts.py src/aaos/rag/generation.py`.
- [ ] **Step 7 — Commit:** `git add src/aaos/rag/contracts.py src/aaos/rag/generation.py tests/test_rag_generation.py`; `git commit -m "feat(rag): add grounded model generation"`.

### Task 18: Add Claim-Level Citation Validation and Rendering

**Files**
- Modify: `src/aaos/rag/contracts.py`
- Modify: `src/aaos/rag/generation.py`
- Modify: `tests/test_rag_generation.py`

**Interfaces**
- Consumes: `GroundedModelOutput`, exact request-local evidence map and resolved spans.
- Produces: typed `RagAnswer` and `RenderedRagResponse` with deterministic citations.

**Requirements**
- Every factual claim has one or more request-supplied evidence IDs; unseen/reused IDs, empty citations, unresolved pages/version/course, or partial validation reject the whole response.
- Trusted code renders document title plus page or page range; the model never renders citation strings. PARTIAL_ANSWER exposes supported claims and identifies unsupported requested portions without inventing content.

- [ ] **Step 1 — Write failing tests:** ANSWER/PARTIAL/INSUFFICIENT, hallucinated/cross-request IDs, uncited claim, missing provenance, exact single/range citation, conflicting citations, no partially validated display, and operational error separation. Representative assertion: `with pytest.raises(InvalidEvidenceReferenceError): validator.validate(output, request_evidence)`.
- [ ] **Step 2 — Run RED:** `uv run pytest tests/test_rag_generation.py -k "citation or answer" -v`; expect validation/render failures.
- [ ] **Step 3 — Minimal implementation:** add strict answer validator and deterministic renderer over the already materialized evidence map.
- [ ] **Step 4 — Run GREEN:** rerun RED command; expect PASS.
- [ ] **Step 5 — Focused regression:** `uv run pytest tests/test_rag_contracts.py tests/test_rag_generation.py -v`.
- [ ] **Step 6 — Static checks:** `uv run ruff check src/aaos/rag/contracts.py src/aaos/rag/generation.py tests/test_rag_generation.py`; `uv run mypy src/aaos/rag/contracts.py src/aaos/rag/generation.py`.
- [ ] **Step 7 — Commit:** `git add src/aaos/rag/contracts.py src/aaos/rag/generation.py tests/test_rag_generation.py`; `git commit -m "feat(rag): validate claims and render citations"`.

### Task 19: Expose the Guarded LocalRagService Facade

**Files**
- Modify: `src/aaos/rag/__init__.py`
- Create: `src/aaos/rag/service.py`
- Create: `tests/test_rag_service.py`

**Interfaces**
- Consumes: `HybridRetriever`, `EvidenceSelector`, `EvidenceSufficiencyGate`, the later-approved atomic requirement/span/entailment stages, and grounded generator/renderer.
- Produces: `LocalRagService.ask(RagQuery) -> RenderedRagResponse` (async).

**Requirements**
- Preserve scope/snapshot/evidence/citation gates in one mandatory path usable by Phase 5; no lower-level public bypass, chat history, tools, or fallback. After structural gating, invoke the later-approved atomic decomposition, extractive span validation, and independent entailment stages, then validate and deterministically aggregate their support before propagating `PARTIAL`/`UNSUPPORTED` without duplicating retrieval or citation logic. Require the retriever's verified embedding-release boundary before any model generation call.
- The facade is agnostic to dense-generation lifetime. It relies on `HybridRetriever` to resolve each query's `QuerySnapshot` and to own/close the matching generation-specific dense handle; it does not cache an ACTIVE generation or dense index itself. The embedding release verification remains mandatory before generation.

- [ ] **Step 1 — Write failing tests:** successful/partial/insufficient, no factual-generation gateway call after structural insufficiency or deterministically aggregated `UNSUPPORTED`, atomic decomposition/span/entailment ordering, dense/lexical/model failure propagation, exact one query snapshot, embedding release before model generation, no history/tools, and facade exports only intended types. Representative assertion: `assert (await service.ask(unsupported_query)).answer.status is RagAnswerStatus.INSUFFICIENT_EVIDENCE`.
- [ ] **Step 2 — Run RED:** `uv run pytest tests/test_rag_service.py -v`; expect missing facade.
- [ ] **Step 3 — Minimal implementation:** add narrow orchestration with no catch-all response fallback.
- [ ] **Step 4 — Run GREEN:** rerun RED command; expect PASS.
- [ ] **Step 5 — Focused regression:** `uv run pytest tests/test_rag_retrieval.py tests/test_rag_evidence.py tests/test_rag_generation.py tests/test_rag_service.py -v`.
- [ ] **Step 6 — Static checks:** `uv run ruff check src/aaos/rag/__init__.py src/aaos/rag/service.py tests/test_rag_service.py`; `uv run mypy src/aaos/rag tests/test_rag_service.py`.
- [ ] **Step 7 — Commit:** `git add src/aaos/rag/__init__.py src/aaos/rag/service.py tests/test_rag_service.py`; `git commit -m "feat(rag): expose guarded local rag service"`.

## Checkpoint D — Grounded Generation and Citations

Require all fake-ModelGateway tests, strict public status review, exact one-repair proof, claim/provenance/cross-request ID failures, multilingual/conflict/injection fixtures, no-call-on-insufficient proof, no tools/fallback review, and Checkpoints A–C verification before Task 20.

### Task 20: Compose and Own Phase4RagRuntime

**Files**
- Create: `src/aaos/rag/runtime.py`
- Create: `tests/test_rag_runtime.py`
- Create: `tests/test_rag_imports.py`

**Interfaces**
- Consumes: injected paths/config/gateway, database constructors, repository, dense factory, embedding factory, services.
- Produces: `build_phase4_runtime(...) -> Phase4RagRuntime` owning Engine, the generation-independent `DenseIndexFactory`, embedding lifecycle, and services. Query-scoped generation-specific dense handles remain owned and closed by `HybridRetriever`.

**Requirements**
- Idempotent close order: services stop -> dense indexes close -> embedding closes/releases GPU -> SQLAlchemy engine disposes. Construction failures close only owned resources.
- Import/construction without explicit command must not migrate/create normal DB/Chroma, load BGE-M3, contact LM Studio/network, or index.
- Runtime construction creates a generation-independent `DenseIndexFactory` and does not resolve or require an ACTIVE generation or open a dense handle. It injects that factory into `HybridRetriever`; each query opens the exact `snapshot.generation` handle and owns its close. The runtime must never bind one construction-time ACTIVE handle, and it must not close query-owned handles.
- Construction uses an explicit ownership ledger: every acquired resource is registered immediately and failures unwind all registered resources in reverse acquisition order. Meaningful stages are engine/session/repository, dense-factory, embedding service, lock/chunker/indexer, indexing coordinator, retriever, selector/gate/generator/validator, and facade construction; tests inject failures at each stage that can occur after an owned resource is acquired.
- Cleanup attempts every owned resource even when an earlier close fails. The original construction failure remains primary where possible; any cleanup failure is retained as a sanitized `RagOperationalError` (using the existing safe code, such as `DENSE_INDEX_UNAVAILABLE`) and never rendered with backend text, paths, SQL, model-cache details, or secrets. A successfully built runtime has deterministic idempotent close semantics and never double-closes a query-owned dense handle.

- [ ] **Step 1 — Write failing tests:** generation-independent construction with no ACTIVE generation, snapshot-selected dense handles for G1/G2/RETIRED historical scopes, no BUILDING access, runtime race/relaunch behavior, ownership/close order, double close, context manager, every meaningful construction-failure point, cleanup-failure observability, engine disposal, side-effect-free imports, no Phase3 runtime dependency, and no normal path artifacts. Representative assertions include that `open(snapshot.generation)` receives the pinned identity, each handle closes once before lexical/generation work completes, and `assert events == ["dense.close", "embedding.close", "engine.dispose"]` for runtime-owned resources.
- [ ] **Step 2 — Run RED:** `uv run pytest tests/test_rag_runtime.py tests/test_rag_imports.py -v`; expect import failures.
- [ ] **Step 3 — Minimal implementation:** compose explicit factories and track ownership with deterministic cleanup.
- [ ] **Step 4 — Run GREEN:** rerun RED command; expect PASS.
- [ ] **Step 5 — Focused regression:** `uv run pytest tests/test_document_imports.py tests/test_inference_imports.py tests/test_rag_runtime.py tests/test_rag_imports.py -v`.
- [ ] **Step 6 — Static checks:** `uv run ruff check src/aaos/rag/runtime.py tests/test_rag_runtime.py tests/test_rag_imports.py`; `uv run mypy src/aaos/rag/runtime.py tests/test_rag_runtime.py`.
- [ ] **Step 7 — Commit:** `git add src/aaos/rag/runtime.py tests/test_rag_runtime.py tests/test_rag_imports.py`; `git commit -m "feat(rag): own local rag runtime lifecycle"`.

### Task 21: Add the Exact Safe RAG CLI

**Files**
- Create: `src/aaos/rag/cli.py`
- Modify: `src/aaos/cli.py`
- Create: `tests/test_rag_cli.py`

**Interfaces**
- Consumes: lazy runtime builder, argv and injected stdin/stdout.
- Produces: exact approved index/status/ask command surface and safe exit codes.

**Requirements**
- Commands: `rag index-new [--document-version-id ID]`, `rag rebuild`, `rag status`, and mutually exclusive `rag ask --course-id ID|--global|--document-version-id ID`.
- Ask reads one nonblank question interactively/stdin; no `--question`, URL, file path, Moodle cmid, arbitrary generation, or arbitrary model argument.
- Output contains answer/citations or fixed safe operational/status fields only; never raw question/chunk/model response/path/secret.

- [ ] **Step 1 — Write failing tests:** exact dispatch, every valid command, exclusivity/missing scope, stdin question, rejected flags, no import-time runtime, safe errors/status, no question echo, no cross-command routing. Representative assertion: `assert "private question" not in captured.out + captured.err`.
- [ ] **Step 2 — Run RED:** `uv run pytest tests/test_rag_cli.py -v`; expect command-not-supported failures.
- [ ] **Step 3 — Minimal implementation:** add narrow parsers/renderers and lazy top-level dispatch consistent with existing CLI style.
- [ ] **Step 4 — Run GREEN:** rerun RED command; expect PASS.
- [ ] **Step 5 — Focused regression:** `uv run pytest tests/test_document_cli.py tests/test_doctor_cli.py tests/test_rag_cli.py -v`.
- [ ] **Step 6 — Static checks:** `uv run ruff check src/aaos/cli.py src/aaos/rag/cli.py tests/test_rag_cli.py`; `uv run mypy src/aaos/cli.py src/aaos/rag/cli.py tests/test_rag_cli.py`.
- [ ] **Step 7 — Commit:** `git add src/aaos/cli.py src/aaos/rag/cli.py tests/test_rag_cli.py`; `git commit -m "feat(rag): add local rag command surface"`.

## Checkpoint E — Runtime and CLI

Require generation-aware runtime construction/cleanup/import tests (including G1/G2/RETIRED snapshot switching and reverse rollback), exact CLI grammar/privacy/negative-surface tests, all earlier checkpoints, full Ruff/strict mypy/lock checks, and review that no normal path is touched without an explicit command and no Phase 5 capability exists before Task 22.

### Task 22: Add Evaluation Dataset, Classical IR Metrics, and Ablations

**Files**
- Create: `src/aaos/rag/evaluation/__init__.py`
- Create: `src/aaos/rag/evaluation/contracts.py`
- Create: `src/aaos/rag/evaluation/metrics.py`
- Create: `tests/test_rag_evaluation.py`

**Interfaces**
- Consumes: sanitized gold manifests and ranked retrieval outputs.
- Produces: deterministic split manifest, Recall@1/3/5/10, MRR, nDCG, subgroup/sufficiency/ablation reports.

**Requirements**
- Validate 80–120 target cases with question language, answer language, expected status, scope, supporting internal document versions/pages, and category; keep real private questions/content untracked.
- Deterministically split about 70/30 calibration/held-out by stable case ID, lock held-out before tuning, and cover English, Arabic, cross-language, lexical, semantic, multi-source, conflicting, partial, unsupported, current/history, and course isolation.
- Compare dense-only, FTS-only, hybrid RRF and a controlled ~300/~500/~700 chunk ablation; no giant search. If hybrid has no meaningful benefit, checkpoint pauses for design review.
- A benchmark used to diagnose or choose a lexical-policy correction is labeled `BENCHMARK_V1_DIAGNOSTIC` and retained only as historical evidence. Its held-out cases are not pristine after influencing the correction. Final acceptance must prepare a new fingerprinted benchmark version with human-reviewed qrels, create a new unseen held-out split, lock that split before retrieval output is inspected, and prohibit post-lock tuning.

- [ ] **Step 1 — Write failing tests:** schema/privacy, duplicate IDs, subgroup coverage, deterministic split/no leakage, hand-calculated recall/MRR/nDCG, sufficiency false acceptance/refusal, baseline labels, and fingerprinted run manifests. Representative assertion: `assert calibration_ids.isdisjoint(held_out_ids)`.
- [ ] **Step 2 — Run RED:** `uv run pytest tests/test_rag_evaluation.py -v`; expect missing evaluation modules.
- [ ] **Step 3 — Minimal implementation:** implement pure manifest/metric/aggregation functions and explicit ablation configurations.
- [ ] **Step 4 — Run GREEN:** rerun RED command; expect PASS.
- [ ] **Step 5 — Focused regression:** `uv run pytest tests/test_rag_evaluation.py tests/test_rag_retrieval.py tests/test_rag_evidence.py -v`.
- [ ] **Step 6 — Static checks:** `uv run ruff check src/aaos/rag/evaluation tests/test_rag_evaluation.py`; `uv run mypy src/aaos/rag/evaluation tests/test_rag_evaluation.py`.
- [ ] **Step 7 — Commit:** `git add src/aaos/rag/evaluation/__init__.py src/aaos/rag/evaluation/contracts.py src/aaos/rag/evaluation/metrics.py tests/test_rag_evaluation.py`; `git commit -m "feat(rag): add deterministic retrieval evaluation"`.

### Task 23: Add Explicit Local-Only RAGAS Evaluation

**Files**
- Create: `src/aaos/rag/evaluation/ragas_adapter.py`
- Create: `tests/test_rag_ragas.py`

**Interfaces**
- Consumes: Task 1 verified RAGAS APIs, explicit local evaluator/embeddings, evaluation cases/results.
- Produces: supplementary groundedness/relevance/context metrics plus recorded evaluator configuration.

**Requirements**
- No runtime import/dependency on RAGAS; no implicit OpenAI/Gemini/Anthropic credentials/provider; missing local evaluator fails explicitly.
- Record RAGAS version, evaluator model/embeddings, metric set, dataset version, run ID; cloud evaluation requires future separate authorization and sanitized data.

- [ ] **Step 1 — Write failing tests:** local evaluator required, cloud provider/env fallback rejected, unavailable local evaluator failure, no runtime import, safe manifest, and current selected metric API behavior. Representative assertion: `with pytest.raises(LocalEvaluatorUnavailableError): adapter.evaluate(run)` when the configured local evaluator is absent.
- [ ] **Step 2 — Run RED:** `uv run pytest tests/test_rag_ragas.py -v`; expect missing adapter.
- [ ] **Step 3 — Minimal implementation:** create an injected RAGAS adapter with an allowlisted local evaluator factory and zero provider auto-discovery.
- [ ] **Step 4 — Run GREEN:** rerun RED command; expect PASS with local fakes.
- [ ] **Step 5 — Focused regression:** `uv run pytest tests/test_rag_evaluation.py tests/test_rag_ragas.py tests/test_rag_imports.py -v`.
- [ ] **Step 6 — Static checks:** `uv run ruff check src/aaos/rag/evaluation/ragas_adapter.py tests/test_rag_ragas.py`; `uv run mypy src/aaos/rag/evaluation/ragas_adapter.py tests/test_rag_ragas.py`.
- [ ] **Step 7 — Commit:** `git add src/aaos/rag/evaluation/ragas_adapter.py tests/test_rag_ragas.py`; `git commit -m "feat(rag): add local only ragas evaluation"`.

### Task 24: Add Human, Safety, and Performance Evaluation Reports

**Files**
- Create: `src/aaos/rag/evaluation/reports.py`
- Create: `tests/test_rag_reports.py`

**Interfaces**
- Consumes: dataset/run manifests, retrieval/generation/safety timings and human labels.
- Produces: sanitized reproducible `EvaluationSummary`/acceptance artifact files under injected output roots.

**Requirements**
- Report human claim-evidence support, citation appropriateness, completeness, Arabic quality, partial/conflicting/unsupported behavior; injection fixtures remain data and have no tools.
- Report cold/warm embedding, dense, FTS, RRF/selection, generation/total latency, indexing throughput, RAM, GPU memory where measurable, Chroma size, corpus/config fingerprints and model identifiers; make no SLA claim before measurement.
- Never serialize real questions, chunks, answers, document titles, or private content into repository artifacts.

- [ ] **Step 1 — Write failing tests:** required human fields, injection outcomes, latency/resource units, fingerprint lineage, aggregate-only privacy scan, deterministic JSON, and injected output root. Representative assertion: `assert set(report) <= SAFE_ACCEPTANCE_FIELDS`.
- [ ] **Step 2 — Run RED:** `uv run pytest tests/test_rag_reports.py -v`; expect missing reporter.
- [ ] **Step 3 — Minimal implementation:** add typed aggregate collectors/renderers with allowlisted fields.
- [ ] **Step 4 — Run GREEN:** rerun RED command; expect PASS.
- [ ] **Step 5 — Focused regression:** `uv run pytest tests/test_rag_evaluation.py tests/test_rag_ragas.py tests/test_rag_reports.py -v`.
- [ ] **Step 6 — Static checks:** `uv run ruff check src/aaos/rag/evaluation/reports.py tests/test_rag_reports.py`; `uv run mypy src/aaos/rag/evaluation/reports.py tests/test_rag_reports.py`.
- [ ] **Step 7 — Commit:** `git add src/aaos/rag/evaluation/reports.py tests/test_rag_reports.py`; `git commit -m "feat(rag): add safe evaluation reporting"`.

## Checkpoint F — Evaluation Infrastructure

Require deterministic dataset/split/metric tests, no held-out leakage, dense/lexical/hybrid and chunk ablation harnesses, sufficiency metrics, local-only RAGAS privacy proof, sanitized human/injection/performance outputs, no private corpus committed, no cloud fallback, and human approval of the calibration protocol before Task 25.

### Task 25: Add Disposable End-to-End and Crash-Recovery Integration

**Files**
- Create: `tests/test_rag_integration.py`
- Create: `tests/test_rag_crash_recovery.py`
- Modify: `src/aaos/rag/indexing.py`
- Modify: `src/aaos/rag/retrieval.py`
- Modify: `src/aaos/rag/generation.py`
- Modify: `src/aaos/rag/service.py`
- Modify: `src/aaos/rag/runtime.py`

**Interfaces**
- Consumes: migrated disposable SQLite, disposable Chroma, deterministic pages, fake embeddings and fake ModelGateway.
- Produces: full offline proof from Phase 3 rows through indexing/retrieval/answer/citations.

**Requirements**
- Exercise index-new, no-op revision, rebuild/activation, current/history/course scope, cross-store crash injection at every boundary, startup recovery, injection evidence, no fallback, and deterministic repeated answers/citations.
- Tests inspect that normal DB/Chroma paths remain absent and Phase 3 rows remain unchanged.

- [ ] **Step 1 — Write failing tests:** one complete bilingual corpus flow plus parametrized crashes before/after SQLite, FTS, Chroma, READY, revision, and activation commits. Representative assertion: `assert answer.citations[0].page_start == gold_page` after the disposable end-to-end query.
- [ ] **Step 2 — Run RED:** `uv run pytest tests/test_rag_integration.py tests/test_rag_crash_recovery.py -v`; expect integration gaps.
- [ ] **Step 3 — Minimal implementation:** fix only missing composition/recovery behavior in existing Phase 4 modules, test-first for each observed failure; do not change frozen Phase 3.
- [ ] **Step 4 — Run GREEN:** rerun RED command; expect PASS.
- [ ] **Step 5 — Focused regression:** `uv run pytest tests -k rag -v`.
- [ ] **Step 6 — Static/full checks:** `uv run ruff format --check .`; `uv run ruff check .`; `uv run mypy src tests`; `uv lock --check`; `git diff --check`.
- [ ] **Step 7 — Commit:** inspect `git status --short`, then stage only changed Task 25 paths explicitly: `git add tests/test_rag_integration.py tests/test_rag_crash_recovery.py src/aaos/rag/indexing.py src/aaos/rag/retrieval.py src/aaos/rag/generation.py src/aaos/rag/service.py src/aaos/rag/runtime.py`; inspect `git diff --cached`; `git commit -m "test(rag): prove disposable end to end recovery"`.

### Task 26: Add Controlled Target-Hardware Acceptance and Evidence

**Files**
- Create: `tests/live/test_phase4_local_rag_acceptance.py`
- Modify: `src/aaos/rag/evaluation/reports.py`
- Modify: `tests/test_rag_reports.py`

**Interfaces**
- Consumes: explicit human opt-in, separately authorized disposable/real corpus paths, real BGE-M3, local configured `ModelGateway`, locked evaluation config/held-out set.
- Produces: safe aggregate acceptance report; no automatic tag.

**Requirements**
- Default pytest skips before any model/storage/network action. Explicit acceptance separately verifies BGE-M3 model identity/dimension, indexing, GPU/RAM lifecycle on RTX 4050 6 GB, embedding release before local generation, local LM Studio availability, answer/citation behavior, held-out IR/RAGAS/human/safety/performance results, and no normal-path use unless that exact run is human-authorized.
- If BGE-M3 cannot run/release safely, local ModelGateway unavailable, hybrid benefit absent, or held-out/security gates fail, stop for architecture/human review. Do not auto-switch model, worker process, cloud evaluator, or simpler retrieval.
- The final acceptance uses the post-correction benchmark version and its newly locked unseen held-out split. The diagnostic V1 benchmark and its previously observed held-out results remain historical and must not be presented as the final post-correction held-out evidence.

- [ ] **Step 1 — Write failing/default-skip tests:** prove no opt-in means skip with zero side effects, exact required environment/authorization fields, safe output schema, model/GPU lifecycle markers, and no freeze tag operation. Representative assertion: `assert not normal_database_path.exists() and not normal_chroma_path.exists()` after the default-skipped test collection.
- [ ] **Step 2 — Run RED:** run the isolated acceptance test with fake acceptance dependencies enabled; expect missing acceptance wiring, while ordinary `uv run pytest tests/live/test_phase4_local_rag_acceptance.py -v` remains safely skipped.
- [ ] **Step 3 — Minimal implementation:** add guarded acceptance composition and aggregate report fields; never embed private corpus text in output.
- [ ] **Step 4 — Run GREEN offline:** run guarded tests with fakes; expect PASS and default skip.
- [ ] **Step 5 — Controlled real acceptance:** only after separate human authorization, run the exact opted-in command recorded by the implementation review, then complete held-out human review and capture sanitized aggregates.
- [ ] **Step 6 — Final verification:** `uv run pytest`; `uv run ruff format --check .`; `uv run ruff check .`; `uv run mypy src tests`; `uv lock --check`; `git diff --check`; verify `data/database/aaos.db` and `data/rag/chroma/` were untouched unless explicitly authorized.
- [ ] **Step 7 — Commit:** after human review of safe acceptance evidence, `git add tests/live/test_phase4_local_rag_acceptance.py src/aaos/rag/evaluation/reports.py tests/test_rag_reports.py`; `git commit -m "test(rag): add controlled phase4 acceptance"`. Do not create `phase-04-passed`.

## Phase 4 runtime amendment — Qwen completion budget and non-overlapping GPU lifecycle

This amendment is documentation-only until human approval. It applies only to
the already-scoped generation/runtime/acceptance work in Tasks 17, 20, and 26;
it does not authorize held-out answer execution, RAGAS, Checkpoint G, or Phase 5.

### Amendment A — bounded structured completion

The calibration reproduction showed the initial grounded response consuming the
existing 1024-token cap and ending with incomplete JSON. The one repair stopped
below that cap but returned markdown-fenced invalid JSON, so increasing the
budget alone is insufficient. The approved fixed default is **2048 completion
tokens** for both the initial and repair `ChatRequest`. This remains below the
configured 8192-token context and the generator's existing 4096-token maximum.
Before each request, the prompt-token allowance plus 2048 completion tokens must
fit the configured context; fail closed rather than silently truncating evidence
or requirements, enlarging context, switching models, or using a cloud fallback.

Both calls must set LM Studio's locally confirmed `response_format` JSON Schema
mode. The deterministic infrastructure-owned schema represents exactly the
existing strict `RagAnswer` contract (`status`, `answer_text`, `language`, and
`claims`; each claim has exactly `claim_id`, `text`, and `evidence_ids`). The
schema is not influenced by question/evidence data. JSON Schema constrains
syntax but does not replace the strict application parser, request-local
evidence-ID checks, provenance/citation validator, or trusted renderer;
additional fields, malformed claims, unknown IDs, markdown/prose, and incomplete
output remain rejected. The maximum remains one repair attempt; no extraction,
prose stripping, third attempt, schema relaxation, or automatic increase to
3072/4096 is allowed. Calibration must prove strict completion, schema-mode
success, and trusted validation at 2048 before held-out execution. A response
that reaches 2048 and truncates, or fails schema/strict validation after repair,
is a human review gate.

**Files:** modify `src/aaos/rag/generation.py`; extend
`tests/test_rag_generation.py` with a realistic response exceeding the old
512-token bound, strict parsing, malformed-output rejection, and exactly-one
repair assertions. Extend the local LM Studio adapter tests for the exact JSON
Schema request contract and keep the existing strict parser authoritative.

### Earlier calibration refinement — superseded by requirement-level amendment

The earlier refinement recorded a local Qwen semantic coverage classifier with
its 256-token bound, strict `semantic_coverage` JSON Schema, and at most one
repair. Its request configuration uses `temperature=0` when supported by the
existing `ModelGateway` path. That whole-question contract is retained here as
history only; the requirement-level amendment below replaces it for any future
implementation. No benchmark IDs, qrels, or source-specific rules may be
introduced.

The factual grounded-answer request timeout remains the approved bounded **60
seconds**, and the factual 2048-token completion contract remains unchanged.
The remaining classifier timeout/schema/bound statements in this historical
paragraph describe the superseded whole-question design; the current
requirement-level bounds, two-step repair policy, and calibration gates are
defined below. Any failed requirement stops for human review rather than
triggering automatic retuning.

### Amendment B — deterministic local answer-model residency

Create `src/aaos/inference/lm_studio_lifecycle.py` with a narrow typed
controller and no public model-management command. Its production adapter may
use only the locally confirmed LM Studio CLI operations:

```text
lms ps --json
lms unload <validated-configured-model>
lms load <validated-configured-model> --gpu max --context-length <validated-context> -y
```

The executable is resolved through the approved local installation; model and
context values come only from validated `AppSettings`. Do not accept caller
commands, arbitrary model identifiers, URLs, or arguments; never use `lms
unload --all`; never start/reconfigure the server, download a model, or assume
JIT/on-demand loading. Command output is discarded and converted to the
existing safe `MODEL_UNAVAILABLE` error boundary. A successful command is not
enough: `ps --json` must verify the configured model's postcondition.

`Phase4RagRuntime` owns the controller and composes it through private
wrappers, without changing `ModelGateway` or exposing lifecycle controls to the
LLM:

1. Before any BGE-M3 acquisition, ensure the configured answer model is absent
   and verify the postcondition.
2. Run the existing BGE embed/retrieve path and its mandatory release/verification
   boundary.
3. Immediately before each `ModelGateway.generate()` call, ensure/load the
   configured answer model and verify it is available.
4. If unload, BGE release verification, load, or postcondition verification
   fails, stop with typed `MODEL_UNAVAILABLE`; do not generate, retrieve, switch
   devices/models, or use a cloud/network fallback.
5. Unload the answer model before a later embedding request and during
   deterministic runtime cleanup. Close is idempotent, bounded, and does not
   rely on process termination.

The runtime remains the sole lifecycle owner. The gateway remains the sole
answer-generation boundary; retrieval, evidence, citation, and rendering logic
are not duplicated or broadened. The existing short transaction and embedding
release ordering remain unchanged.

**Files:** create `src/aaos/inference/lm_studio_lifecycle.py` and
`tests/test_lm_studio_lifecycle.py`; modify `src/aaos/rag/runtime.py` only for
explicit ownership/composition and the private lifecycle wrappers. Update
`tests/test_rag_runtime.py` for construction/cleanup ordering. Extend
`tests/live/test_phase4_local_rag_acceptance.py` only with safe aggregate
markers for model absence before BGE, BGE release before generation, and no
simultaneous residency. Normal tests inject fakes and never invoke `lms`, CUDA,
LM Studio, or model downloads.

### Amendment verification and acceptance gate

Run the generation and lifecycle tests with fake gateways/controllers first.
Then, in a separately human-authorized calibration-only run, record the fixed
model/config identity, completion usage/finish status, and aggregate GPU memory
observations. Acceptance must show Qwen not GPU-resident before BGE work, BGE
released before Qwen load/generation, and no simultaneous BGE+Qwen GPU
residency. A missing/unmeasurable postcondition is a blocker even if the run
happens to fit within 6 GB. No held-out answer cases, RAGAS, or Checkpoint G may
run until this amendment is approved and the calibration gate passes.

## Architecture amendment — requirement-level semantic sufficiency (historical)

This is the earlier human-approved design/plan amendment retained for audit.
The atomic/extractive/entailment amendment below supersedes its two-step
decomposition/coverage implementation guidance. The existing Tasks 14–19
implementation remains the baseline until the new amendment receives a
separate implementation authorization. Neither section authorizes held-out
answer execution, RAGAS, Checkpoint G, tagging, or Phase 5 by itself.
All schemas, bounds, and ordering in this historical section are retained for
audit only and are not normative for the amended implementation.

### Why the deterministic gate is not sufficient

The completed 56-case semantic coverage spike was mechanically healthy but did
not prove a safe whole-question boundary: it produced false support and
recognized none of the expected partial cases. Dense similarity, lexical
coverage, rank/fusion, question-part, and source-diversity signals overlap
across `SUPPORTED`, `PARTIAL`, and `UNSUPPORTED`. Do not retune those
thresholds. Keep the Task 16 gate as the deterministic structural
authorization/integrity gate and add requirement-level semantic judgments only
for evidence that passes it.

### Proposed interfaces and ordering

The later implementation must compose this exact order without a second
retrieval:

```text
RagQuery -> QuerySnapshot -> HybridRetriever -> EvidenceSelector
  -> EvidenceSufficiencyGate (structural)
  -> RequirementDecomposer (question/context only)
  -> RequirementCoverageClassifier (fixed requirements + selected evidence)
  -> strict typed requirement validation
  -> deterministic status aggregation
  -> UNSUPPORTED: deterministic INSUFFICIENT_EVIDENCE
  -> PARTIAL: GroundedAnswerGenerator with partial-support constraint
  -> SUPPORTED: GroundedAnswerGenerator
  -> strict RagAnswer parser -> citation validator -> renderer
```

Structural failure skips semantic classification and factual generation. A
structurally eligible query invokes one question-only typed
`RequirementDecomposer`, then one typed `RequirementCoverageClassifier`,
before any factual answer call. The decomposer sees no selected evidence;
deterministic code assigns canonical IDs and freezes its requirement set. The
coverage classifier consumes only that immutable set, answer-language policy,
pinned generation/revision metadata, and selected bounded evidence. It cannot
add, remove, merge, or rewrite the immutable requirement universe, broaden the
`QuerySnapshot`, retrieve more data, choose citations, call tools, or render an
answer.

### Requirement decomposition contract

The approved approach is two bounded structured calls within one loaded Qwen
lifecycle. The question-only `RequirementDecomposer` receives the original
question and language/context needed to understand user intent, but no
retrieved evidence. It must preserve every material requested fact, retain
explicit multi-part requests, avoid artificial over-splitting, and add nothing
not requested. After strict parsing, deterministic code assigns canonical
contiguous IDs (`r1` through `rN`) and freezes the requirement set; model-
supplied IDs are not accepted.

Its closed schema contains no coverage fields:

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
          "requirement_text": {"type": "string", "minLength": 1, "maxLength": 240}
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

The bound is `minItems=1`, `maxItems=6`. The decomposition output contains no
evidence IDs, support status, citations, confidence, rationale, chain-of-
thought, or answer field. The canonical requirement set is immutable for the
remainder of the request.

### Requirement coverage contract

The coverage call receives the immutable deterministic requirement IDs/text plus
selected request-local evidence. It must return exactly one judgment for every
fixed requirement and may not add, remove, merge, or rewrite the requirement
universe. Response array order is not semantically authoritative; deterministic
code validates the exact ID set and normalizes valid permutations to canonical
order:

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
          "requirement_id": {"type": "string", "pattern": "^r[1-6]$"},
          "support": {"type": "string", "enum": ["SUPPORTED", "UNSUPPORTED"]},
          "evidence_ids": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "minLength": 1, "maxLength": 128}
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

The coverage output has no requirement text, confidence, rationale, citation
string, source metadata, tool request, chain-of-thought, or answer field.
Evidence IDs remain opaque request-local control identifiers; the schema does
not invent their format.

### Validation, aggregation, and partial propagation

Before aggregation, deterministic code requires the returned requirement count
to equal the immutable requirement count and the returned ID set to equal the
immutable ID set exactly. Missing, duplicate, unknown, or rewritten
requirements fail closed; valid permutations are normalized to canonical ID
order. For every `SUPPORTED` requirement, every evidence ID
must exist in the current request, belong to the selected pinned
`QuerySnapshot`, and resolve through authoritative SQLite provenance. Missing,
duplicate, cross-request, out-of-snapshot, or unresolved evidence IDs fail
closed. For every `UNSUPPORTED` requirement, `evidence_ids` must be empty. Any
schema deviation is a typed semantic-coverage failure, not a downgrade.

After validation, code derives the internal status: zero supported requirements
with a valid non-empty list is `UNSUPPORTED`; every requirement supported is
`SUPPORTED`; a non-empty mixture is `PARTIAL`. `UNSUPPORTED` maps to
deterministic `INSUFFICIENT_EVIDENCE` and skips factual generation. Public
statuses remain exactly `ANSWER`, `PARTIAL_ANSWER`, and
`INSUFFICIENT_EVIDENCE`; internal coverage statuses are never substituted for
them.

For `PARTIAL`, the grounded generator receives the original question, the
supported immutable requirement subset, only validated evidence IDs/text for
those requirements, and the immutable descriptions of unsupported
requirements. It must answer supported portions only, explicitly mark
unsupported requested portions, never infer missing facts, and cite every
factual claim through the existing validator. Unsupported requirement text is a
request-derived constraint, never factual source material.

### Local model, bounds, and failure policy

Use only `qwen/qwen3.5-4b` through the existing loopback `ModelGateway`; no
cloud or network fallback, tools, agents, memory, or alternative model is
allowed. The proposed completion bounds are **512 tokens for decomposition**
and **512 tokens for coverage**: decomposition is above the old 256-token
whole-question bound, while coverage remains small and bounded. Both remain
below the existing 4096 maximum. Before each call, its prompt tokens plus the
corresponding bound must fit the configured context; fail closed rather than
truncate evidence, change the model, or enlarge context. Use the confirmed
local JSON Schema mode, strict application parsing, and `temperature=0` for
both steps. Permit at most one same-schema repair per step. Transport,
model-availability, lifecycle, and policy errors are not retried. Malformed or
invalid output after a step's repair fails closed and cannot fall through to
factual generation. The approved factual timeout remains 60 seconds and the
2048-token factual completion contract is unchanged.

### Prompt and lifecycle invariants

For decomposition, only the question/request context is interpreted as user
intent; retrieved evidence is absent. For coverage, retrieved document text
and fixed requirement text remain untrusted data. Prompts separate policy from
data and expose no tools. Evidence cannot add, remove, or rewrite
requirements, alter schema, scope, secrets, model/runtime configuration, or
network behavior. The existing SQLite-backed evidence/provenance and citation
validator remain authoritative.

Per request, the runtime ensures Qwen is not resident, performs BGE-M3
embedding/retrieval, releases and verifies BGE, then loads/verifies Qwen once
for decomposition, coverage, and any subsequent factual generation. It does
not unload/reload Qwen between semantic steps or before factual generation and
unloads it during deterministic cleanup. BGE and Qwen must never be
simultaneously GPU-resident; any unverifiable postcondition is a typed
fail-closed model error.

### Later TDD and acceptance work

When explicitly authorized, add focused tests for decomposition receiving no
evidence, canonical ID assignment and immutable requirement sets, exact
coverage completeness, strict evidence-ID validation, deterministic
aggregation, all three internal statuses, partial propagation, unsupported
no-factual-call behavior, malformed output and one-repair bounds per step,
context-budget failure, prompt-injection data, no tools/network fallback,
request-local evidence isolation, BGE-before-Qwen lifecycle, Qwen reuse, and
success/failure cleanup. Extend service, generation, runtime, import, and
guarded acceptance tests with fakes; normal tests never contact LM Studio,
load CUDA models, or use protected paths.

Calibration may use only the already-consumed 56 cases with expected classes
`SUPPORTED=40`, `PARTIAL=4`, and `UNSUPPORTED=12`. First evaluate decomposition
quality, especially all partial and unsupported cases, and report
`MATERIAL_REQUIREMENT_OMITTED`, `SPURIOUS_REQUIREMENT_ADDED`, `OVER_SPLIT`, and
`UNDER_SPLIT` without changing benchmark labels. Then report requirement
counts, coverage judgments, deterministic aggregate statuses, false
support/refusal, partial-to-supported and partial-to-unsupported rates, and
English/Arabic breakdowns. The internal `PARTIAL` aggregate maps to the
existing evaluation label `PARTIALLY_SUPPORTED` and never replaces public
`RagAnswer` statuses. Require zero false support across all unsupported cases
and materially functional partial behavior before held-out authorization. Do
not execute or inspect the pristine 24 answer-heldout cases until
implementation, calibration, lifecycle, citation, and all other pre-heldout
gates pass. `HELDOUT_ANSWER_CASES_EXECUTED=0` and
`ANSWER_HELDOUT_RESULTS_VISIBLE=NO` remain mandatory during this amendment.

## Architecture amendment — atomic requirements, extractive evidence, and independent entailment

**Status:** human-approved in principle for planning; production
implementation is not authorized by this documentation amendment. This
amendment addresses the audited semantic failures without changing retrieval,
candidate selection, benchmark labels, qrels, split membership, or the public
`RagAnswer` status contract. The existing Tasks 14–19 code remains the baseline
until a separately approved implementation task is scheduled.

### Confirmed diagnosis and scope

The audited cases were `v2-p005`, `v2-p020`, `v2-p021`, `v2-p022`, `v2-p035`,
`v2-p041`, and `v2-p047`. Four decompositions were under-split (zero omitted
requirements); two coverage decisions were false entailments, two were
topical-similarity false entailments, two treated partial evidence as full
support, and one exposed a selector/retrieval boundary limitation. Persistent
dangerous false support was observed in `v2-p022` and `v2-p047`. The model
remains `qwen/qwen3.5-4b`.

Do not retune retrieval or selector thresholds. `v2-p035` remains a documented
recall/selection limitation: if authoritative evidence was not selected, the
semantic path must refuse rather than broaden retrieval. Case identifiers are
rationale only and must not become production exceptions.

### Required future query path

Implement the following order with no second retrieval:

```text
RagQuery -> QuerySnapshot -> hybrid retrieval -> EvidenceSelector
  -> structural sufficiency gate
  -> release/verify BGE-M3 -> load qwen/qwen3.5-4b
  -> AtomicRequirementDecomposer (question/context only)
  -> deterministic canonical IDs + immutable requirement set
  -> EvidenceSpanExtractor (selected evidence only)
  -> deterministic span/request/snapshot/provenance validation
  -> IndependentEntailmentVerifier (one requirement + its validated spans)
  -> deterministic per-requirement support and aggregate status
  -> UNSUPPORTED: INSUFFICIENT_EVIDENCE
  -> PARTIAL/SUPPORTED: constrained GroundedAnswerGenerator
  -> strict RagAnswer -> citation/provenance validator -> renderer -> cleanup
```

### Contracts to implement

1. Atomic decomposition: a closed schema containing only one through six
   nonblank `requirement_text` values (maximum 240 characters each). Split
   independent named aspects, `and`-joined facts, comparison dimensions, and
   named categories when independently answerable; do not add, omit, combine,
   or artificially fragment facts. The model proposes text, while trusted code
   assigns contiguous `r1`...`rN` IDs, validates the set, and freezes it. No
   evidence is visible to this step and code does not claim to prove semantic
   atomicity.

2. Extractive spans: a closed object containing exactly one entry for every
   immutable requirement ID; each entry has zero or more bounded spans, each
   with only `evidence_id` and bounded `quote`. Use a small fixed cap (the
   design proposal is at most four spans and 400 quote characters per span).
   Each quote must be copied verbatim from the same selected evidence record
   identified by its `evidence_id`; paraphrasing, summarizing, rewriting,
   model-side normalization, and composition across evidence records are
   prohibited. If no exact suitable text exists for a requirement, return its
   required entry with `spans=[]`. The single repair repeats this same-record,
   verbatim-only contract. No support verdict, rationale, confidence, citation
   text, or chain of thought is allowed.

3. Span validation: deterministically require known requirement IDs, selected
   request-local evidence IDs, pinned `QuerySnapshot` membership, authoritative
   SQLite provenance, and exact normalized substring occurrence of every quote
   in its selected evidence text. Missing/duplicate/unknown IDs, cross-request
   evidence, unresolved provenance, or paraphrased/non-occurring quotes fail
   closed. Invalid spans never contribute support.

4. Independent entailment: for each immutable requirement, pass only that
   requirement and its validated spans to a narrow local model call. The closed
   result is exactly `ENTAILED` or `NOT_ENTAILED`; no IDs, confidence,
   rationale, partial label, citation, or chain of thought. A requirement with
   no validated span is deterministically `NOT_ENTAILED` without a model call.
   Prefer one bounded call per requirement; do not silently batch unrelated
   requirements.

5. Trusted aggregation: code maps `ENTAILED` to `SUPPORTED` and all other
   outcomes to `UNSUPPORTED`. All supported requirements aggregate to
   `SUPPORTED`, all unsupported to `UNSUPPORTED`, and a mixture to `PARTIAL`.
   `UNSUPPORTED` maps to public `INSUFFICIENT_EVIDENCE` and skips factual
   generation. Internal statuses remain distinct from public statuses.

6. Partial propagation: the factual generator receives the original question,
   supported immutable requirements, validated entailing spans/chunks, and
   unsupported requirement descriptions. It answers supported portions only,
   identifies unavailable portions, never infers unsupported facts, and uses
   the existing trusted citation validator.

### Model bounds, repair, and failure policy

Use `qwen/qwen3.5-4b` with `temperature=0` and local JSON Schema mode. Proposed
separate completion bounds for calibration are 512 tokens for decomposition,
384 for span extraction, and 64 for entailment. Each step permits at most one
same-schema repair; there is no third call, prose stripping, schema
relaxation, cloud/network fallback, or automatic budget increase. Prompt plus
the step bound must fit the configured context; overflow, transport/model or
lifecycle failure, malformed output, failed repair, invalid requirement set,
invalid span/ID/quote/provenance, or malformed entailment fails closed before
factual generation.

### Security and lifecycle invariants

Evidence and extracted spans remain untrusted data and cannot alter schema,
requirements, scope, tools, model/runtime configuration, network behavior, or
secrets. No tools, agents, memory, or second retrieval are introduced. The
accepted lifecycle remains Qwen absent during BGE work, BGE released and
verified, Qwen loaded once for decomposition/span/entailment/factual generation,
then deterministically cleaned up; BGE and Qwen never overlap.

### Future implementation surface and TDD gate

When separately authorized, evolve
`src/aaos/rag/semantic_coverage.py` (or a reviewed replacement) into the
decomposer, span extractor/validator, and entailment verifier; extend
`src/aaos/rag/contracts.py`; update `src/aaos/rag/service.py` for aggregation
and partial/unsupported propagation; and make only the typed partial-support
input change needed in `src/aaos/rag/generation.py`. No retrieval or selector
files are in scope. Extend
`tests/test_rag_semantic_coverage.py` (or a reviewed replacement) plus service,
generation, runtime/import, and guarded calibration tests.

The RED/GREEN gate must cover evidence-free decomposition, canonical immutable
IDs, exact span schema and quote/provenance validation, independent entailment,
all aggregate statuses, partial propagation, unsupported no-generation,
malformed output and one-repair limits per step, context overflow,
prompt-injection data, no tools/network fallback, request-local isolation,
non-overlapping BGE/Qwen lifecycle, and success/failure cleanup. Normal tests
use fakes only.

Calibration may use only the already-consumed 56 cases (`SUPPORTED=40`,
`PARTIAL=4`, `UNSUPPORTED=12`). Report decomposition timeouts/truncations/
malformed/repairs and under/over-split/omitted/spurious counts; span validation,
ID, quote, and provenance failures; entailment malformed/repair and
topical-only/partial-as-full acceptance; deterministic aggregate confusion;
and English/Arabic breakdowns. Require `FALSE_SUPPORT_COUNT=0` and materially
functional partial behavior before any held-out answer execution. Do not change
labels, qrels, split, retrieval, or selector based on these results.

## Architecture amendment — per-requirement extractive span extraction

**Status:** human-approved for documentation and later implementation planning
only. No production implementation is authorized by this amendment. This
section supersedes the batched span-output sub-contract above while preserving
the existing retrieval, selector, provenance, entailment, generation,
citation, public-status, and held-out gates.

### Decision and scope

The consumed-calibration audit established two distinct failure classes in the
batched extractor: `v2-p005` and `v2-p002` reached the 384-token ceiling with
truncated JSON on both attempts, while P004 proved that two-requirement output
can succeed and P001 showed intermittent same-record binding errors. The
approved correction is one sequential span call per immutable requirement, not
a larger batch, hybrid batching, retrieval change, or validator relaxation.

The existing caller-facing boundary remains:

```python
EvidenceSpanExtractor.extract(
    requirements: RequirementSet,
    *,
    evidence: tuple[RetrievedEvidence, ...],
) -> tuple[RequirementEvidenceSpans, ...]
```

The implementation owns the loop, calls requirements in canonical `r1`...
`rN` order, and returns the same typed tuple. `RequirementEvidenceSpans` and
service aggregation already represent one requirement at a time, so
`src/aaos/rag/contracts.py` and `src/aaos/rag/service.py` are review-only and
are not expected to change.

Each loop iteration performs exactly one initial extraction attempt and, only
when that attempt violates the approved structural or validation contract, at
most one same-schema repair. Thus the maximum is two model calls per
requirement; there is no second repair or retry loop.

### Exact future implementation surface

**Modify:**

- `src/aaos/inference/models.py`: extend `StructuredOutputSchema` and its
  validation set with `evidence_spans_single_requirement`.
- `src/aaos/inference/lm_studio.py`: register the strict schema named
  `aaos_evidence_spans_single_requirement_v1`, whose root object has only a
  `spans` array; each item has only `evidence_id` and `quote`, with the existing
  max-four-span and max-400-character bounds.
- `src/aaos/rag/semantic_coverage.py`: add a private single-requirement message
  builder/parser and make `EvidenceSpanExtractor.extract()` call it once per
  requirement, attaching the already-owned requirement ID in deterministic
  code. Keep existing quote normalization, provenance checks, repair bound,
  and typed error behavior.

**Test:**

- `tests/test_rag_semantic_coverage.py`: per-requirement call count, isolated
  inputs, closed output schema, deterministic assembly, empty spans, one repair,
  malformed/truncated output, quote/evidence/provenance failures, and no
  automatic rebinding.
- `tests/test_lm_studio_gateway.py`: exact schema serialization and strict
  `additionalProperties: false` behavior for the new schema key.
- `tests/test_rag_integration.py`: three sequential span calls followed by
  unchanged entailment ordering and selected-evidence identity.
- `tests/test_rag_service.py`: aggregate support, unsupported refusal, and
  partial propagation remain unchanged.
- `tests/test_rag_runtime.py` and `tests/test_rag_imports.py`: Qwen reuse,
  BGE/Qwen non-overlap, cleanup on success/failure, and side-effect-free
  construction.
- `tests/live/test_phase4_local_rag_acceptance.py`: retain only the existing
  guarded calibration seam; no held-out or live execution is added by this
  amendment.

### Interfaces and invariants

The private implementation is planned as:

```python
async def _extract_one(
    requirement: Requirement,
    *,
    evidence: tuple[RetrievedEvidence, ...],
) -> RequirementEvidenceSpans: ...
def _parse_single_requirement_spans(
    response: ChatResponse,
    requirement: Requirement,
    evidence: tuple[RetrievedEvidence, ...],
    model_identifier: str,
) -> RequirementEvidenceSpans: ...
```

Each request includes one immutable requirement and the unchanged selected
request-local evidence tuple. The model output is exactly
`{"spans":[{"evidence_id":"...","quote":"..."}]}`; it does not contain
`requirement_id`. Code attaches the requirement ID and treats every returned
span as an untrusted candidate. Candidates are validated independently for
selected/pinned membership, authoritative provenance, and same-record normalized
quote occurrence. Invalid candidates are discarded and never reach entailment
or factual generation; valid candidates remain eligible. The same evidence ID
may support multiple distinct valid quotes. Exact duplicate
`(evidence_id, normalized_quote)` pairs are deterministically deduplicated, but
different quotes are never merged. `spans=[]` is valid no-evidence and skips
entailment for that requirement. If candidates are present but none validate,
the existing one-repair/fail-closed path is used and is not downgraded to
`UNSUPPORTED`. Malformed/truncated output, transport failure, or a failed repair
still fails closed.

### TDD implementation sequence (future authorization)

- [ ] **Step 1 — Write RED tests:** add a fake gateway test that supplies three
  requirement responses and asserts three requests, each containing exactly
  one requirement and the same selected evidence; assert the response schema
  name is `evidence_spans_single_requirement` and no request payload asks the
  model to return a requirement ID.
- [ ] **Step 2 — Run the RED command:**
  `uv run pytest tests/test_rag_semantic_coverage.py -q`.
  Expected failure: the current extractor sends one batched request with the
  `evidence_spans` schema, so the call-count and isolated-input assertions
  fail.
- [ ] **Step 3 — Add the inference schema boundary:** update only
  `src/aaos/inference/models.py` and `src/aaos/inference/lm_studio.py` with the
  new typed schema key and strict schema registration; add the gateway request
  serialization regression and rerun its focused test.
- [ ] **Step 4 — Implement the minimal extractor loop:** replace the batched
  `_build_span_messages`/`_parse_spans` path with `_extract_one` and
  `_parse_single_requirement_spans`; iterate sequentially, attach canonical
  IDs, preserve the existing one-repair-per-requirement behavior, and leave
  `RequirementEvidenceSpans` unchanged.
- [ ] **Step 5 — Verify deterministic failure semantics:** add tests for
  empty spans (no entailment call), independent candidate validation, repeated
  evidence IDs with distinct valid quotes, exact-pair deduplication, invalid
  candidates being discarded, malformed/truncated output (at most one
  same-schema repair), failed repair, unknown evidence IDs, cross-record or
  paraphrased quotes, provenance failures, normalization-only matches, and
  failed extraction not becoming `UNSUPPORTED`; rerun the focused semantic
  suite.
- [ ] **Step 6 — Verify integration and lifecycle:** add/extend fake integration
  tests for three sequential calls, canonical assembly, unchanged entailment
  ordering, Qwen reuse without unload/reload between requirements, no BGE/Qwen
  overlap, cleanup on every path, unchanged partial propagation, and no second
  retrieval. No retrieval or selector files may change.
- [ ] **Step 7 — Focused verification:** run
  `uv run pytest tests/test_rag_semantic_coverage.py tests/test_lm_studio_gateway.py tests/test_rag_integration.py tests/test_rag_service.py tests/test_rag_runtime.py tests/test_rag_imports.py -q`,
  then `uv run ruff format --check .`, `uv run ruff check .`,
  `uv run mypy src tests`, `uv lock --check`, and `git diff --check`.
- [ ] **Step 8 — Review:** inspect the diff for requirement ownership,
  request-local evidence isolation, prompt-injection boundaries, schema
  closure, retry count, lifecycle ordering, and absence of retrieval or
  Phase 3 changes. Commit documentation separately as
  `docs(rag): document per-requirement span extraction`; a later authorized
  implementation is committed separately as
  `feat(rag): extract spans per requirement`.

### Calibration and held-out gate after implementation

Only after a separate implementation authorization, run consumed calibration
cases `v2-p005`, `v2-p002`, `v2-p025`, `v2-p004`, `v2-p001`, and `v2-p014` first.
For every immutable requirement record span-call count, finish reason,
completion tokens, repair use, span count, and validation result. Require no
384-token truncation, unresolved quote/provenance failure, false support,
topical-only entailment, or partial-as-full acceptance before running the full
consumed 56 cases. Report total calls plus min/median/max calls and latency.
The 24 held-out answer cases remain unexecuted and invisible; do not run
RAGAS, Checkpoint G, tagging, or Phase 5 from this amendment.

### Scope exclusions

Do not change retrieval, selector, benchmark/qrels/split, model, temperature,
entailment semantics, public `RagAnswer` statuses, citation rendering,
normalization, Phase 3, held-out execution, or protected data paths. Evidence
remains untrusted data and cannot alter requirements, schemas, scope, tools,
network behavior, model configuration, or secrets.

## Architecture amendment — deterministic request-local evidence segments

**Status:** design/specification and implementation-plan amendment only. The
human-approved segment-ID architecture is not implemented by this document.
Implementation requires a separate authorization and a separate code commit.
The corresponding specification is Section 44 of
`docs/superpowers/specs/2026-08-28-phase-4-local-rag-design.md`.

### Decision and unchanged boundaries

Replace model-generated quote strings with deterministic request-local
`EvidenceSegment` identifiers after the existing retrieval and evidence
selection stages. Atomic decomposition remains unchanged. One sequential call
per immutable requirement supplies only segment IDs; trusted code resolves
those IDs to exact selected text and authoritative source spans, then hands the
existing `EvidenceSpan`/`RequirementEvidenceSpans` values to entailment and the
unchanged answer/citation path.

No retrieval, RRF, selector, snapshot, Chroma, FTS, database schema, persisted
segment identity, citation contract, public answer status, Phase 3 behavior,
benchmark/qrels/split, model identity, temperature, or held-out policy changes.
No tools, agents, memory, cloud fallback, or new dependency is permitted.

### Exact deterministic interface

Create `src/aaos/rag/evidence_segments.py` as a small private, side-effect-free
module. It owns a versioned `SEGMENTATION_VERSION`,
`SEGMENT_MAX_CHARACTERS = 400`, and frozen values equivalent to:

```python
@dataclass(frozen=True, slots=True)
class EvidenceSegmentSourceSpan:
    document_page_id: int
    page_number: int
    source_char_start: int
    source_char_end: int
    segment_char_start: int
    segment_char_end: int


@dataclass(frozen=True, slots=True)
class EvidenceSegment:
    segment_id: str
    parent_evidence_id: str
    exact_text: str
    start_offset: int
    end_offset: int
    source_spans: tuple[EvidenceSegmentSourceSpan, ...]
```

The public surface of this new module remains private to the semantic path:

```python
def segment_selected_evidence(
    evidence: tuple[RetrievedEvidence, ...],
) -> tuple[EvidenceSegment, ...]: ...


def resolve_segment_selection(
    selected_ids: tuple[str, ...],
    *,
    segments: tuple[EvidenceSegment, ...],
    evidence: tuple[RetrievedEvidence, ...],
    pinned_generation_id: int,
    pinned_content_revision: int,
) -> tuple[EvidenceSpan, ...]: ...
```

`segment_selected_evidence()` validates selected parent IDs/generation and
revision, assigns `s001`, `s002`, ... in selected-evidence order and ascending
parent offsets, and partitions exact text without rewriting or overlap. It
chooses the rightmost eligible boundary at or below 400 characters, preferring
paragraph, line, and sentence-like punctuation, then whitespace, then an exact
hard split. The delimiter
whitespace is included in the prior slice so concatenating all slices exactly
reconstructs every parent. Source spans are derived by interval intersection;
cross-page segments retain multiple ordered non-empty intersections. A missing
or invalid authoritative intersection raises the existing typed operational
failure rather than producing untrusted provenance.

`resolve_segment_selection()` accepts only IDs in the current map, verifies
request-local parent/snapshot membership and source spans, removes invalid IDs,
deduplicates valid IDs by first occurrence, and returns code-owned
`EvidenceSpan(parent_evidence_id, quote=segment.exact_text)` values in stable
order. It never accepts parent IDs, quotes, offsets, pages, or provenance from
the model. The initial maximum is four selected segments per requirement, the
same bound currently enforced for trusted evidence spans. Empty `segment_ids`
is valid no-evidence; a non-empty response with no valid IDs gets one
same-schema repair, and a second zero-valid response fails closed.

The resolver receives the already-selected evidence tuple as a trusted
request-local argument; it verifies each segment's parent ID, generation,
content revision, exact parent substring, and authoritative source-span
intersection against that tuple. For a parent span
`[chunk_char_start, chunk_char_end) -> [source_char_start, source_char_end)`,
an intersection `[a, b)` maps to source offsets
`source_char_start + (a - chunk_char_start)` through
`source_char_start + (b - chunk_char_start)`, and to segment offsets relative to
the segment start. Thus crossing page spans retain ordered exact intersections
without accepting model-provided offsets.

### Exact production changes when authorized

Modify only these production boundaries:

- `src/aaos/inference/models.py`: add the typed
  `evidence_segments_single_requirement` schema key to
  `StructuredOutputSchema` and `ChatRequest` validation.
- `src/aaos/inference/lm_studio.py`: register the strict local JSON Schema
  `aaos_evidence_segments_single_requirement_v1` with root
  `{"segment_ids": [...]}`, `additionalProperties: false`, string IDs matching
  `^s[0-9]{3,}$`, and `maxItems: 4`. No quote, evidence, requirement, offset,
  status, or citation fields are allowed.
- `src/aaos/rag/semantic_coverage.py`: replace only the quote-producing span
  call with sequential `_select_segments_one()` calls, one requirement at a
  time in canonical order. Build an isolated prompt containing that requirement
  and the same selected segment universe, parse the closed ID response, invoke
  at most one identical-schema repair, resolve IDs through
  `evidence_segments.py`, and return the unchanged
  `RequirementEvidenceSpans` tuple. Keep entailment and aggregate status code
  unchanged.

`src/aaos/rag/contracts.py`, `src/aaos/rag/service.py`,
`src/aaos/rag/generation.py`, citation code, retrieval code, and selector code
are review-only. Change them only if a compile-time contract requires a
minimal adapter; no semantic redesign is authorized.

### TDD sequence

- [ ] **Step 1 — Write RED tests:** create
  `tests/test_rag_evidence_segments.py` and extend semantic/gateway integration
  tests. Cover stable IDs, exact contiguous reconstruction, unique request-local
  IDs, natural-boundary/whitespace/hard-split behavior, Arabic/English, empty
  text, source-span and cross-page mapping, and no model/network involvement.
  Add a fake-gateway test with three requirements proving each request contains
  exactly one requirement and the identical selected segment tuple.
- [ ] **Step 2 — Run RED:**
  `uv run pytest tests/test_rag_evidence_segments.py tests/test_rag_semantic_coverage.py -q`.
  Expected failure is the missing segment module/schema and the current quote
  output contract; no production behavior is changed to make the RED pass.
- [ ] **Step 3 — Add the schema boundary:** update only
  `src/aaos/inference/models.py` and `src/aaos/inference/lm_studio.py`; add
  exact serialization tests in `tests/test_lm_studio_gateway.py` for strict
  `additionalProperties: false`, deterministic schema name, ID pattern, and
  max-four bound. Verify prompt/evidence text cannot mutate schema metadata.
- [ ] **Step 4 — Implement deterministic segmentation:** add the pure
  partitioner and source-span intersection in `evidence_segments.py`. Tests
  must prove exact text is code-owned, all parent text is covered once, IDs are
  stable/unique, no overlap exists, and malformed parent/provenance input fails
  closed.
- [ ] **Step 5 — Implement selection and resolution:** change only the
  semantic extractor path to make one sequential request per requirement,
  attach requirement IDs in trusted code, resolve valid IDs, preserve first-seen
  order, and leave `RequirementEvidenceSpans` unchanged. Add tests for valid,
  repeated, unknown, mixed valid/invalid, and zero-valid selections; one repair;
  failed repair; explicit empty selection; and no automatic rebinding.
- [ ] **Step 6 — Verify semantic handoff:** test that empty selection skips
  entailment, invalid IDs never reach entailment, a wrong-but-valid segment still
  requires independent entailment, only entailing trusted segments reach factual
  generation, partial/unsupported aggregate semantics remain unchanged, and
  no model quote text exists in the request or response contract.
- [ ] **Step 7 — Verify lifecycle and integration:** extend
  `tests/test_rag_integration.py`, `tests/test_rag_runtime.py`,
  `tests/test_rag_service.py`, and `tests/test_rag_imports.py` to prove Qwen is
  reused across sequential selection calls, BGE/Qwen never overlap, cleanup is
  deterministic on success/failure, no second retrieval occurs, and imports
  remain side-effect-free. Retain the guarded calibration seam without adding
  held-out execution.
- [ ] **Step 8 — Focused verification:** run
  `uv run pytest tests/test_rag_evidence_segments.py tests/test_rag_semantic_coverage.py tests/test_lm_studio_gateway.py tests/test_rag_integration.py tests/test_rag_service.py tests/test_rag_runtime.py tests/test_rag_imports.py -q`,
  `uv run ruff format --check .`, `uv run ruff check .`,
  `uv run mypy src tests`, `uv lock --check`, and `git diff --check`.
- [ ] **Step 9 — Focused review:** inspect requirement isolation, exact-text
  ownership, source-span intersection, prompt-injection boundaries, schema
  closure, four-ID bound, repair count, entailment handoff, lifecycle ordering,
  and absence of retrieval/Phase 3 changes. Commit implementation separately
  as `feat(rag): select deterministic evidence segments`; keep any documentation
  commits separate.

### Calibration gate after implementation

Only after implementation authorization, run consumed cases
`v2-p002`, `v2-p005`, `v2-p025`, `v2-p004`, `v2-p001`, and `v2-p014`; run p002
up to three times under the frozen configuration. Record per-requirement calls,
invalid-ID count, zero-valid selections, repairs, fail-closed outcomes,
entailment result, and latency. Quote-copy, quote-binding, and quote-not-found
failures must be zero by construction. Require
`P002_UNRESOLVED_ATTEMPT_COUNT=0`, no false support, no topical-only entailment,
no partial-as-full acceptance, no provenance failure, and no lifecycle failure
before the full consumed 56-case calibration. A truncation, unresolved
selection, failed repair, or context/lifecycle error stops for human review;
there is no automatic budget increase or fallback.

If the targeted gate passes, run the existing full 56-case calibration and
report the four PARTIAL cases and all semantic safety counters. Only after
that, and under the already-approved governance, may representative consumed
answers and the final 24 held-out answers be considered. This amendment itself
must not run retrieval, generation, held-out answers, RAGAS, Checkpoint G,
tagging, or Phase 5.

### Rollback and stop conditions

Rollback is a documentation/implementation commit revert only; no database
migration or persisted segment cleanup is needed because segment IDs are
request-local. Stop immediately if a segment cannot reconstruct exact parent
text, source intersections are ambiguous, an unknown ID is trusted, an invalid
selection reaches entailment, the schema accepts extra fields, a third model
call occurs, BGE/Qwen overlap appears, or any retrieval/selector/Phase 3 path
changes. Stop for human review if p002 remains unresolved, any calibration case
truncates or fails structured validation, or a production contract outside this
amendment must change.

## Checkpoint G — Controlled Acceptance

Require fresh full deterministic verification, migration upgrade/downgrade, disposable SQLite/Chroma E2E, crash recovery, prompt injection/no-fallback tests, real BGE-M3 lifecycle on the target RTX 4050 6 GB, real local ModelGateway generation, locked held-out classical IR/RAGAS/multilingual/human review, performance/resource report, sanitized artifact review, security review, and explicit human acceptance. Tagging remains a separate future authorization.

## Evaluation and Acceptance Sequence

1. Build sanitized deterministic fixtures and run all unit/persistence/adapter/integration tests with fake embeddings/gateway.
2. Create the private 80–120-case gold manifest from authorized Phase 3 content; keep content untracked, commit only schema/tooling/safe aggregates.
3. Freeze deterministic 70/30 calibration/held-out split by case ID.
4. On calibration only, compare chunk sizes near 300/500/700, candidate depths, RRF constant, evidence budgets and sufficiency thresholds; record config fingerprints.
5. Compare dense-only, FTS5-only and hybrid RRF. If hybrid lacks meaningful useful benefit, stop for design review.
6. Lock config/policy versions, rebuild a fresh generation, and run the new unseen held-out Recall@1/3/5/10, MRR, nDCG, subgroup and sufficiency metrics. Do not reuse a diagnostic benchmark's held-out split after it informed a retrieval-policy correction.
7. Run explicit local-only RAGAS supplementary metrics; unavailable local evaluator is a failure, never a cloud fallback.
8. Perform held-out human claim/evidence/citation/completeness/Arabic/partial/conflict/unsupported review.
9. Run injection and no-tools/no-secret/no-cross-scope safety evaluation.
10. Measure cold/warm latency, throughput, RAM, GPU memory and storage; verify embedding release before generation.
11. Produce an aggregate acceptance summary containing dataset/corpus/config/model/generation/revision fingerprints and no private content.
12. Stop for human Phase 4 acceptance. A later explicit command may create `phase-04-passed`; this plan never does so automatically.

## Exact Final Verification Commands

```powershell
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv lock --check
git diff --check
```

Additionally require the Phase 4 focused migration, disposable SQLite, disposable Chroma, chunking, index-new, rebuild, crash recovery, scope/isolation, citation, injection, no-fallback, IR, RAGAS, multilingual, GPU lifecycle, local generation, held-out human review, and acceptance-report gates named in Tasks 1–26 and Checkpoints A–G.

## Specification Coverage Matrix

| # | Approved requirement | Task(s) |
| ---: | --- | --- |
| 1 | Phase 3 read-only dependency | 4, 11, 20, 25 |
| 2 | Document-only corpus | 4, 14, 25 |
| 3 | Stateless Phase 4 | 2, 19, 21 |
| 4 | Explicit Course/Global/Historical scopes | 2, 14, 21 |
| 5 | Current-version default | 4, 14 |
| 6 | Historical isolation | 4, 14, 25 |
| 7 | Deterministic structure-aware chunking | 5 |
| 8 | Exact source spans | 3, 5, 18 |
| 9 | Deterministic chunk keys | 3, 5 |
| 10 | FULL/PARTIAL/NONE | 2, 5, 11 |
| 11 | NOT_SEARCHABLE | 2, 3, 11 |
| 12 | RagIndexGeneration | 2–4 |
| 13 | content_revision | 3, 4, 12, 14 |
| 14 | Deterministic fingerprint | 2, 13, 22 |
| 15 | PREPARING/READY states | 2, 3, 11–13 |
| 16 | READY-only retrieval | 6, 11, 14 |
| 17 | SQLite authority | 3, 4, 14–15 |
| 18 | FTS5/BM25 | 3, 6, 14 |
| 19 | Lexical normalizer versioning | 2, 6 |
| 20 | Chroma rebuildability | 7–8, 13 |
| 21 | Generation isolation | 3, 8, 13–15 |
| 22 | DenseIndex abstraction | 7–8 |
| 23 | BGE-M3 EmbeddingService | 1, 9 |
| 24 | Verified GPU lifecycle | 9, 20, 26 |
| 25 | No mandatory simultaneous embedding+LLM residency | 9, 20, 26 |
| 26 | Hybrid dense + lexical | 14–15, 22 |
| 27 | RRF | 15, 22 |
| 28 | No reranker v1 | 15, 19, 25 |
| 29 | Evidence selection | 16 |
| 30 | Token/chunk budget | 2, 16 |
| 31 | Deterministic sufficiency | 16, 22 |
| 32 | No unsupported fallback | 16, 19, 25 |
| 33 | Conflicting evidence | 16–18, 22 |
| 34 | Multilingual retrieval | 5–6, 9, 22 |
| 35 | Answer-language policy | 17, 22 |
| 36 | ModelGateway reuse | 17, 20, 26 |
| 37 | Typed RagAnswer | 2, 18 |
| 38 | ANSWER | 2, 18 |
| 39 | PARTIAL_ANSWER | 2, 18 |
| 40 | INSUFFICIENT_EVIDENCE | 2, 16, 18 |
| 41 | Bounded retry | 17 |
| 42 | Query-local evidence IDs | 16, 18 |
| 43 | Claim-level citations | 18 |
| 44 | Exact citation provenance | 5, 18 |
| 45 | Prompt injection handling | 17, 24–26 |
| 46 | No tools | 17, 19, 24 |
| 47 | LocalRagService facade | 19 |
| 48 | Phase 5 cannot bypass safeguards | 19–20 |
| 49 | Single index writer | 10, 12–13 |
| 50 | Generation/revision pinning | 4, 14 |
| 51 | index-new lease | 10, 12 |
| 52 | Blue/green rebuild | 13 |
| 53 | Failed rebuild preserves ACTIVE | 13, 25 |
| 54 | No retired-generation purge | 13 |
| 55 | Cross-store recovery | 11–13, 25 |
| 56 | Activation integrity gate | 13, 25 |
| 57 | Side-effect-free imports | 1, 9, 20–21 |
| 58 | Disposable storage | 3, 8, 25–26 |
| 59 | No normal-path live writes without authorization | 20–21, 25–26 |
| 60 | Safe errors | 2, 8–21 |
| 61 | Safe logging | 17–21, 24–26 |
| 62 | index-new CLI | 21 |
| 63 | rebuild CLI | 21 |
| 64 | status CLI | 21 |
| 65 | ask CLI | 21 |
| 66 | Interactive question input | 21 |
| 67 | No URL/path/cmid | 21 |
| 68 | Deterministic evaluation | 22–24 |
| 69 | Recall@1/3/5/10 | 22 |
| 70 | MRR | 22 |
| 71 | nDCG | 22 |
| 72 | Dense-only baseline | 22 |
| 73 | Lexical-only baseline | 22 |
| 74 | Hybrid baseline | 22 |
| 75 | Multilingual subgroup metrics | 22, 26 |
| 76 | Calibration/held-out split | 22, 26 |
| 77 | Sufficiency metrics | 22 |
| 78 | RAGAS | 1, 23, 26 |
| 79 | Explicit local evaluator | 23, 26 |
| 80 | No cloud fallback | 23, 26 |
| 81 | Human claim/evidence review | 24, 26 |
| 82 | Injection evaluation | 17, 24–26 |
| 83 | Latency/resources | 24, 26 |
| 84 | Corpus/config fingerprinting | 2, 22, 24, 26 |
| 85 | Sanitized acceptance artifacts | 22–24, 26 |
| 86 | Explicit human freeze gate | 26 |

## Plan Self-Review Checklist

- [ ] Re-read the complete approved design and this plan together.
- [ ] Verify all 86 matrix rows map to concrete RED/GREEN tasks.
- [ ] Verify every interface consumed is produced by an earlier task.
- [ ] Verify every coding task has a focused RED command, expected failure, minimal GREEN, regression/static verification, and exact commit.
- [ ] Search for unresolved placeholder language and undefined symbols/paths.
- [ ] Verify Phase 3 schema/semantics/runtime remain untouched except read-only foreign-key relationships.
- [ ] Verify no Phase 5 agents/history/tools/actions enter any task.
- [ ] Verify normal tests never download/load BGE-M3, contact LM Studio/network, or require a GPU.
- [ ] Verify RAGAS cannot discover/use cloud providers automatically.
- [ ] Verify every database/Chroma/evaluation test injects disposable paths.
- [ ] Verify no task creates `phase-04-passed` or stages `reflex`.
- [ ] Stop for architecture review if Task 1 dependency compatibility, BGE-M3 lifecycle, hybrid-benefit, or controlled acceptance gates contradict the approved design.

## Implementation Handoff Boundary

This document is the coding plan only. Do not execute Task 1 until a human has reviewed and explicitly approved this plan. After approval, execute tasks in order and stop at Checkpoints A–G for the specified reviews.
