"""Frozen public contracts for Phase 3 document ingestion."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


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


class StructureUnitKind(StrEnum):
    HEADING = "HEADING"
    PARAGRAPH = "PARAGRAPH"
    TABLE_CELL = "TABLE_CELL"


class IngestionErrorCode(StrEnum):
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    NETWORK_FAILURE = "NETWORK_FAILURE"
    DOWNLOAD_TOO_LARGE = "DOWNLOAD_TOO_LARGE"
    UNAUTHORIZED_REDIRECT = "UNAUTHORIZED_REDIRECT"
    UNAUTHORIZED_ROUTE = "UNAUTHORIZED_ROUTE"
    INVALID_PDF = "INVALID_PDF"
    PDF_ENCRYPTED = "PDF_ENCRYPTED"
    PDF_PARSE_FAILED = "PDF_PARSE_FAILED"
    INVALID_DOCX = "INVALID_DOCX"
    DOCX_PARSE_FAILED = "DOCX_PARSE_FAILED"
    UNSUPPORTED_DOCX_STRUCTURE = "UNSUPPORTED_DOCX_STRUCTURE"
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
class ExtractedStructureUnit:
    ordinal: int
    kind: StructureUnitKind
    text: str
    heading_level: int | None
    table_index: int | None
    row_index: int | None
    column_index: int | None


@dataclass(frozen=True, slots=True)
class ParsedDocx:
    units: tuple[ExtractedStructureUnit, ...]


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
    def exit_code(self) -> int:
        """Return a nonzero process result only when a document failed."""
        return 1 if self.failed > 0 else 0


class DocumentIngestionFailure(RuntimeError):
    """Internal fixed-message failure carrying one safe structured code."""

    def __init__(self, code: IngestionErrorCode) -> None:
        super().__init__("document ingestion failed")
        self.code = code


MAX_PDF_BYTES = 52_428_800
MAX_TRANSIENT_RETRIES = 2
MAX_AUTHORIZED_REDIRECTS = 5
FETCH_TIMEOUT_MS = 120_000
FILE_TYPE = FileType.PDF_ONLY
