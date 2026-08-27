# Phase 3 File Downloader and Document Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Safely ingest approved Moodle PDF resources into immutable content-addressed storage with deterministic versioning, page extraction, provenance, and fail-closed authenticated download controls.

**Architecture:** A synchronous `Phase3IngestionService` coordinates one application-owned ingestion lock, an existing dedicated Edge session, a Phase-3-only Moodle resource authorizer/fetcher, PyMuPDF validation/extraction, content-addressed filesystem storage, and per-document SQLite transactions. Phase 2 remains metadata-only: no normal sync triggers ingestion, and the existing collection policy continues to reject downloads and `pluginfile` requests.

**Tech Stack:** Python 3.11, frozen dataclasses and `StrEnum`, SQLAlchemy 2.x, Alembic 1.19.x, SQLite, Playwright 1.62.0 with installed Microsoft Edge `msedge`, PyMuPDF 1.28.2, pytest 9.x, Ruff, mypy strict mode, and uv lockfile management.

**Spec:** `docs/superpowers/specs/2026-08-27-phase-3-file-downloader-document-ingestion-design.md`

## Global Constraints

- Phase 1 canonical identity, normalization, and synchronization contracts remain frozen.
- Phase 2 remains metadata-only. Normal Moodle synchronization performs no download and gains no `pluginfile` permission.
- Phase 3 starts only from an existing active normalized Moodle `RemoteFile` selected by database identity; no CLI or public interface accepts a URL.
- The current Phase 2 source identity encoding `resource-cm:<positive-decimal-cmid>` is the only accepted Moodle Resource identity encoding.
- Authenticated fetches remain inside the existing dedicated persistent Edge/Playwright `BrowserContext`; cookies and storage state are never exported or reconstructed in another HTTP client.
- Human users alone perform SSO/password/MFA entry. AAOS does not inspect credentials, cookies, browser storage, screenshots, traces, or network bodies beyond the one authorized PDF response.
- Phase 3 v1 is synchronous, sequential, single-writer, and PDF-only.
- `MAX_PDF_BYTES = 52_428_800` is a hard accepted/stored-file limit. Valid `Content-Length` above the limit is rejected early; actual received bytes are always measured.
- `MAX_TRANSIENT_RETRIES = 2`, meaning one initial request plus at most two retries, and only `NETWORK_TIMEOUT` and `NETWORK_FAILURE` are retryable.
- `MAX_AUTHORIZED_REDIRECTS = 5` and `FETCH_TIMEOUT_MS = 120_000` are fixed implementation-owned bounds with no CLI override.
- Every redirect is followed manually with Playwright `max_redirects=0` and independently revalidated.
- Original filenames are display metadata only and never determine storage paths.
- PDF bytes live under injected content-addressed filesystem storage; SQLite stores safe relative storage keys and never PDF BLOBs or absolute paths.
- SHA-256 determines immutable content identity and `DocumentVersion` identity; filename or extraction changes alone do not create versions.
- OCR, PDF passwords, translation, summarization, LLM calls, chunking, embeddings, RAG, PixelRAG, visual indexing, and generic reprocessing are excluded.
- No background scheduler, concurrent worker, delete, purge, history-removal, or garbage-collection command is introduced.
- Tests and acceptance use a fresh migrated disposable SQLite database and fresh disposable content-store root.
- Automated tests and Phase 3 acceptance never create or modify `data/database/aaos.db` or `data/documents/`.
- Real normal-database/content-store ingestion remains separately human-authorized after Phase 3 acceptance.
- No Moodle write, browser/API fallback, generic browser surface, generic authenticated POST/GET surface, or arbitrary downloader is introduced.
- Safe output contains fixed statuses, internal database IDs, numeric metadata, and batch counts only. It excludes cookies, auth headers, sesskeys, storage state, complete sensitive `pluginfile` URLs, response bodies, page text, unsafe filenames, and raw exception strings.
- Leave the pre-existing untracked `reflex` path untouched and never stage it.

## Proposed Final File Map

### Production files

| Path | Responsibility |
| --- | --- |
| `src/aaos/documents/__init__.py` | Export the narrow Phase 3 public contracts only. |
| `src/aaos/documents/contracts.py` | Closed enums, immutable value objects, protocol inputs, per-document results, and batch results. |
| `src/aaos/documents/config.py` | Fixed v1 policy and validated database/store/lock paths derived from an injected absolute AAOS data root. |
| `src/aaos/documents/lock.py` | Atomic Phase 3 single-writer lease with fail-closed ownership semantics. |
| `src/aaos/documents/storage.py` | Filename sanitization, staging, SHA-256 paths, atomic installation, dedup verification, and compensation. |
| `src/aaos/documents/pdf.py` | PyMuPDF validation, encrypted-PDF rejection, page extraction, and extraction-status classification. |
| `src/aaos/documents/repository.py` | Eligible `RemoteFile` selection and per-document SQLAlchemy transactions for state, versions, pages, and blob metadata. |
| `src/aaos/documents/service.py` | Sequential ingestion orchestration, retries, compensation, mode behavior, and batch accounting. |
| `src/aaos/documents/cli.py` | Exact `documents ingest-new` and `documents refresh` parsing, safe rendering, and production composition. |
| `src/aaos/moodle/file_download.py` | Phase-3-only resource authorization, authenticated browser session, and manual redirect fetcher. |
| `src/aaos/moodle/receiver.py` | Add a backward-compatible typed view of the already-validated non-secret Moodle runtime values including the AAOS data root. |
| `src/aaos/persistence/document_models.py` | `Document`, `ContentBlob`, `DocumentVersion`, and `DocumentPage` SQLAlchemy mappings sharing the existing `Base`. |
| `src/aaos/cli.py` | Lazy dispatch to the two exact document commands. |
| `migrations/env.py` | Register Phase 3 mappings in Alembic metadata. |
| `migrations/versions/0003_phase3_document_ingestion.py` | Forward/reversible Phase 3 schema migration. |
| `pyproject.toml` | Add exact runtime dependency `pymupdf==1.28.2`. |
| `uv.lock` | Lock PyMuPDF and its platform artifacts. |

### Test files

| Path | Responsibility |
| --- | --- |
| `tests/pdf_fixtures.py` | Generate deterministic temporary text, mixed, no-text, malformed, and encrypted PDF fixtures. |
| `tests/test_document_contracts.py` | Enums, immutable result contracts, fixed policy, and dependency smoke checks. |
| `tests/test_document_config.py` | Disposable path derivation, absolute-root checks, and production-default isolation. |
| `tests/test_document_migration.py` | `0003` upgrade/downgrade, tables, constraints, foreign keys, indexes, and UTC fields. |
| `tests/test_document_persistence.py` | ORM constraints, selection, readiness/failure state, version allocation, deduplication, and provenance queries. |
| `tests/test_document_storage.py` | Filename/path attacks, staging, atomic install, dedup, reparse/symlink rejection, and compensation. |
| `tests/test_document_pdf.py` | PDF/content/encryption validation, extraction, Unicode, geometry, empty pages, and statuses. |
| `tests/test_document_lock.py` | Exclusive acquisition, ownership-safe release, and no pre-fetch mutation on contention. |
| `tests/test_moodle_file_download.py` | Resource authorization, redirect policy, size checks, retry-neutral fetch outcomes, and secret-safe errors. |
| `tests/test_moodle_file_download_integration.py` | Real local HTTPS Playwright `BrowserContext.request` redirect/body behavior with test-only TLS relaxation. |
| `tests/test_moodle_receiver_config.py` | Preserve exact Process/User conflict handling while exposing the validated data root to Phase 3 composition. |
| `tests/test_document_service.py` | Layered orchestration, retries, modes, batch isolation, dedup/version behavior, and compensation. |
| `tests/test_document_cli.py` | Exact command grammar, selector validation, safe output, exit codes, and no implicit Phase 2 path. |
| `tests/test_document_integration.py` | Disposable migrated SQLite plus disposable store end-to-end ingestion and refresh. |
| `tests/test_document_imports.py` | Import-side-effect and normal DB/store absence checks. |
| `tests/live/test_phase3_document_ingestion_smoke.py` | Default-skipped explicit live-acceptance guard. |
| `tests/test_academic_migration.py` | Update head-schema expectations while preserving Phase 1 downgrade assertions. |
| `tests/test_academic_persistence.py` | Preserve Phase 1 table assertions after shared metadata gains Phase 3 tables. |
| `tests/test_doctor_cli.py` | Preserve existing command routing and prove exact document command dispatch. |
| `tests/test_moodle_browser_models.py` | Prove frozen Phase 2 policy still blocks `pluginfile` and downloads. |

---

### Task 1: Add PyMuPDF and freeze Phase 3 contracts/configuration

**Files:**

- Create: `src/aaos/documents/__init__.py`
- Create: `src/aaos/documents/contracts.py`
- Create: `src/aaos/documents/config.py`
- Create: `tests/test_document_contracts.py`
- Create: `tests/test_document_config.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**

- Consumes: `Path`, aware UTC `datetime`, and the repository's Python 3.11 runtime.
- Produces:

```python
class IngestionStatus(StrEnum):
    NEVER_ATTEMPTED = "NEVER_ATTEMPTED"
    READY = "READY"
    FAILED = "FAILED"

class ExtractionStatus(StrEnum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    NO_TEXT = "NO_TEXT"

class TextSource(StrEnum):
    EMBEDDED_PDF = "EMBEDDED_PDF"

class IngestionErrorCode(StrEnum):
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    NETWORK_FAILURE = "NETWORK_FAILURE"
    DOWNLOAD_TOO_LARGE = "DOWNLOAD_TOO_LARGE"
    UNAUTHORIZED_REDIRECT = "UNAUTHORIZED_REDIRECT"
    UNAUTHORIZED_ROUTE = "UNAUTHORIZED_ROUTE"
    INVALID_PDF = "INVALID_PDF"
    PDF_ENCRYPTED = "PDF_ENCRYPTED"
    PDF_PARSE_FAILED = "PDF_PARSE_FAILED"
    STORAGE_FAILED = "STORAGE_FAILED"
    PERSISTENCE_FAILED = "PERSISTENCE_FAILED"

class IngestionMode(StrEnum):
    INGEST_NEW = "INGEST_NEW"
    REFRESH = "REFRESH"

class FileType(StrEnum):
    PDF_ONLY = "PDF_ONLY"

class DocumentResultCode(StrEnum):
    INGESTED = "INGESTED"
    UNCHANGED = "UNCHANGED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"

@dataclass(frozen=True, slots=True)
class RemoteFileSelection:
    remote_file_id: int
    course_id: int
    source_system: str
    source_id: str
    title: str

@dataclass(frozen=True, slots=True)
class ExtractedPage:
    page_number: int
    text: str
    text_source: TextSource
    width_points: float
    height_points: float

@dataclass(frozen=True, slots=True)
class ParsedPdf:
    page_count: int
    extraction_status: ExtractionStatus
    pages: tuple[ExtractedPage, ...]

@dataclass(frozen=True, slots=True)
class FetchedPdf:
    body: bytes = field(repr=False)
    source_filename: str
    advisory_media_type: str | None

@dataclass(frozen=True, slots=True)
class BlobInstall:
    sha256: str
    size_bytes: int
    storage_key: str
    created: bool

@dataclass(frozen=True, slots=True)
class DocumentIngestionResult:
    remote_file_id: int
    code: DocumentResultCode
    version_number: int | None = None
    size_bytes: int | None = None
    page_count: int | None = None
    extraction_status: ExtractionStatus | None = None
    deduplicated: bool = False
    error_code: IngestionErrorCode | None = None

@dataclass(frozen=True, slots=True)
class BatchIngestionResult:
    remote_files_selected: int
    ingested: int
    unchanged: int
    failed: int
    skipped: int
    new_versions: int
    deduplicated_blobs: int
    documents: tuple[DocumentIngestionResult, ...]

    @property
    def exit_code(self) -> int: ...

class DocumentIngestionFailure(RuntimeError):
    def __init__(self, code: IngestionErrorCode) -> None: ...

MAX_PDF_BYTES = 52_428_800
MAX_TRANSIENT_RETRIES = 2
MAX_AUTHORIZED_REDIRECTS = 5
FETCH_TIMEOUT_MS = 120_000
FILE_TYPE = FileType.PDF_ONLY

@dataclass(frozen=True, slots=True)
class DocumentRuntimePaths:
    database_path: Path
    content_store_root: Path
    ingestion_lock_path: Path

def resolve_document_runtime_paths(data_root: str | Path) -> DocumentRuntimePaths: ...
```

- `FetchedPdf.body` and any URL-bearing transport object use `repr=False`.
- `BatchIngestionResult.exit_code` returns `1` when `failed > 0`, otherwise `0`.
- `resolve_document_runtime_paths()` requires an absolute, normalized data root and returns exactly `database/aaos.db`, `documents`, and `temporary/phase3-ingestion.lock` beneath it without creating paths.

- [ ] **Step 0: Complete the PyMuPDF license preflight before adding the dependency.**

Inspect `LICENSE`, `COPYING`, `NOTICE`, `pyproject.toml` license metadata, `README.md`, and governance documents for the intended AAOS distribution/deployment model. Record that PyMuPDF 1.28.2 is offered under GNU AGPL v3 or an Artifex commercial license. Proceed to `uv add` only when repository governance clearly establishes a compatible use/distribution model; otherwise stop Task 1 and request a human licensing decision without offering a legal conclusion. At plan-writing time the repository has no `LICENSE`/`COPYING`/`NOTICE`, no package-license metadata, and no documented distribution model, so the expected initial result is a licensing-decision stop rather than silent dependency installation.

- [ ] **Step 1: Write the RED dependency and contract tests.**

```python
def test_pymupdf_runtime_version_matches_lock() -> None:
    import pymupdf

    assert pymupdf.VersionBind == "1.28.2"

def test_phase3_policy_values_are_fixed() -> None:
    assert MAX_PDF_BYTES == 50 * 1024 * 1024
    assert MAX_TRANSIENT_RETRIES == 2
    assert MAX_AUTHORIZED_REDIRECTS == 5
    assert FETCH_TIMEOUT_MS == 120_000
    assert FILE_TYPE is FileType.PDF_ONLY

def test_batch_exit_code_is_nonzero_only_when_any_document_failed() -> None:
    assert BatchIngestionResult(1, 1, 0, 0, 0, 1, 0, ()).exit_code == 0
    assert BatchIngestionResult(1, 0, 0, 1, 0, 0, 0, ()).exit_code == 1
```

- [ ] **Step 2: Run RED and prove the missing dependency/contracts.**

Run: `uv run pytest tests/test_document_contracts.py tests/test_document_config.py -v`

Expected: FAIL because `pymupdf` and `aaos.documents` do not exist.

- [ ] **Step 3: Add the exact runtime dependency through uv.**

Run: `uv add "pymupdf==1.28.2"`

Expected: `pyproject.toml` lists `pymupdf==1.28.2`, `uv.lock` contains PyMuPDF 1.28.2, and no OCR package is added.

- [ ] **Step 4: Implement the frozen contracts and validated path resolver.**

```python
def resolve_document_runtime_paths(data_root: str | Path) -> DocumentRuntimePaths:
    root = Path(data_root).expanduser()
    if not root.is_absolute() or root.resolve(strict=False) != root:
        raise ValueError("document data root must be an absolute normalized path")
    return DocumentRuntimePaths(
        database_path=root / "database" / "aaos.db",
        content_store_root=root / "documents",
        ingestion_lock_path=root / "temporary" / "phase3-ingestion.lock",
    )
```

Implement `DocumentIngestionFailure` with the fixed message `document ingestion failed`; retain only its enum `code` as structured diagnostic data.

- [ ] **Step 5: Run focused GREEN verification.**

Run: `uv run pytest tests/test_document_contracts.py tests/test_document_config.py -v`

Expected: PASS with immutable enums/dataclasses, exact constants, and no filesystem creation.

- [ ] **Step 6: Run dependency and import regressions.**

Run: `uv lock --check`

Run: `uv run python -c "import pymupdf; import aaos.documents"`

Expected: both commands PASS with no `data/database/aaos.db` or `data/documents/` creation.

- [ ] **Step 7: Commit only Task 1 files.**

```text
git add pyproject.toml uv.lock src/aaos/documents/__init__.py src/aaos/documents/contracts.py src/aaos/documents/config.py tests/test_document_contracts.py tests/test_document_config.py
git commit -m "feat(documents): add ingestion contracts and PDF dependency"
```

### Task 2: Add the Phase 3 persistence schema and migration

**Files:**

- Create: `src/aaos/persistence/document_models.py`
- Create: `migrations/versions/0003_phase3_document_ingestion.py`
- Create: `tests/test_document_migration.py`
- Create: `tests/test_document_persistence.py`
- Modify: `migrations/env.py`
- Modify: `tests/test_academic_migration.py`
- Modify: `tests/test_academic_persistence.py`

**Interfaces:**

- Consumes: `Base` and `UTCDateTime` from `aaos.persistence.academic_models`, plus the enums from Task 1.
- Produces:

```python
class DocumentORM(Base):
    __tablename__ = "documents"
    id: Mapped[int]
    remote_file_id: Mapped[int]
    ingestion_status: Mapped[str]
    last_attempted_at: Mapped[datetime | None]
    last_error_code: Mapped[str | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

class ContentBlobORM(Base):
    __tablename__ = "content_blobs"
    id: Mapped[int]
    sha256: Mapped[str]
    size_bytes: Mapped[int]
    media_type: Mapped[str]
    storage_key: Mapped[str]
    created_at: Mapped[datetime]

class DocumentVersionORM(Base):
    __tablename__ = "document_versions"
    id: Mapped[int]
    document_id: Mapped[int]
    version_number: Mapped[int]
    content_blob_id: Mapped[int]
    source_filename: Mapped[str]
    ingested_at: Mapped[datetime]
    extraction_status: Mapped[str]
    page_count: Mapped[int]

class DocumentPageORM(Base):
    __tablename__ = "document_pages"
    id: Mapped[int]
    document_version_id: Mapped[int]
    page_number: Mapped[int]
    text: Mapped[str]
    text_source: Mapped[str]
    width_points: Mapped[float]
    height_points: Mapped[float]
    created_at: Mapped[datetime]
```

- `Document.remote_file_id` is a required unique FK to `remote_files.id`.
- `ContentBlob.sha256` and `ContentBlob.storage_key` are independently unique.
- `DocumentVersion` has `UNIQUE(document_id, version_number)` and `UNIQUE(document_id, content_blob_id)`.
- `DocumentPage` has `UNIQUE(document_version_id, page_number)`.
- Named indexes cover `Document.remote_file_id`, `ContentBlob.sha256`, `DocumentVersion(document_id, version_number)`, and `DocumentPage(document_version_id, page_number)`; unique indexes may satisfy the corresponding unique lookups where Alembic/SQLite exposes them deterministically.
- SQLite check constraints enforce positive IDs/counts/dimensions, SHA format length, size `1..52_428_800`, `application/pdf`, the closed status/error values, and relative `sha256/<2 hex>/<64 hex>.pdf` storage-key shape where SQLite can enforce it. Python storage validation remains authoritative for full path semantics.
- All foreign keys use default restrictive deletion; no cascade/purge behavior is added.

- [ ] **Step 1: Write RED migration and constraint tests.**

```python
def test_phase3_upgrade_adds_exact_document_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "phase3.db"
    config = make_config(database_path)
    command.upgrade(config, "head")
    assert {"documents", "content_blobs", "document_versions", "document_pages"}.issubset(
        set(inspect(create_database_engine(database_path)).get_table_names())
    )

def test_phase3_downgrade_preserves_phase1_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "phase3.db"
    config = make_config(database_path)
    command.upgrade(config, "head")
    command.downgrade(config, "0002_phase1_academic_domain")
    assert set(inspect(create_database_engine(database_path)).get_table_names()) == {
        "alembic_version", "courses", "assignments", "announcements", "remote_files"
    }
```

Add focused tests that violate each unique/FK/check constraint and verify `IntegrityError`.

- [ ] **Step 2: Run RED and prove schema absence.**

Run: `uv run pytest tests/test_document_migration.py tests/test_document_persistence.py tests/test_academic_migration.py tests/test_academic_persistence.py -v`

Expected: FAIL because revision `0003_phase3_document_ingestion` and document mappings are absent.

- [ ] **Step 3: Implement focused mappings on the shared metadata.**

```python
class DocumentORM(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("remote_file_id", name="uq_documents_remote_file_id"),
        CheckConstraint(
            "ingestion_status IN ('NEVER_ATTEMPTED','READY','FAILED')",
            name="ck_documents_ingestion_status",
        ),
        Index("ix_documents_status", "ingestion_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    remote_file_id: Mapped[int] = mapped_column(ForeignKey("remote_files.id"), nullable=False)
    ingestion_status: Mapped[str] = mapped_column(String(32), nullable=False)
```

Define the remaining columns and tables exactly as the interface block specifies. Use `Text` for page text and `Float` for dimensions.

- [ ] **Step 4: Implement migration `0003` and metadata registration.**

Set `down_revision = "0002_phase1_academic_domain"`. Import `aaos.persistence.document_models` in `migrations/env.py` solely to register its tables with the shared `Base.metadata`; do not move or alter Phase 1 mappings.

- [ ] **Step 5: Update prior head-schema assertions without weakening Phase 1 checks.**

Change exact-head expectations to include all eight domain/document tables. Keep explicit assertions that downgrade to `0002` leaves the four Phase 1 tables and that Phase 1 constraints still work.

- [ ] **Step 6: Run GREEN migration and persistence verification.**

Run: `uv run pytest tests/test_document_migration.py tests/test_document_persistence.py tests/test_academic_migration.py tests/test_academic_persistence.py tests/test_database.py -v`

Expected: PASS for upgrade, downgrade, constraints, FK enforcement, UTC round-trip, and shared metadata.

- [ ] **Step 7: Run static checks for the schema slice.**

Run: `uv run ruff check src/aaos/persistence migrations tests/test_document_migration.py tests/test_document_persistence.py tests/test_academic_migration.py tests/test_academic_persistence.py`

Run: `uv run mypy src/aaos/persistence tests/test_document_persistence.py`

Expected: PASS.

- [ ] **Step 8: Commit only Task 2 files.**

```text
git add src/aaos/persistence/document_models.py migrations/env.py migrations/versions/0003_phase3_document_ingestion.py tests/test_document_migration.py tests/test_document_persistence.py tests/test_academic_migration.py tests/test_academic_persistence.py
git commit -m "feat(documents): add document persistence schema"
```

### Task 3: Add the document repository and transaction contracts

**Files:**

- Create: `src/aaos/documents/repository.py`
- Modify: `tests/test_document_persistence.py`

**Interfaces:**

- Consumes: Task 1 contracts, Task 2 ORM mappings, `RemoteFileORM`, and `Callable[[], Session]` matching `AcademicSyncService`.
- Produces:

```python
@dataclass(frozen=True, slots=True)
class SelectionPlan:
    selected: tuple[RemoteFileSelection, ...]
    fetch: tuple[RemoteFileSelection, ...]
    skipped: tuple[RemoteFileSelection, ...]

@dataclass(frozen=True, slots=True)
class PersistedVersion:
    document_id: int
    version_number: int
    content_blob_id: int
    content_blob_created: bool

class DocumentRepository:
    def __init__(self, session_factory: Callable[[], Session]) -> None: ...

    def plan_selection(
        self,
        *,
        mode: IngestionMode,
        source_system: str,
        remote_file_id: int | None,
    ) -> SelectionPlan: ...

    def existing_version_for_sha256(
        self, *, remote_file_id: int, sha256: str
    ) -> int | None: ...

    def persist_success(
        self,
        *,
        selection: RemoteFileSelection,
        blob: BlobInstall,
        source_filename: str,
        parsed_pdf: ParsedPdf,
        attempted_at: datetime,
    ) -> PersistedVersion: ...

    def record_failure(
        self,
        *,
        selection: RemoteFileSelection,
        code: IngestionErrorCode,
        attempted_at: datetime,
    ) -> None: ...

    def blob_is_referenced(self, *, sha256: str) -> bool: ...
```

- `plan_selection` filters the exact configured Moodle `source_system` and preserves deterministic `RemoteFileORM.id` order.
- Batch mode selects only active rows. A requested inactive/missing/wrong-namespace row raises fixed `DocumentIngestionFailure(UNAUTHORIZED_ROUTE)` before fetch.
- `INGEST_NEW` places ready documents in `skipped`; `REFRESH` places all selected active rows in `fetch`.
- `persist_success` owns one `session.begin()` transaction. It creates/reuses `ContentBlob`, allocates the next version, inserts every page, and sets `Document` to `READY` with cleared error.
- Same document/SHA returns the existing version without mutation. The service reports that as `UNCHANGED`.
- `record_failure` creates/updates `Document`; status is `FAILED` only when no successful version exists, otherwise remains `READY`. It stores only the fixed error code.

- [ ] **Step 1: Write RED repository tests.**

```python
def test_ingest_new_skips_ready_document_without_fetch_selection(session_factory) -> None:
    plan = DocumentRepository(session_factory).plan_selection(
        mode=IngestionMode.INGEST_NEW,
        source_system="moodle:fixture",
        remote_file_id=None,
    )
    assert tuple(item.remote_file_id for item in plan.fetch) == ()
    assert len(plan.skipped) == 1

def test_failed_refresh_preserves_ready_status_and_versions(session_factory) -> None:
    repository = DocumentRepository(session_factory)
    repository.record_failure(
        selection=READY_SELECTION,
        code=IngestionErrorCode.NETWORK_FAILURE,
        attempted_at=UTC_NOW,
    )
    assert load_document().ingestion_status == IngestionStatus.READY
    assert load_document().last_error_code == IngestionErrorCode.NETWORK_FAILURE
```

Also test exact source filtering, inactive/specific selection rejection, deterministic order, unchanged SHA, contiguous versions, page rows, global blob reuse, and rollback on injected `SQLAlchemyError`.

- [ ] **Step 2: Run RED and prove repository absence.**

Run: `uv run pytest tests/test_document_persistence.py -v`

Expected: FAIL because `DocumentRepository`, `SelectionPlan`, and `PersistedVersion` do not exist.

- [ ] **Step 3: Implement selection and read-only lookup methods.**

Use SQLAlchemy queries constrained by exact `source_system`, `is_active is True`, and optional positive internal ID. Never query by caller-provided URL or title.

- [ ] **Step 4: Implement success/failure transactions.**

```python
with session.begin():
    document = _get_or_create_document(session, selection.remote_file_id, attempted_at)
    existing = _existing_version(session, document.id, blob.sha256)
    if existing is not None:
        document.ingestion_status = IngestionStatus.READY.value
        document.last_error_code = None
        document.last_attempted_at = attempted_at
        document.updated_at = attempted_at
        return _persisted_existing(existing)
    content_blob, created = _get_or_create_blob(session, blob, attempted_at)
    version_number = _next_version_number(session, document.id)
    version = _insert_version_and_pages(...)
    document.ingestion_status = IngestionStatus.READY.value
```

Catch `SQLAlchemyError`, roll back, and raise `DocumentIngestionFailure(PERSISTENCE_FAILED)` without native SQL or path text.

- [ ] **Step 5: Run GREEN repository verification.**

Run: `uv run pytest tests/test_document_persistence.py tests/test_academic_persistence.py -v`

Expected: PASS for selection, immutable history, status rules, deduplication, version allocation, provenance, and rollback.

- [ ] **Step 6: Run static checks.**

Run: `uv run ruff check src/aaos/documents/repository.py tests/test_document_persistence.py`

Run: `uv run mypy src/aaos/documents/repository.py tests/test_document_persistence.py`

Expected: PASS.

- [ ] **Step 7: Commit only Task 3 files.**

```text
git add src/aaos/documents/repository.py tests/test_document_persistence.py
git commit -m "feat(documents): add document ingestion repository"
```

### Task 4: Add content-addressed storage and safe filename metadata

**Files:**

- Create: `src/aaos/documents/storage.py`
- Create: `tests/test_document_storage.py`

**Interfaces:**

- Consumes: `BlobInstall`, `DocumentIngestionFailure`, `IngestionErrorCode`, and an injected absolute content-store root from `DocumentRuntimePaths`.
- Produces:

```python
def sanitize_source_filename(value: str | None, *, fallback: str) -> str: ...

@dataclass(frozen=True, slots=True)
class StagedBlob:
    sha256: str
    size_bytes: int
    temporary_path: Path = field(repr=False)

class ContentBlobStore:
    def __init__(self, root: Path) -> None: ...
    def stage(self, body: bytes) -> StagedBlob: ...
    def install(self, staged: StagedBlob) -> BlobInstall: ...
    def discard_temporary(self, staged: StagedBlob) -> None: ...
    def compensate_new_blob(self, install: BlobInstall, *, referenced: bool) -> None: ...
```

- `stage()` computes lowercase SHA-256, requires `1..MAX_PDF_BYTES`, writes only beneath `<root>/.temporary/`, flushes and `fsync()`s the file, and returns no bytes/path in `repr`.
- `install()` derives exactly `sha256/<first-two-hex>/<full-sha256>.pdf`, creates confined parents, atomically promotes with `os.replace`, and returns `created=False` only after verifying the existing regular file has the same size and SHA-256.
- Every root, parent, and target is checked for symlinks/reparse points and resolved containment before use. A pre-existing non-directory, link/reparse point, malformed target, or hash mismatch fails with `STORAGE_FAILED`.
- `sanitize_source_filename()` strips path components/control characters, replaces Windows-reserved characters, trims trailing dots/spaces, caps UTF-8-safe display metadata at 255 characters, rejects reserved device basenames, and uses `<fallback>.pdf`; it never supplies a filesystem path.

- [ ] **Step 1: Write RED filename and confinement tests.**

Add parameterized tests for `../../lecture.pdf`, `..\..\lecture.pdf`, `C:\something.pdf`, `/foo/bar.pdf`, and `lecture/../../x.pdf`; assert every result is one basename and no result affects `storage_key`. Add root/temporary/parent symlink or Windows reparse tests, `currentevil`-style containment confusion, empty/oversized bodies, and malformed existing blob tests.

- [ ] **Step 2: Run RED and prove storage is absent.**

Run: `uv run pytest tests/test_document_storage.py -v`

Expected: FAIL because `aaos.documents.storage` and its contracts do not exist.

- [ ] **Step 3: Implement sanitization, staging, and atomic installation.**

Use `tempfile.mkstemp(dir=root / ".temporary")`, `hashlib.sha256`, an implementation-private `_ensure_confined_regular_path()`, and fixed `DocumentIngestionFailure(STORAGE_FAILED)` errors. Do not reuse the Phase 2 private browser-profile helper because it raises browser-specific errors and has a different ownership boundary.

- [ ] **Step 4: Implement deduplication and compensation.**

`discard_temporary()` is idempotent for its owned missing temporary file. `compensate_new_blob()` removes only `install.created is True and referenced is False`; it never deletes a pre-existing/shared blob and removes an empty hash-prefix directory only when owned and empty.

- [ ] **Step 5: Run focused GREEN verification.**

Run: `uv run pytest tests/test_document_storage.py -v`

Expected: PASS for traversal inputs, atomic install, deterministic key, dedup verification, compensation, and Windows path/link safety.

- [ ] **Step 6: Run neighboring security checks.**

Run: `uv run pytest tests/test_moodle_browser_models.py tests/test_document_config.py -v`

Run: `uv run ruff check src/aaos/documents/storage.py tests/test_document_storage.py`

Run: `uv run mypy src/aaos/documents/storage.py tests/test_document_storage.py`

Expected: PASS and no production path is created.

- [ ] **Step 7: Commit only Task 4 files.**

```text
git add src/aaos/documents/storage.py tests/test_document_storage.py
git commit -m "feat(documents): add content-addressed blob store"
```

### Task 5: Add deterministic PDF validation and page extraction

**Files:**

- Create: `src/aaos/documents/pdf.py`
- Create: `tests/pdf_fixtures.py`
- Create: `tests/test_document_pdf.py`

**Interfaces:**

- Consumes: `bytes`, `ParsedPdf`, `ExtractedPage`, `ExtractionStatus`, `TextSource`, and PyMuPDF 1.28.2.
- Produces:

```python
class PdfProcessor:
    def process(self, body: bytes) -> ParsedPdf: ...
```

- `process()` checks byte length, opens from memory using `pymupdf.open(stream=body, filetype="pdf")`, rejects `needs_pass`, requires at least one page, and extracts `page.get_text("text")`, `page.rect.width`, and `page.rect.height` in stable 1-based order.
- `PDF_ENCRYPTED` means PyMuPDF opened the PDF far enough to determine that a password is required. `INVALID_PDF` means the bytes could not be opened or recognized as a valid PDF at the initial document-open/content-validation boundary. `PDF_PARSE_FAILED` means PyMuPDF opened a document successfully but deterministic processing later failed while reading page count, page structure, page geometry, or embedded page text. Native PyMuPDF exception text is never exposed. Media type and filename are never trusted as validation.
- All pages are retained. `SUCCESS` means every page has non-whitespace text, `PARTIAL` means a mixture, and `NO_TEXT` means none. Text is preserved as Unicode and `text_source` is always `EMBEDDED_PDF`.

- [ ] **Step 1: Create deterministic RED PDF fixtures/tests.**

Create temporary fixtures for one-page English, Arabic plus English, multiple pages, mixed text/empty, all-empty, malformed bytes, renamed HTML, and password-required encrypted PDF. Assert malformed/HTML input maps exactly to `INVALID_PDF`, password-required input maps exactly to `PDF_ENCRYPTED`, and page numbers, Unicode text, positive finite point dimensions, empty rows, and all three extraction statuses are deterministic. Use a controlled PyMuPDF document/page test double that succeeds at open and raises during page-count/structure/geometry/text processing to prove `PDF_PARSE_FAILED`; do not depend on unstable corrupt-PDF behavior.

- [ ] **Step 2: Run RED and prove the processor is absent.**

Run: `uv run pytest tests/test_document_pdf.py -v`

Expected: FAIL because `PdfProcessor` does not exist.

- [ ] **Step 3: Implement minimal PyMuPDF validation/extraction.**

Wrap only `pymupdf.open(stream=body, filetype="pdf")` in the initial-open boundary and translate failure there to `DocumentIngestionFailure(INVALID_PDF)`. After a successful open, check `document.needs_pass` and raise `PDF_ENCRYPTED`; then wrap page-count, page-load, geometry, and text extraction in the processing boundary and translate those failures to `PDF_PARSE_FAILED`. Keep the document inside a `with` block, build immutable page tuples, never write document bytes/text to logs or disk, and never preserve native exception text.

- [ ] **Step 4: Run focused GREEN verification.**

Run: `uv run pytest tests/test_document_pdf.py tests/test_document_contracts.py -v`

Expected: PASS for real-byte validation, encryption rejection, Unicode, geometry, empty-page retention, and status classification.

- [ ] **Step 5: Run static checks.**

Run: `uv run ruff check src/aaos/documents/pdf.py tests/pdf_fixtures.py tests/test_document_pdf.py`

Run: `uv run mypy src/aaos/documents/pdf.py tests/pdf_fixtures.py tests/test_document_pdf.py`

Expected: PASS.

- [ ] **Step 6: Commit only Task 5 files.**

```text
git add src/aaos/documents/pdf.py tests/pdf_fixtures.py tests/test_document_pdf.py
git commit -m "feat(documents): validate and extract PDF pages"
```

### Task 6: Add the independent Phase 3 single-writer lease

**Files:**

- Create: `src/aaos/documents/lock.py`
- Create: `tests/test_document_lock.py`

**Interfaces:**

- Consumes: validated `DocumentRuntimePaths.ingestion_lock_path`.
- Produces:

```python
class IngestionLockUnavailable(RuntimeError): ...

class IngestionLease:
    def __enter__(self) -> IngestionLease: ...
    def __exit__(self, exc_type, exc, traceback) -> None: ...
    def release(self) -> None: ...

class IngestionLock:
    def __init__(self, path: Path) -> None: ...
    def acquire(self) -> IngestionLease: ...
```

- `acquire()` validates confined non-link parents and atomically creates the exact lock file using `O_CREAT | O_EXCL`; its contents are a random non-secret ownership nonce only.
- Any pre-existing lock rejects the second job. There is no silent stale-lock deletion, PID killing, or lock bypass.
- `release()` is idempotent for its lease but removes the file only when the stored nonce still equals the lease nonce; mismatch fails closed.

- [ ] **Step 1: Write RED exclusive-ownership tests.**

Test first acquisition, second acquisition rejection, normal/exception cleanup, release idempotence, nonce mismatch, link/reparse/path escape, and a service-spy assertion that contention causes zero fetch, repository, or store calls.

- [ ] **Step 2: Run RED and prove the lock is absent.**

Run: `uv run pytest tests/test_document_lock.py -v`

Expected: FAIL because `IngestionLock` and `IngestionLease` do not exist.

- [ ] **Step 3: Implement atomic lease ownership.**

Use `os.open()`/`os.fdopen()` with exclusive creation, flush and `fsync`; always release through `finally`/context management after service cleanup. Emit only `document ingestion already active` for contention.

- [ ] **Step 4: Run GREEN and neighboring lock regressions.**

Run: `uv run pytest tests/test_document_lock.py tests/test_moodle_browser_models.py tests/test_moodle_session_lifetime.py -v`

Expected: PASS and the Phase 3 lock remains separate from the browser-profile lock.

- [ ] **Step 5: Run static checks.**

Run: `uv run ruff check src/aaos/documents/lock.py tests/test_document_lock.py`

Run: `uv run mypy src/aaos/documents/lock.py tests/test_document_lock.py`

Expected: PASS.

- [ ] **Step 6: Commit only Task 6 files.**

```text
git add src/aaos/documents/lock.py tests/test_document_lock.py
git commit -m "feat(documents): add single-writer ingestion lock"
```

### Task 7: Authorize and fetch one Moodle Resource inside the existing Edge context

**Files:**

- Create: `src/aaos/moodle/file_download.py`
- Create: `tests/test_moodle_file_download.py`
- Create: `tests/test_moodle_file_download_integration.py`
- Modify: `tests/test_moodle_browser_models.py`

**Interfaces:**

- Consumes: `RemoteFileSelection`, `FetchedPdf`, existing `MoodleSettings`, `AuthPaths`, `BrowserProfileLease`, `BrowserSessionProvisioner`, and the existing authenticated-course-index proof helper.
- Produces:

```python
@dataclass(frozen=True, slots=True)
class ResourceFetchPermit:
    remote_file_id: int
    source_id: str
    initial_url: str = field(repr=False)

class MoodleFileAuthorizer:
    def __init__(
        self,
        *,
        canonical_wwwroot: str,
        expected_source_system: str,
    ) -> None: ...
    def authorize(self, selection: RemoteFileSelection) -> ResourceFetchPermit: ...
    def authorize_redirect(
        self, *, permit: ResourceFetchPermit, current_url: str, location: str
    ) -> str: ...
    def authorize_final_url(self, *, permit: ResourceFetchPermit, url: str) -> None: ...

class BrowserAuthenticatedFetcher:
    def __init__(
        self,
        *,
        context: BrowserContext,
        authorizer: MoodleFileAuthorizer,
        timeout_ms: int = FETCH_TIMEOUT_MS,
    ) -> None: ...
    def fetch(self, selection: RemoteFileSelection) -> FetchedPdf: ...

@contextmanager
def open_phase3_fetcher_session(
    *,
    canonical_wwwroot: str,
    expected_source_system: str,
    settings: MoodleSettings,
    auth_paths: AuthPaths,
) -> Iterator[BrowserAuthenticatedFetcher]: ...
```

- `authorize()` compares `selection.source_system` only with the constructor's explicit `expected_source_system`, accepts positive database/course IDs and `source_id == "resource-cm:<positive decimal>"`, and constructs `<canonical_wwwroot>/mod/resource/view.php?id=<cmid>` itself. It performs no global/runtime configuration lookup, and no public method accepts a URL selector.
- Redirect authorization resolves a relative `Location`, rejects userinfo/fragments, non-HTTPS, origin/base-path mismatch, loopback/private/link-local/reserved IP literals, and every route except the exact initial Resource route followed by Moodle-owned redirect hops ending at a `pluginfile.php` path corresponding to that permit. Every hop is checked, cycles fail, and the maximum is five.
- `fetch()` uses only `context.request.get(url, max_redirects=0, timeout=FETCH_TIMEOUT_MS)`, follows validated redirects manually, requires final HTTP 200 and an authorized final URL, rejects a valid `Content-Length` above 50 MiB before reading, reads once, then verifies actual length.
- `open_phase3_fetcher_session()` constructs `MoodleFileAuthorizer(canonical_wwwroot=canonical_wwwroot, expected_source_system=expected_source_system)` from its explicit arguments, acquires the existing profile lease, launches the existing headed persistent `msedge` context, lets the human authenticate using the existing bounded proof flow, and closes context before releasing the profile lock. It never reads hidden/global configuration, exports cookies/storage state, or invokes `httpx`/`requests`.

- [ ] **Step 1: Write RED authorizer and transport tests.**

Test known `resource-cm:42`, explicit matching/mismatching `expected_source_system`, unknown/malformed source IDs, direct caller `pluginfile`, cross-origin, downgrade, schemes `file`/`data`/`javascript`, localhost/private/link-local destinations, wrong base path, unsupported routes, redirect cycles/overflow, wrong final target, non-200, oversized `Content-Length`, oversized actual bytes, and fixed non-sensitive errors. Assert URL-bearing values have redacted `repr` and that authorizer/session construction never reads global configuration.

- [ ] **Step 2: Run RED and prove the Phase 3 boundary is absent.**

Run: `uv run pytest tests/test_moodle_file_download.py tests/test_moodle_browser_models.py -v`

Expected: FAIL because `aaos.moodle.file_download` does not exist; the existing Phase 2 `pluginfile` rejection remains PASS.

- [ ] **Step 3: Implement the authorizer without changing Phase 2 policy.**

Use `urllib.parse`, `ipaddress`, exact normalized origin/base-path checks, and the fixed source-ID parser. Keep `src/aaos/moodle/browser_policy.py` unchanged.

- [ ] **Step 4: Implement manual Playwright request-context fetching.**

Do not enable downloads or page navigation to the file. Derive filename metadata from a sanitized `Content-Disposition` filename when safely parseable, otherwise `resource-<cmid>.pdf`; return only in-memory bytes.

- [ ] **Step 5: Add local HTTPS real-Playwright coverage.**

Reuse the repository's `trustme` temporary loopback HTTPS fixture pattern and `channel="msedge"`. In a test-only context with `ignore_https_errors=True`, prove an approved Resource-to-pluginfile redirect and PDF body pass; cross-origin, redirect loop, size overflow, and unapproved route fail. Assert certificate material stays under `tmp_path` and production context creation still omits `ignore_https_errors`.

- [ ] **Step 6: Run focused GREEN and Phase 2 regressions.**

Run: `uv run pytest tests/test_moodle_file_download.py tests/test_moodle_file_download_integration.py tests/test_moodle_browser_models.py tests/test_moodle_browser_navigation.py tests/test_moodle_browser_runtime.py -v`

Expected: PASS; Phase 2 remains download-disabled and rejects `pluginfile`.

- [ ] **Step 7: Run static checks.**

Run: `uv run ruff check src/aaos/moodle/file_download.py tests/test_moodle_file_download.py tests/test_moodle_file_download_integration.py tests/test_moodle_browser_models.py`

Run: `uv run mypy src/aaos/moodle/file_download.py tests/test_moodle_file_download.py`

Expected: PASS.

- [ ] **Step 8: Commit only Task 7 files.**

```text
git add src/aaos/moodle/file_download.py tests/test_moodle_file_download.py tests/test_moodle_file_download_integration.py tests/test_moodle_browser_models.py
git commit -m "feat(moodle): authorize phase3 resource fetches"
```

### Task 8: Orchestrate ingestion, retries, modes, deduplication, and compensation

**Files:**

- Create: `src/aaos/documents/service.py`
- Create: `tests/test_document_service.py`

**Interfaces:**

- Consumes: `DocumentRepository`, `ContentBlobStore`, `PdfProcessor`, `IngestionLock`, an already-bound `Callable[[], ContextManager[BrowserAuthenticatedFetcher]]`, and aware-UTC clock callable. Task 9 binds that factory with the explicit validated `canonical_wwwroot` and `expected_source_system`; the service never reads Moodle configuration.
- Produces:

```python
class Phase3IngestionService:
    def __init__(
        self,
        *,
        repository: DocumentRepository,
        blob_store: ContentBlobStore,
        pdf_processor: PdfProcessor,
        ingestion_lock: IngestionLock,
        fetcher_factory: Callable[[], ContextManager[BrowserAuthenticatedFetcher]],
        clock: Callable[[], datetime],
    ) -> None: ...

    def run(
        self,
        *,
        mode: IngestionMode,
        source_system: str,
        remote_file_id: int | None = None,
    ) -> BatchIngestionResult: ...
```

- `run()` acquires the ingestion lock before selection/fetch/store mutation, plans deterministic active selections, reports `SKIPPED` without opening the fetcher when all `ingest-new` rows are already successful, and otherwise uses one authenticated browser session for the sequential batch.
- For each selection: fetch with one initial attempt plus at most two retries only for `NETWORK_TIMEOUT`/`NETWORK_FAILURE`; sanitize filename; stage/hash; validate/extract the actual received PDF; return `UNCHANGED` without blob/version creation when the repository already has that validated SHA; otherwise install blob and persist one transaction; compensate on DB failure; discard temporary files in `finally`.
- Failures are isolated per document. `record_failure` runs after cleanup and cannot remove a prior successful version. The batch continues sequentially and exits nonzero if any selected item failed.
- Batch counts are exact: `remote_files_selected == len(selected)`, `new_versions` counts new `DocumentVersion`s, and `deduplicated_blobs` counts new versions that reused a pre-existing global blob.

- [ ] **Step 1: Write RED pipeline-order and retry tests.**

Use strict spies to assert `lock -> select -> fetch -> stage/hash -> validate/extract -> install -> persist -> cleanup -> unlock`. Prove exact attempts `3` for transient failures, success on retry, and exactly `1` for authorization, redirect, size, PDF, storage, and persistence failures.

- [ ] **Step 2: Write RED mode/version/batch tests.**

Cover: ingest-new skip causes zero download; refresh same SHA is `UNCHANGED`; changed bytes create versions 1/2; same bytes for two RemoteFiles create two Documents/two versions/one blob; A success/B invalid/C success retains A/C; inactive records are not auto-deleted; selector is database ID only; no partial resume/conditional headers.

- [ ] **Step 3: Write RED compensation tests.**

Inject repository failure after `install()`: newly created unreferenced blob is removed; pre-existing/shared blob is retained; no version is visible; no temporary artifact remains; lock releases after cleanup.

- [ ] **Step 4: Run RED and prove orchestration is absent.**

Run: `uv run pytest tests/test_document_service.py -v`

Expected: FAIL because `Phase3IngestionService` does not exist.

- [ ] **Step 5: Implement the minimal synchronous service.**

Keep retry classification as an explicit set `{NETWORK_TIMEOUT, NETWORK_FAILURE}`. Never retry `DocumentIngestionFailure` with any other code and never catch/print raw exception text. Do not add threads, tasks, queues, schedulers, or an attempt-history table.

- [ ] **Step 6: Run focused GREEN verification.**

Run: `uv run pytest tests/test_document_service.py tests/test_document_persistence.py tests/test_document_storage.py tests/test_document_pdf.py tests/test_document_lock.py -v`

Expected: PASS for ordering, retry counts, modes, independent transactions, dedup/version semantics, and compensation.

- [ ] **Step 7: Run static checks.**

Run: `uv run ruff check src/aaos/documents/service.py tests/test_document_service.py`

Run: `uv run mypy src/aaos/documents/service.py tests/test_document_service.py`

Expected: PASS.

- [ ] **Step 8: Commit only Task 8 files.**

```text
git add src/aaos/documents/service.py tests/test_document_service.py
git commit -m "feat(documents): add ingestion orchestration"
```

### Task 9: Add the exact document CLI and production composition

**Files:**

- Create: `src/aaos/documents/cli.py`
- Create: `tests/test_document_cli.py`
- Modify: `src/aaos/cli.py`
- Modify: `src/aaos/moodle/receiver.py`
- Modify: `tests/test_doctor_cli.py`
- Modify: `tests/test_moodle_receiver_config.py`

**Interfaces:**

- Consumes: existing receiver configuration resolution, `MoodleSettings`, `AuthPaths`, `DocumentRuntimePaths`, existing SQLAlchemy session factory, and all Tasks 1-8 components.
- Produces:

```python
@dataclass(frozen=True, slots=True)
class RuntimeMoodleEnvironment:
    bootstrap_config: MoodleBootstrapConfig
    settings: MoodleSettings
    auth_paths: AuthPaths
    data_root: Path

def load_runtime_moodle_environment() -> RuntimeMoodleEnvironment: ...
def run_documents_command(argv: Sequence[str]) -> int: ...
def build_phase3_service(
    *,
    bootstrap_config: MoodleBootstrapConfig,
    settings: MoodleSettings,
    auth_paths: AuthPaths,
    paths: DocumentRuntimePaths,
) -> Phase3IngestionService: ...
def render_batch_result(result: BatchIngestionResult) -> tuple[str, ...]: ...
```

- Exact accepted grammar is `aaos documents ingest-new [--remote-file-id <positive-int>]` and `aaos documents refresh [--remote-file-id <positive-int>]`. Unknown flags, duplicate selectors, missing/nonpositive IDs, URLs, and extra arguments exit `2` with fixed usage output.
- `load_runtime_moodle_environment()` performs the existing exact four-name Process/User resolution once, retains conflict fail-closed behavior, and returns a normalized absolute `data_root`. Existing `load_runtime_moodle_configuration()` delegates to it and preserves its current three-tuple API for all Phase 2 callers.
- `run_documents_command()` passes `environment.bootstrap_config`, `environment.settings`, `environment.auth_paths`, and paths derived from `environment.data_root` explicitly to `build_phase3_service()`.
- `build_phase3_service()` binds `open_phase3_fetcher_session(canonical_wwwroot=bootstrap_config.canonical_wwwroot, expected_source_system=settings.source_system, settings=settings, auth_paths=auth_paths)` in its fetcher factory. No Task 7/8/9 component re-reads hidden/global configuration.
- Production composition requires the existing migrated database file and validated content-store parent; it does not create/migrate the normal database or run Phase 2 synchronization. Store/temporary subdirectories may be created only after the ingestion lock is held by the service.
- Safe output contains one fixed per-document line using internal ID/status/numeric metadata/error code plus the seven aggregate names. It never renders filename, URL, body, text, exception, browser output, or credentials.

- [ ] **Step 1: Write RED exact-routing and privacy tests.**

Test both modes with/without selector, invalid grammar, URL rejection, exit codes, fixed batch fields, and secret sentinels injected into transport exceptions/results that must not appear in stdout/stderr/repr. Assert the exact runtime `bootstrap_config.canonical_wwwroot` and `settings.source_system` reach the fetcher factory/authorizer, with no second configuration read. Extend receiver configuration tests for identical Process/User values, disagreement, missing/malformed data root, exact four-name access, and the unchanged legacy tuple API.

- [ ] **Step 2: Write RED scope-isolation tests.**

Prove `aaos doctor`, Moodle bootstrap/browser commands, and document commands route only to their own lazy imports. Assert Phase 2 sync/browser collection never imports/calls `Phase3IngestionService`; document commands never call normalization, academic sync, entity collection, Moodle writes, downloads via Page, or normal DB migration.

- [ ] **Step 3: Run RED and prove the command is absent.**

Run: `uv run pytest tests/test_document_cli.py tests/test_doctor_cli.py tests/test_moodle_receiver_config.py -v`

Expected: FAIL because document command dispatch/composition does not exist.

- [ ] **Step 4: Implement exact CLI parsing and safe rendering.**

Follow the existing exact-argv dispatch style in `src/aaos/cli.py`; do not introduce a generic authenticated Moodle command or URL parameter. Convert only fixed `DocumentIngestionFailure.code`/lock errors to safe output.

- [ ] **Step 5: Implement production composition without side effects on import.**

Reuse configured User/process values through `load_runtime_moodle_environment()`, derive Phase 3 paths from its validated AAOS data root, require an existing migrated DB, inject the store/lock, and bind `open_phase3_fetcher_session` with the environment's explicit bootstrap canonical root, expected source namespace, settings, and auth paths. Do not open Edge until the service has selected at least one fetch candidate.

- [ ] **Step 6: Run GREEN CLI and neighboring regressions.**

Run: `uv run pytest tests/test_document_cli.py tests/test_doctor_cli.py tests/test_moodle_receiver_config.py tests/test_moodle_live_runner.py tests/test_moodle_browser_source.py -v`

Expected: PASS with unchanged existing CLI behavior and no implicit download path.

- [ ] **Step 7: Run static checks.**

Run: `uv run ruff check src/aaos/cli.py src/aaos/documents/cli.py src/aaos/moodle/receiver.py tests/test_document_cli.py tests/test_doctor_cli.py tests/test_moodle_receiver_config.py`

Run: `uv run mypy src/aaos/cli.py src/aaos/documents/cli.py src/aaos/moodle/receiver.py tests/test_document_cli.py tests/test_moodle_receiver_config.py`

Expected: PASS.

- [ ] **Step 8: Commit only Task 9 files.**

```text
git add src/aaos/cli.py src/aaos/documents/cli.py src/aaos/moodle/receiver.py tests/test_document_cli.py tests/test_doctor_cli.py tests/test_moodle_receiver_config.py
git commit -m "feat(cli): add document ingestion commands"
```

### Task 10: Prove disposable end-to-end ingestion and import safety

**Files:**

- Create: `tests/test_document_integration.py`
- Create: `tests/test_document_imports.py`

**Interfaces:**

- Consumes: the public `Phase3IngestionService` composition interfaces, existing Alembic test configuration/build helper, deterministic PDF fixtures, fake authorized fetcher, fresh `tmp_path` database, and fresh `tmp_path` content store.
- Produces no production interface; this task verifies the complete offline contract.

- [ ] **Step 1: Write the RED disposable integration test.**

Build a Phase 1 `RemoteFile` through `CompleteSourceSnapshot -> normalize_collection() -> AcademicSyncService.sync()` in a freshly migrated disposable SQLite database, then inject an authorized PDF fetcher and run `ingest-new`. Assert one Document/blob/version, one row per PDF page, exact provenance join to Course/RemoteFile, safe relative storage key, and no normal path access.

- [ ] **Step 2: Extend RED integration through refresh and dedup.**

Run refresh with the same bytes (`UNCHANGED`, no new version), changed bytes (version 2), and a second RemoteFile with identical bytes (shared blob). Assert contiguous versions, stable source-derived Documents, current source inactivity preserves history, and batch counts exactly match persisted rows.

- [ ] **Step 3: Add RED failure/compensation integration.**

Run a three-item batch where A is valid, B is invalid PDF, and C is valid; assert A/C READY, B FAILED with fixed code, nonzero exit, independent transactions, no false version, no orphan/temp files, and no Moodle write/download surface.

- [ ] **Step 4: Add import and normal-path sentinels.**

In a fresh child process import `aaos.documents`, `aaos.documents.cli`, and `aaos.moodle.file_download`; assert exit 0, no Edge/network launch, and that sentinel-absent `data/database/aaos.db` and `data/documents/` remain absent. Monkeypatch normal paths to raise if accessed during every disposable test.

- [ ] **Step 5: Run the full disposable integration test.**

Run: `uv run pytest tests/test_document_integration.py tests/test_document_imports.py -v`

Expected: PASS because Tasks 1-9 expose the exact interfaces frozen above; no normal DB/store is created. If it fails, stop the task and correct the owning earlier task in its named production file with a new RED regression before continuing.

- [ ] **Step 6: Run GREEN end-to-end and neighboring regressions.**

Run: `uv run pytest tests/test_document_integration.py tests/test_document_imports.py tests/test_document_service.py tests/test_moodle_file_download_integration.py tests/test_academic_sync.py -v`

Expected: PASS for fresh migration, source bridge, ingestion, refresh, dedup, failure isolation, provenance, cleanup, and normal-path absence.

- [ ] **Step 7: Run static checks.**

Run: `uv run ruff check tests/test_document_integration.py tests/test_document_imports.py src/aaos/documents src/aaos/moodle/file_download.py`

Run: `uv run mypy src tests/test_document_integration.py tests/test_document_imports.py`

Expected: PASS.

- [ ] **Step 8: Commit only Task 10 test files.**

```text
git add tests/test_document_integration.py tests/test_document_imports.py
git commit -m "test(documents): verify disposable ingestion integration"
```

### Task 11: Add the opt-in live gate and complete Phase 3 acceptance

**Files:**

- Create: `tests/live/test_phase3_document_ingestion_smoke.py`
- Temporarily create during the human-authorized live gate: `aaos_live_phase3_ingestion_acceptance.py`
- Remove before the final commit: `aaos_live_phase3_ingestion_acceptance.py`

**Interfaces:**

- Consumes: one human-approved positive Moodle Resource course-module ID (`cmid`), a freshly migrated disposable SQLite populated only through the existing `SourceCollection -> normalize_collection() -> AcademicSyncService.sync()` bridge, a fresh disposable content store, and the existing authenticated Edge profile/context. The runner resolves the disposable internal `RemoteFileORM.id` only after synchronization.
- Produces no retained production interface. The live runner emits only the exact fixed acceptance fields below and contains no filename/title/URL/body/text/token/cookie data.

- [ ] **Step 1: Write the default-skipped live-smoke guard.**

Require both `AAOS_RUN_LIVE_PHASE3_SMOKE=1` and a positive decimal `AAOS_PHASE3_RESOURCE_CMID`; otherwise `pytest.skip`. Assert the retained test can collect current Phase 2 metadata into a disposable DB, resolve exactly one active row in the expected namespace with `source_id == f"resource-cm:{cmid}"`, and invoke the production service only with that row's disposable internal ID. Normal `uv run pytest` must never launch Edge or contact Moodle.

- [ ] **Step 2: Run the guard test without opt-in.**

Run: `uv run pytest tests/live/test_phase3_document_ingestion_smoke.py -v`

Expected: exactly one intentional SKIP and no filesystem/network/browser side effect.

- [ ] **Step 3: Run the complete deterministic repository gate before live authorization.**

Run: `uv run pytest -q`

Run: `uv run ruff format --check .`

Run: `uv run ruff check .`

Run: `uv run mypy src tests`

Run: `uv lock --check`

Run: `git diff --check`

Expected: all deterministic checks PASS; only repository-approved live tests are skipped; `data/database/aaos.db` and `data/documents/` remain absent.

- [ ] **Step 4: Create the temporary privacy-safe live acceptance runner only after explicit human authorization.**

The runner accepts only `--resource-cmid <positive-decimal>`. It builds/migrates a disposable SQLite database, performs the approved current Phase 2 metadata collection, normalizes and synchronizes it into that database, then queries for exactly one active `RemoteFileORM` whose `source_system` equals the validated expected namespace and whose `source_id` equals `resource-cm:<cmid>`. Zero or multiple matches fail closed. The runner takes the matched disposable `RemoteFileORM.id`, invokes the real `Phase3IngestionService` with `remote_file_id=<resolved-id>` for `ingest-new` and `refresh`, and uses a disposable content store plus the existing Edge profile. It never adds cmid selection to the production CLI and does not hard-code filename, page count, size, or historical entity counts.

- [ ] **Step 5: Execute exactly one controlled live acceptance.**

Run from the normal Windows user session only after the human selects a current Moodle Resource known to be a PDF and sets its positive cmid: `uv run python aaos_live_phase3_ingestion_acceptance.py --resource-cmid $env:AAOS_PHASE3_RESOURCE_CMID`

Expected fixed output:

```text
LIVE_REMOTE_FILE_AUTHORIZATION=PASS
AUTH_CONTEXT=EXISTING_EDGE
COOKIE_EXPORT=NONE
DOWNLOAD=PASS
REDIRECT_POLICY=PASS
FINAL_ORIGIN=APPROVED_MOODLE
PDF_SIZE_LIMIT=PASS
PDF_VALIDATION=PASS
SHA256_COMPUTED=PASS
CONTENT_BLOB_INSTALL=PASS
PDF_EXTRACTION=PASS
PAGE_COUNT=<positive actual integer>
EXTRACTION_STATUS=<SUCCESS|PARTIAL|NO_TEXT>
DOCUMENT_CREATED=1
DOCUMENT_VERSION_CREATED=1
CONTENT_BLOB_CREATED=1
PAGE_ROWS=<same actual integer as PAGE_COUNT>
REFRESH_SAME_BYTES=PASS
NEW_VERSION_CREATED=NO
DEDUPLICATION=PASS
DISPOSABLE_DATABASE_REMOVED=YES
DISPOSABLE_CONTENT_STORE_REMOVED=YES
NORMAL_DATABASE_TOUCHED=NO
NORMAL_CONTENT_STORE_TOUCHED=NO
MOODLE_WRITES=NONE
COOKIE_EXPORT=NONE
LIVE_PHASE_3_ACCEPTANCE=PASS
```

Any authorization/authentication/redirect/size/PDF/storage/DB/cleanup uncertainty fails closed and stops; do not retry automatically or select another file.

- [ ] **Step 6: Remove the temporary runner and re-run final gates.**

Delete only `aaos_live_phase3_ingestion_acceptance.py` after confirming its exact workspace path; then rerun all six commands from Step 3. Verify ingestion/profile locks are released, disposable paths are removed, normal DB/store remain absent, and `reflex` is untouched.

- [ ] **Step 7: Perform the whole-phase security/design/spec review.**

Review every Phase 3 diff for: arbitrary URL/pluginfile reachability; redirect/origin/private-network bypass; cookie/storage export; size-limit gaps; filename/path/reparse attacks; PDF parser error leakage; blob/DB atomicity; shared-blob compensation; one-shot single-writer behavior; per-document transactions; inactive-history preservation; CLI weakening flags; Phase 2 download regression; OCR/RAG/PixelRAG/background/delete scope leakage; and normal-path writes. Fix Critical/Important findings test-first and rerun Step 3.

- [ ] **Step 8: Commit only the retained live guard after all acceptance evidence passes.**

```text
git add tests/live/test_phase3_document_ingestion_smoke.py
git commit -m "test(documents): add guarded phase3 live smoke"
```

Do not create `phase-03-passed`. Stop for human Phase 3 acceptance and explicit tag authorization.

---

## Specification Coverage Matrix

| Approved design area | Implemented by task(s) |
| --- | --- |
| 1. Purpose and scope | 1, 7-11 |
| 2. Frozen foundation and phase boundary | 7, 9-11 |
| 3. Design goals and non-goals | Global Constraints; 4-11 |
| 4. Chosen layered architecture | 3-9 |
| 5. Authentication and browser boundary | 7, 9, 11 |
| 6. Selection and download authorization | 3, 7, 9 |
| 7. Fetch and size contract | 1, 7, 8 |
| 8. PDF validation and filename handling | 4, 5, 8 |
| 9. Content-addressed storage | 4, 8, 10 |
| 10. Persistence model | 2, 3 |
| 11. Deduplication and versioning | 3, 8, 10 |
| 12. Page extraction and extraction status | 1, 5, 10 |
| 13. Trigger and command semantics | 3, 8, 9 |
| 14. Atomic ingestion and compensation | 3, 4, 8, 10 |
| 15. Failure codes and retry policy | 1, 7, 8 |
| 16. Single-writer and batch behavior | 6, 8, 9 |
| 17. Course provenance and observability | 2, 9, 10 |
| 18. Configuration | 1, 4, 9 |
| 19. Persistence and migration | 2, 3, 10 |
| 20. Testing strategy | Every task, especially 7, 8, and 10 |
| 21. Live Phase 3 acceptance | 11 |
| 22. Phase acceptance criteria | 10, 11 |
| 23. Final deterministic gates | 11 |
| 24. Explicit exclusions and future boundaries | Global Constraints; 9, 11 |
| 25. Governance housekeeping debt | Global Constraints; no implementation task changes those documents |

## Implementation Completion Gate

Phase 3 implementation is not complete until every task commit exists, the Task 11 deterministic and live gates pass, the temporary runner is absent, `data/database/aaos.db` and `data/documents/` are absent/untouched, both AAOS-owned locks are released, `reflex` remains untouched, and a human explicitly accepts the phase. Function availability or a passing unit suite alone is insufficient authorization to write normal data or begin Phase 4.
