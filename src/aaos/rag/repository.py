"""Authoritative SQLite repository for Phase 4 derived RAG state.

The repository is the only layer that knows about the SQLAlchemy mappings.  It
returns immutable values and translates persistence failures into the fixed
RAG error contract.  Phase 3 rows are queried but never mutated here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Callable, TypeVar, cast

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import Select

from aaos.documents.contracts import IngestionStatus
from aaos.persistence.academic_models import CourseORM, RemoteFileORM
from aaos.persistence.document_models import (
    ContentBlobORM,
    DocumentORM,
    DocumentPageORM,
    DocumentStructureUnitORM,
    DocumentVersionORM,
)
from aaos.persistence.rag_models import (
    RagChunkORM,
    RagChunkSourceSpanORM,
    RagChunkStructureSourceSpanORM,
    RagDocumentIndexORM,
    RagDocumentSummaryORM,
    RagIndexGenerationORM,
)
from aaos.rag.contracts import (
    MAX_RAG_TEXT_CHARACTERS,
    CourseScope,
    CoverageStatus,
    DocumentIndexStatus,
    GenerationStatus,
    GlobalScope,
    HistoricalScope,
    RagErrorCode,
    RagGenerationIdentity,
    RagOperationalError,
    RagScope,
)
from aaos.rag.lexical import (
    LexicalSearchResult,
    delete_fts_row,
    insert_fts_row,
    rebuild_fts_rows,
    search_fts,
)
from aaos.rag.summary_contracts import (
    StoredDocumentSummary,
    SummaryCacheKey,
    SummaryPoint,
)

SessionFactory = Callable[[], Session]
_Record = TypeVar("_Record")


@dataclass(frozen=True, slots=True)
class DocumentPageRecord:
    """Immutable Phase 3 page value returned for an eligible version."""

    document_page_id: int
    document_version_id: int
    page_number: int
    text: str
    text_source: str
    width_points: float
    height_points: float

    @property
    def id(self) -> int:
        """Return the stable Phase 3 page identity."""
        return self.document_page_id


@dataclass(frozen=True, slots=True)
class DocumentStructureUnitRecord:
    """Immutable structural provenance unit returned for a STRUCTURE version."""

    document_structure_unit_id: int
    document_version_id: int
    ordinal: int
    kind: str
    text: str
    heading_level: int | None
    table_index: int | None
    row_index: int | None
    column_index: int | None

    @property
    def id(self) -> int:
        return self.document_structure_unit_id


@dataclass(frozen=True, slots=True)
class DocumentVersionRecord:
    """Immutable Phase 3 provenance identity used by RAG indexing."""

    document_version_id: int
    document_id: int
    remote_file_id: int
    course_id: int
    version_number: int
    content_blob_id: int
    content_sha256: str
    source_filename: str
    provenance_mode: str
    page_count: int | None

    @property
    def id(self) -> int:
        """Return the stable Phase 3 version identity."""
        return self.document_version_id


@dataclass(frozen=True, slots=True)
class GenerationBuild:
    """Persisted generation metadata without exposing its ORM row."""

    generation_id: int
    status: GenerationStatus
    identity: RagGenerationIdentity
    chunker_version: str
    chunking_config: str
    lexical_normalizer_version: str
    embedding_model: str
    embedding_revision: str
    retrieval_config: str
    created_at: datetime
    activated_at: datetime | None

    @property
    def id(self) -> int:
        """Return the stable generation identifier."""
        return self.generation_id


@dataclass(frozen=True, slots=True)
class QuerySnapshot:
    """Immutable authorization snapshot for later retrieval services."""

    generation: RagGenerationIdentity
    scope: RagScope
    document_version_ids: tuple[int, ...]
    normalized_query: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.generation, RagGenerationIdentity):
            raise ValueError("generation must be a RagGenerationIdentity")
        if not isinstance(self.scope, (CourseScope, GlobalScope, HistoricalScope)):
            raise ValueError("scope must be a supported RAG scope")
        if isinstance(self.scope, HistoricalScope) and len(
            set(self.scope.document_version_ids)
        ) != len(self.scope.document_version_ids):
            raise ValueError("historical scope must not contain duplicate versions")
        if (
            not isinstance(self.document_version_ids, tuple)
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
                for value in self.document_version_ids
            )
            or len(set(self.document_version_ids)) != len(self.document_version_ids)
        ):
            raise ValueError("document_version_ids must be a tuple of unique positive integers")
        if (
            not isinstance(self.normalized_query, str)
            or len(self.normalized_query) > MAX_RAG_TEXT_CHARACTERS
        ):
            raise ValueError("normalized_query must be a string")


@dataclass(frozen=True, slots=True)
class DocumentIndexRecord:
    """Persisted per-generation document-index state."""

    document_index_id: int
    generation_id: int
    document_version_id: int
    status: DocumentIndexStatus
    coverage_status: CoverageStatus
    searchable_page_count: int
    chunk_count: int
    error_code: str | None


@dataclass(frozen=True, slots=True)
class ChunkRecord:
    """Persisted authoritative chunk value."""

    chunk_id: int
    chunk_key: str
    generation_id: int
    document_version_id: int
    ordinal: int
    heading: str | None
    exact_text: str
    token_count: int
    text_sha256: str
    provenance_mode: str
    page_start: int | None
    page_end: int | None


@dataclass(frozen=True, slots=True)
class SourceSpanRecord:
    """Persisted exact page-to-chunk provenance value."""

    source_span_id: int
    rag_chunk_id: int
    document_page_id: int
    ordinal: int
    page_number: int
    page_char_start: int
    page_char_end: int
    chunk_char_start: int
    chunk_char_end: int


@dataclass(frozen=True, slots=True)
class StructureSourceSpanRecord:
    """Persisted exact structural-unit-to-chunk provenance value."""

    source_span_id: int
    rag_chunk_id: int
    document_structure_unit_id: int
    ordinal: int
    unit_ordinal: int
    kind: str
    heading_level: int | None
    table_index: int | None
    row_index: int | None
    column_index: int | None
    source_char_start: int
    source_char_end: int
    chunk_char_start: int
    chunk_char_end: int


@dataclass(frozen=True, slots=True)
class AuthoritativeRetrievalChunk:
    """One snapshot-authorized chunk with immutable document provenance."""

    chunk: ChunkRecord
    document_version: DocumentVersionRecord
    source_spans: tuple[
        SourceSpanRecord | StructureSourceSpanRecord,
        ...,
    ]


@dataclass(frozen=True, slots=True)
class DocumentVerificationRecord:
    """SQLite-side integrity evidence for one publishable document index."""

    generation_id: int
    document_version_id: int
    chunk_keys: tuple[str, ...]
    chunk_count: int
    source_span_count: int
    fts_row_count: int


@dataclass(frozen=True, slots=True)
class GenerationIntegrityReport:
    """Safe cross-store-independent integrity evidence for one generation."""

    generation_id: int
    expected_document_count: int
    ready_document_count: int
    not_searchable_document_count: int
    preparing_document_count: int
    failed_document_count: int
    chunk_count: int
    fts_row_count: int
    valid: bool
    dense_vector_count: int = 0
    dense_index_healthy: bool = False

    def __post_init__(self) -> None:
        if (
            isinstance(self.generation_id, bool)
            or not isinstance(self.generation_id, int)
            or self.generation_id <= 0
        ):
            raise ValueError("generation_id must be positive")
        for name in (
            "expected_document_count",
            "ready_document_count",
            "not_searchable_document_count",
            "preparing_document_count",
            "failed_document_count",
            "chunk_count",
            "fts_row_count",
            "dense_vector_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be non-negative")
        if not isinstance(self.valid, bool) or not isinstance(self.dense_index_healthy, bool):
            raise ValueError("integrity flags must be bool")


@dataclass(frozen=True, slots=True)
class StagedDocumentIndexResult:
    """Verified PREPARING state awaiting one atomic ACTIVE publication."""

    generation: RagGenerationIdentity
    document_version_id: int
    intended_status: DocumentIndexStatus
    coverage_status: CoverageStatus
    searchable_page_count: int
    chunk_count: int
    verification: DocumentVerificationRecord | None

    def __post_init__(self) -> None:
        if not isinstance(self.generation, RagGenerationIdentity):
            raise ValueError("generation must be a RagGenerationIdentity")
        if (
            isinstance(self.document_version_id, bool)
            or not isinstance(self.document_version_id, int)
            or self.document_version_id <= 0
        ):
            raise ValueError("document_version_id must be positive")
        if self.intended_status not in {
            DocumentIndexStatus.READY,
            DocumentIndexStatus.NOT_SEARCHABLE,
        }:
            raise ValueError("intended_status must be READY or NOT_SEARCHABLE")
        if not isinstance(self.coverage_status, CoverageStatus):
            raise ValueError("coverage_status must be a CoverageStatus")
        for name in ("searchable_page_count", "chunk_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be nonnegative")
        if self.intended_status is DocumentIndexStatus.NOT_SEARCHABLE:
            coherent = (
                self.coverage_status is CoverageStatus.NONE
                and self.searchable_page_count == 0
                and self.chunk_count == 0
                and self.verification is None
            )
        else:
            # searchable_page_count is provenance-specific:
            # PAGE READY documents require positive page coverage,
            # while STRUCTURE READY documents intentionally use zero.
            # The repository validates that mode-specific invariant
            # against the actual DocumentVersion.
            coherent = (
                self.coverage_status
                in {
                    CoverageStatus.FULL,
                    CoverageStatus.PARTIAL,
                }
                and self.chunk_count > 0
                and isinstance(
                    self.verification,
                    DocumentVerificationRecord,
                )
            )
        if not coherent:
            raise ValueError("staged document metadata is incoherent")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _safe_database_error(code: RagErrorCode, error: SQLAlchemyError) -> RagOperationalError:
    return RagOperationalError(code)


def _without_duplicate_content(
    records: tuple[DocumentVersionRecord, ...],
) -> tuple[DocumentVersionRecord, ...]:
    """Index one copy of identical stored content per course.

    The same file can reach a course twice (for example a Moodle file and a
    Drive link to it). Indexing both splits retrieval and citations between
    identical chunks, so only the lowest version id is indexed. Copies in
    other courses stay, so course-scoped questions still find them.
    """
    kept: dict[tuple[int, int], DocumentVersionRecord] = {}
    for record in sorted(records, key=lambda item: item.document_version_id):
        kept.setdefault((record.course_id, record.content_blob_id), record)
    survivors = {id(record) for record in kept.values()}
    return tuple(record for record in records if id(record) in survivors)


def _as_generation_status(value: str) -> GenerationStatus:
    try:
        return GenerationStatus(value)
    except ValueError as error:
        raise RagOperationalError(RagErrorCode.GENERATION_ACTIVATION_FAILED) from error


def _as_index_status(value: str) -> DocumentIndexStatus:
    try:
        return DocumentIndexStatus(value)
    except ValueError as error:
        raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED) from error


def _as_coverage_status(value: str) -> CoverageStatus:
    try:
        return CoverageStatus(value)
    except ValueError as error:
        raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED) from error


class _ExpectedActiveGenerationUnset:
    __slots__ = ()


_EXPECTED_ACTIVE_GENERATION_UNSET = _ExpectedActiveGenerationUnset()


class RagRepository:
    """Typed, transaction-scoped access to authoritative Phase 4 SQLite state."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _version_filters() -> tuple[ColumnElement[bool], ...]:
        return (
            DocumentORM.ingestion_status == IngestionStatus.READY.value,
            RemoteFileORM.is_active.is_(True),
            CourseORM.is_active.is_(True),
        )

    @classmethod
    def _version_query(cls, *, ready_generation_id: int | None = None) -> Select[Any]:
        query = (
            select(
                DocumentVersionORM.id,
                DocumentVersionORM.document_id,
                RemoteFileORM.id.label("remote_file_id"),
                CourseORM.id.label("course_id"),
                DocumentVersionORM.version_number,
                DocumentVersionORM.content_blob_id,
                ContentBlobORM.sha256.label("content_sha256"),
                DocumentVersionORM.source_filename,
                DocumentVersionORM.provenance_mode,
                DocumentVersionORM.page_count,
            )
            .join(DocumentORM, DocumentVersionORM.document_id == DocumentORM.id)
            .join(ContentBlobORM, DocumentVersionORM.content_blob_id == ContentBlobORM.id)
            .join(RemoteFileORM, DocumentORM.remote_file_id == RemoteFileORM.id)
            .join(CourseORM, RemoteFileORM.course_id == CourseORM.id)
            .where(*cls._version_filters())
        )
        if ready_generation_id is not None:
            query = query.join(
                RagDocumentIndexORM,
                (RagDocumentIndexORM.document_version_id == DocumentVersionORM.id)
                & (RagDocumentIndexORM.generation_id == ready_generation_id)
                & (RagDocumentIndexORM.status == DocumentIndexStatus.READY.value),
            )
        return query

    @staticmethod
    def _record_version(row: Any) -> DocumentVersionRecord:
        return DocumentVersionRecord(
            document_version_id=int(row.id),
            document_id=int(row.document_id),
            remote_file_id=int(row.remote_file_id),
            course_id=int(row.course_id),
            version_number=int(row.version_number),
            content_blob_id=int(row.content_blob_id),
            content_sha256=str(row.content_sha256),
            source_filename=str(row.source_filename),
            provenance_mode=str(row.provenance_mode),
            page_count=(None if row.page_count is None else int(row.page_count)),
        )

    def _resolve_versions_in_session(
        self,
        session: Session,
        scope: RagScope,
        *,
        current: bool,
        ready_generation_id: int | None = None,
    ) -> tuple[DocumentVersionRecord, ...]:
        if not isinstance(scope, (CourseScope, GlobalScope, HistoricalScope)):
            raise RagOperationalError(RagErrorCode.INVALID_QUERY_SCOPE)
        if isinstance(scope, HistoricalScope):
            if current:
                raise RagOperationalError(RagErrorCode.INVALID_QUERY_SCOPE)
            requested_ids = tuple(sorted(set(scope.document_version_ids)))
            query = self._version_query(ready_generation_id=ready_generation_id).where(
                DocumentVersionORM.id.in_(requested_ids)
            )
        else:
            query = self._version_query(ready_generation_id=ready_generation_id)
            if isinstance(scope, CourseScope):
                query = query.where(CourseORM.id == scope.course_id)
            latest = (
                select(
                    DocumentVersionORM.document_id,
                    func.max(DocumentVersionORM.version_number).label("max_version"),
                )
                .join(DocumentORM, DocumentVersionORM.document_id == DocumentORM.id)
                .join(RemoteFileORM, DocumentORM.remote_file_id == RemoteFileORM.id)
                .join(CourseORM, RemoteFileORM.course_id == CourseORM.id)
                .where(*self._version_filters())
                .group_by(DocumentVersionORM.document_id)
                .subquery()
            )
            query = query.join(
                latest,
                (DocumentVersionORM.document_id == latest.c.document_id)
                & (DocumentVersionORM.version_number == latest.c.max_version),
            )
        rows = session.execute(
            query.order_by(CourseORM.id, RemoteFileORM.id, DocumentVersionORM.id)
        ).all()
        records = tuple(self._record_version(row) for row in rows)
        if not records:
            raise RagOperationalError(RagErrorCode.NO_ELIGIBLE_DOCUMENTS)
        return records

    def _resolve_versions(
        self, scope: RagScope, *, current: bool
    ) -> tuple[DocumentVersionRecord, ...]:
        session = self._session_factory()
        try:
            with session.begin():
                return self._resolve_versions_in_session(session, scope, current=current)
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            code = RagErrorCode.PROVENANCE_RESOLUTION_FAILED
            raise _safe_database_error(code, error) from error
        finally:
            session.close()

    def resolve_current_versions(
        self, scope: CourseScope | GlobalScope
    ) -> tuple[DocumentVersionRecord, ...]:
        """Resolve newest eligible version per document for course/global scope."""
        return self._resolve_versions(scope, current=True)

    def resolve_index_new_candidates(
        self,
        identity: RagGenerationIdentity,
        document_version_id: int | None = None,
    ) -> tuple[DocumentVersionRecord, ...]:
        """Return current eligible versions not terminally published in ACTIVE."""
        if document_version_id is not None and (
            isinstance(document_version_id, bool)
            or not isinstance(document_version_id, int)
            or document_version_id <= 0
        ):
            raise RagOperationalError(RagErrorCode.NO_ELIGIBLE_DOCUMENTS)
        session = self._session_factory()
        try:
            with session.begin():
                generation = self._require_generation(session, identity, identity.content_revision)
                if generation.status != GenerationStatus.ACTIVE.value:
                    raise RagOperationalError(RagErrorCode.NO_ACTIVE_GENERATION)
                try:
                    current = _without_duplicate_content(
                        self._resolve_versions_in_session(session, GlobalScope(), current=True)
                    )
                except RagOperationalError as error:
                    if error.code is not RagErrorCode.NO_ELIGIBLE_DOCUMENTS:
                        raise
                    current = ()
                if document_version_id is not None:
                    current = tuple(
                        item for item in current if item.document_version_id == document_version_id
                    )
                    if not current:
                        raise RagOperationalError(RagErrorCode.NO_ELIGIBLE_DOCUMENTS)
                statuses = {
                    int(row.document_version_id): _as_index_status(row.status)
                    for row in session.scalars(
                        select(RagDocumentIndexORM).where(
                            RagDocumentIndexORM.generation_id == identity.generation_id,
                            RagDocumentIndexORM.document_version_id.in_(
                                tuple(item.document_version_id for item in current)
                            ),
                        )
                    )
                }
                return tuple(
                    sorted(
                        (
                            item
                            for item in current
                            if statuses.get(item.document_version_id)
                            not in {
                                DocumentIndexStatus.READY,
                                DocumentIndexStatus.NOT_SEARCHABLE,
                            }
                        ),
                        key=lambda item: (
                            item.document_id,
                            item.version_number,
                            item.document_version_id,
                        ),
                    )
                )
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.PROVENANCE_RESOLUTION_FAILED, error) from error
        finally:
            session.close()

    def resolve_historical_versions(
        self, scope: HistoricalScope
    ) -> tuple[DocumentVersionRecord, ...]:
        """Resolve only explicitly requested eligible historical versions."""
        return self._resolve_versions(scope, current=False)

    @classmethod
    def _published_version_query(
        cls,
        generation_id: int,
        *,
        document_version_ids: tuple[int, ...] | None = None,
        course_id: int | None = None,
    ) -> Select[Any]:
        """Build the terminal-publication view used by query authorization."""
        query = (
            cls._version_query()
            .add_columns(RagDocumentIndexORM.status.label("published_status"))
            .join(
                RagDocumentIndexORM,
                (RagDocumentIndexORM.document_version_id == DocumentVersionORM.id)
                & (RagDocumentIndexORM.generation_id == generation_id)
                & RagDocumentIndexORM.status.in_(
                    (
                        DocumentIndexStatus.READY.value,
                        DocumentIndexStatus.NOT_SEARCHABLE.value,
                    )
                ),
            )
        )
        if document_version_ids is not None:
            query = query.where(DocumentVersionORM.id.in_(document_version_ids))
        if course_id is not None:
            query = query.where(CourseORM.id == course_id)
        return query

    def _resolve_current_query_versions_in_session(
        self, session: Session, scope: CourseScope | GlobalScope, generation_id: int
    ) -> tuple[tuple[DocumentVersionRecord, ...], bool]:
        """Resolve latest published versions and report whether any were published."""
        query = self._published_version_query(
            generation_id,
            course_id=scope.course_id if isinstance(scope, CourseScope) else None,
        ).order_by(
            CourseORM.id,
            RemoteFileORM.id,
            DocumentVersionORM.document_id,
            DocumentVersionORM.version_number.desc(),
            DocumentVersionORM.id.desc(),
        )
        rows = session.execute(query).all()
        latest_by_document: dict[int, Any] = {}
        for row in rows:
            document_id = int(row.document_id)
            latest_by_document.setdefault(document_id, row)
        ordered_rows = tuple(
            sorted(
                latest_by_document.values(),
                key=lambda row: (int(row.course_id), int(row.remote_file_id), int(row.id)),
            )
        )
        records = tuple(
            self._record_version(row)
            for row in ordered_rows
            if row.published_status == DocumentIndexStatus.READY.value
        )
        return records, bool(rows)

    def _resolve_historical_query_versions_in_session(
        self,
        session: Session,
        scope: HistoricalScope,
        generation_ids: tuple[int, ...],
    ) -> tuple[RagGenerationIdentity, tuple[DocumentVersionRecord, ...]]:
        """Select one generation containing every requested published version."""
        requested_ids = tuple(sorted(scope.document_version_ids))
        if len(set(requested_ids)) != len(requested_ids):
            raise RagOperationalError(RagErrorCode.INVALID_QUERY_SCOPE)
        for generation_id in generation_ids:
            rows = session.execute(
                self._published_version_query(generation_id, document_version_ids=requested_ids)
            ).all()
            by_version = {int(row.id): row for row in rows}
            if set(by_version) != set(requested_ids):
                continue
            generation = session.get(RagIndexGenerationORM, generation_id)
            if generation is None:
                continue
            records = tuple(
                self._record_version(by_version[version_id])
                for version_id in requested_ids
                if by_version[version_id].published_status == DocumentIndexStatus.READY.value
            )
            return self._generation_from_row(generation).identity, records
        raise RagOperationalError(RagErrorCode.INVALID_QUERY_SCOPE)

    def pin_query_snapshot(self, scope: RagScope, *, normalized_query: str = "") -> QuerySnapshot:
        """Pin one coherent generation/revision and immutable version universe."""
        session = self._session_factory()
        try:
            with session.begin():
                active = session.scalar(
                    select(RagIndexGenerationORM)
                    .where(RagIndexGenerationORM.status == GenerationStatus.ACTIVE.value)
                    .order_by(RagIndexGenerationORM.id)
                )
                if active is None:
                    raise RagOperationalError(RagErrorCode.NO_ACTIVE_GENERATION)
                if not isinstance(scope, (CourseScope, GlobalScope, HistoricalScope)):
                    raise RagOperationalError(RagErrorCode.INVALID_QUERY_SCOPE)
                if isinstance(scope, HistoricalScope):
                    retired_ids = tuple(
                        int(value)
                        for value in session.scalars(
                            select(RagIndexGenerationORM.id)
                            .where(RagIndexGenerationORM.status == GenerationStatus.RETIRED.value)
                            .order_by(RagIndexGenerationORM.id.desc())
                        ).all()
                    )
                    generation_identity, records = (
                        self._resolve_historical_query_versions_in_session(
                            session,
                            scope,
                            (int(active.id), *retired_ids),
                        )
                    )
                else:
                    records, published = self._resolve_current_query_versions_in_session(
                        session, scope, int(active.id)
                    )
                    generation_identity = self._generation_from_row(active).identity
                    if not published:
                        raise RagOperationalError(RagErrorCode.NO_ELIGIBLE_DOCUMENTS)
                return QuerySnapshot(
                    generation=generation_identity,
                    scope=scope,
                    document_version_ids=tuple(item.document_version_id for item in records),
                    normalized_query=normalized_query,
                )
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.PROVENANCE_RESOLUTION_FAILED, error) from error
        finally:
            session.close()

    create_query_snapshot = pin_query_snapshot

    def read_pages(self, document_version_id: int) -> tuple[DocumentPageRecord, ...]:
        """Read ordered pages for one eligible Phase 3 version."""
        if (
            isinstance(document_version_id, bool)
            or not isinstance(document_version_id, int)
            or document_version_id <= 0
        ):
            raise RagOperationalError(RagErrorCode.PROVENANCE_RESOLUTION_FAILED)
        session = self._session_factory()
        try:
            with session.begin():
                version = session.execute(
                    self._version_query().where(DocumentVersionORM.id == document_version_id)
                ).first()
                if version is None:
                    raise RagOperationalError(RagErrorCode.PROVENANCE_RESOLUTION_FAILED)
                rows = session.scalars(
                    select(DocumentPageORM)
                    .where(DocumentPageORM.document_version_id == document_version_id)
                    .order_by(DocumentPageORM.page_number, DocumentPageORM.id)
                ).all()
                return tuple(
                    DocumentPageRecord(
                        document_page_id=int(row.id),
                        document_version_id=int(row.document_version_id),
                        page_number=int(row.page_number),
                        text=row.text,
                        text_source=row.text_source,
                        width_points=float(row.width_points),
                        height_points=float(row.height_points),
                    )
                    for row in rows
                )
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.PROVENANCE_RESOLUTION_FAILED, error) from error
        finally:
            session.close()

    @staticmethod
    def _generation_from_row(row: RagIndexGenerationORM) -> GenerationBuild:
        identity = RagGenerationIdentity(
            generation_id=int(row.id),
            config_fingerprint=row.config_fingerprint,
            content_revision=int(row.content_revision),
            embedding_dimensions=int(row.embedding_dimensions),
        )
        return GenerationBuild(
            generation_id=int(row.id),
            status=_as_generation_status(row.status),
            identity=identity,
            chunker_version=row.chunker_version,
            chunking_config=row.chunking_config,
            lexical_normalizer_version=row.lexical_normalizer_version,
            embedding_model=row.embedding_model,
            embedding_revision=row.embedding_revision,
            retrieval_config=row.retrieval_config,
            created_at=row.created_at,
            activated_at=row.activated_at,
        )

    def create_generation(
        self,
        *,
        config_fingerprint: str,
        chunker_version: str,
        chunking_config: str,
        lexical_normalizer_version: str,
        embedding_model: str,
        embedding_revision: str,
        embedding_dimensions: int,
        retrieval_config: str,
        content_revision: int = 0,
    ) -> GenerationBuild:
        """Create an invisible BUILDING generation with revision zero by default."""
        if (
            not isinstance(config_fingerprint, str)
            or len(config_fingerprint) != 64
            or any(char not in "0123456789abcdef" for char in config_fingerprint)
            or isinstance(embedding_dimensions, bool)
            or not isinstance(embedding_dimensions, int)
            or embedding_dimensions <= 0
            or isinstance(content_revision, bool)
            or not isinstance(content_revision, int)
            or content_revision < 0
        ):
            raise RagOperationalError(RagErrorCode.GENERATION_ACTIVATION_FAILED)
        fields = (
            chunker_version,
            chunking_config,
            lexical_normalizer_version,
            embedding_model,
            embedding_revision,
            retrieval_config,
        )
        if any(not isinstance(value, str) or not value.strip() for value in fields):
            raise RagOperationalError(RagErrorCode.GENERATION_ACTIVATION_FAILED)
        session = self._session_factory()
        try:
            with session.begin():
                row = RagIndexGenerationORM(
                    status=GenerationStatus.BUILDING.value,
                    config_fingerprint=config_fingerprint,
                    chunker_version=chunker_version,
                    chunking_config=chunking_config,
                    lexical_normalizer_version=lexical_normalizer_version,
                    embedding_model=embedding_model,
                    embedding_revision=embedding_revision,
                    embedding_dimensions=embedding_dimensions,
                    retrieval_config=retrieval_config,
                    content_revision=content_revision,
                    created_at=_now_utc(),
                )
                session.add(row)
                session.flush()
                return self._generation_from_row(row)
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.GENERATION_ACTIVATION_FAILED, error) from error
        finally:
            session.close()

    def get_generation(self, generation_id: int) -> GenerationBuild:
        """Read one generation without exposing its ORM object."""
        if (
            isinstance(generation_id, bool)
            or not isinstance(generation_id, int)
            or generation_id <= 0
        ):
            raise RagOperationalError(RagErrorCode.GENERATION_ACTIVATION_FAILED)
        session = self._session_factory()
        try:
            with session.begin():
                row = session.get(RagIndexGenerationORM, generation_id)
                if row is None:
                    raise RagOperationalError(RagErrorCode.GENERATION_ACTIVATION_FAILED)
                return self._generation_from_row(row)
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.GENERATION_ACTIVATION_FAILED, error) from error
        finally:
            session.close()

    def get_active_generation(self) -> GenerationBuild:
        """Return the sole ACTIVE generation or a fixed safe error."""
        session = self._session_factory()
        try:
            with session.begin():
                row = session.scalar(
                    select(RagIndexGenerationORM)
                    .where(RagIndexGenerationORM.status == GenerationStatus.ACTIVE.value)
                    .order_by(RagIndexGenerationORM.id)
                )
                if row is None:
                    raise RagOperationalError(RagErrorCode.NO_ACTIVE_GENERATION)
                return self._generation_from_row(row)
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.NO_ACTIVE_GENERATION, error) from error
        finally:
            session.close()

    def list_building_generations(self) -> tuple[GenerationBuild, ...]:
        """Return BUILDING generations in stable creation order."""
        session = self._session_factory()
        try:
            with session.begin():
                rows = session.scalars(
                    select(RagIndexGenerationORM)
                    .where(RagIndexGenerationORM.status == GenerationStatus.BUILDING.value)
                    .order_by(RagIndexGenerationORM.id)
                ).all()
                return tuple(self._generation_from_row(row) for row in rows)
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.GENERATION_ACTIVATION_FAILED, error) from error
        finally:
            session.close()

    def resolve_rebuild_candidates(self) -> tuple[DocumentVersionRecord, ...]:
        """Resolve the complete current eligible Phase 3 corpus deterministically."""
        session = self._session_factory()
        try:
            with session.begin():
                try:
                    return _without_duplicate_content(
                        self._resolve_versions_in_session(session, GlobalScope(), current=True)
                    )
                except RagOperationalError as error:
                    if error.code is RagErrorCode.NO_ELIGIBLE_DOCUMENTS:
                        return ()
                    raise
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.PROVENANCE_RESOLUTION_FAILED, error) from error
        finally:
            session.close()

    def _require_generation(
        self,
        session: Session,
        identity: RagGenerationIdentity,
        expected_revision: int,
    ) -> RagIndexGenerationORM:
        if not isinstance(identity, RagGenerationIdentity):
            raise RagOperationalError(RagErrorCode.GENERATION_ACTIVATION_FAILED)
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
            or expected_revision != identity.content_revision
        ):
            raise RagOperationalError(RagErrorCode.GENERATION_ACTIVATION_FAILED)
        row = session.scalar(
            select(RagIndexGenerationORM).where(
                RagIndexGenerationORM.id == identity.generation_id,
                RagIndexGenerationORM.config_fingerprint == identity.config_fingerprint,
                RagIndexGenerationORM.content_revision == expected_revision,
                RagIndexGenerationORM.embedding_dimensions == identity.embedding_dimensions,
            )
        )
        if row is None:
            raise RagOperationalError(RagErrorCode.GENERATION_ACTIVATION_FAILED)
        return row

    @staticmethod
    def _resolve_snapshot_generation(
        session: Session, snapshot: QuerySnapshot
    ) -> RagIndexGenerationORM:
        """Resolve only the immutable identity of an already-created snapshot."""
        if not isinstance(snapshot, QuerySnapshot):
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
        row = session.scalar(
            select(RagIndexGenerationORM).where(
                RagIndexGenerationORM.id == snapshot.generation.generation_id,
                RagIndexGenerationORM.config_fingerprint == snapshot.generation.config_fingerprint,
                RagIndexGenerationORM.embedding_dimensions
                == snapshot.generation.embedding_dimensions,
            )
        )
        if row is None:
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
        return row

    @classmethod
    def _require_snapshot_generation(
        cls, session: Session, snapshot: QuerySnapshot
    ) -> RagIndexGenerationORM:
        """Require an ACTIVE or RETIRED generation for materialization."""
        row = cls._resolve_snapshot_generation(session, snapshot)
        if row.status not in {
            GenerationStatus.ACTIVE.value,
            GenerationStatus.RETIRED.value,
        }:
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
        return row

    def activate_generation(
        self,
        identity: RagGenerationIdentity,
        *,
        expected_revision: int,
        expected_active_generation_id: (
            int | None | _ExpectedActiveGenerationUnset
        ) = _EXPECTED_ACTIVE_GENERATION_UNSET,
    ) -> GenerationBuild:
        """Atomically retire the expected ACTIVE row and activate a BUILDING row."""
        session = self._session_factory()
        try:
            with session.begin():
                row = self._require_generation(session, identity, expected_revision)
                if row.status != GenerationStatus.BUILDING.value:
                    raise RagOperationalError(RagErrorCode.GENERATION_ACTIVATION_FAILED)
                active_rows = tuple(
                    session.scalars(
                        select(RagIndexGenerationORM)
                        .where(RagIndexGenerationORM.status == GenerationStatus.ACTIVE.value)
                        .order_by(RagIndexGenerationORM.id)
                    ).all()
                )
                if len(active_rows) > 1:
                    raise RagOperationalError(RagErrorCode.GENERATION_ACTIVATION_FAILED)
                if isinstance(expected_active_generation_id, _ExpectedActiveGenerationUnset):
                    pass
                elif expected_active_generation_id is None:
                    if active_rows:
                        raise RagOperationalError(RagErrorCode.GENERATION_ACTIVATION_FAILED)
                elif (
                    isinstance(expected_active_generation_id, bool)
                    or not isinstance(expected_active_generation_id, int)
                    or expected_active_generation_id <= 0
                    or len(active_rows) != 1
                    or int(active_rows[0].id) != expected_active_generation_id
                ):
                    raise RagOperationalError(RagErrorCode.GENERATION_ACTIVATION_FAILED)
                if active_rows:
                    active_rows[0].status = GenerationStatus.RETIRED.value
                row.status = GenerationStatus.ACTIVE.value
                row.activated_at = _now_utc()
                session.flush()
                return self._generation_from_row(row)
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.GENERATION_ACTIVATION_FAILED, error) from error
        finally:
            session.close()

    def verify_generation(
        self,
        identity: RagGenerationIdentity,
        expected_document_version_ids: tuple[int, ...],
    ) -> GenerationIntegrityReport:
        """Validate one generation's authoritative SQLite/FTS state."""
        if (
            not isinstance(expected_document_version_ids, tuple)
            or any(
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
                for value in expected_document_version_ids
            )
            or len(set(expected_document_version_ids)) != len(expected_document_version_ids)
        ):
            raise RagOperationalError(RagErrorCode.INDEX_INTEGRITY_FAILED)
        session = self._session_factory()
        try:
            with session.begin():
                self._require_generation(session, identity, identity.content_revision)
                expected = tuple(expected_document_version_ids)
                expected_set = set(expected)
                rows = tuple(
                    session.scalars(
                        select(RagDocumentIndexORM)
                        .where(RagDocumentIndexORM.generation_id == identity.generation_id)
                        .order_by(RagDocumentIndexORM.document_version_id, RagDocumentIndexORM.id)
                    ).all()
                )
                row_ids = {int(row.document_version_id) for row in rows}
                ready = sum(row.status == DocumentIndexStatus.READY.value for row in rows)
                not_searchable = sum(
                    row.status == DocumentIndexStatus.NOT_SEARCHABLE.value for row in rows
                )
                preparing = sum(row.status == DocumentIndexStatus.PREPARING.value for row in rows)
                failed = sum(row.status == DocumentIndexStatus.FAILED.value for row in rows)
                chunk_count = int(
                    session.scalar(
                        select(func.count(RagChunkORM.id)).where(
                            RagChunkORM.generation_id == identity.generation_id
                        )
                    )
                    or 0
                )
                fts_row_count = int(
                    session.scalar(
                        text(
                            "SELECT count(*) FROM rag_chunks_fts "
                            "WHERE generation_id = :generation_id"
                        ),
                        {"generation_id": identity.generation_id},
                    )
                    or 0
                )
                valid = row_ids == expected_set and not preparing and not failed
                if valid:
                    for row in rows:
                        document_version_id = int(row.document_version_id)
                        if row.status == DocumentIndexStatus.READY.value:
                            try:
                                verification = self._verify_generation_document_in_session(
                                    session, identity, document_version_id
                                )
                            except RagOperationalError:
                                valid = False
                                break
                            if verification.chunk_count != int(row.chunk_count):
                                valid = False
                                break
                        elif row.status == DocumentIndexStatus.NOT_SEARCHABLE.value:
                            if (
                                row.coverage_status != CoverageStatus.NONE.value
                                or int(row.searchable_page_count) != 0
                                or int(row.chunk_count) != 0
                                or session.scalar(
                                    select(func.count(RagChunkORM.id)).where(
                                        RagChunkORM.generation_id == identity.generation_id,
                                        RagChunkORM.document_version_id == document_version_id,
                                    )
                                )
                            ):
                                valid = False
                                break
                        else:
                            valid = False
                            break
                if chunk_count != fts_row_count:
                    valid = False
                return GenerationIntegrityReport(
                    generation_id=identity.generation_id,
                    expected_document_count=len(expected),
                    ready_document_count=ready,
                    not_searchable_document_count=not_searchable,
                    preparing_document_count=preparing,
                    failed_document_count=failed,
                    chunk_count=chunk_count,
                    fts_row_count=fts_row_count,
                    valid=valid,
                )
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.INDEX_INTEGRITY_FAILED, error) from error
        finally:
            session.close()

    def recover_building_generation(self, identity: RagGenerationIdentity) -> tuple[int, ...]:
        """Mark interrupted PREPARING documents failed without changing ACTIVE state."""
        session = self._session_factory()
        recovered: list[int] = []
        try:
            with session.begin():
                generation = self._require_generation(session, identity, identity.content_revision)
                if generation.status != GenerationStatus.BUILDING.value:
                    raise RagOperationalError(RagErrorCode.GENERATION_ACTIVATION_FAILED)
                rows = session.scalars(
                    select(RagDocumentIndexORM).where(
                        RagDocumentIndexORM.generation_id == identity.generation_id,
                        RagDocumentIndexORM.status == DocumentIndexStatus.PREPARING.value,
                    )
                ).all()
                for row in rows:
                    self._delete_document_chunks(
                        session,
                        generation_id=identity.generation_id,
                        document_version_id=int(row.document_version_id),
                    )
                    row.status = DocumentIndexStatus.FAILED.value
                    row.coverage_status = CoverageStatus.NONE.value
                    row.searchable_page_count = 0
                    row.chunk_count = 0
                    row.error_code = RagErrorCode.DOCUMENT_INDEX_FAILED.value
                    row.updated_at = _now_utc()
                    recovered.append(int(row.document_version_id))
                session.flush()
                return tuple(sorted(recovered))
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.GENERATION_ACTIVATION_FAILED, error) from error
        finally:
            session.close()

    def retire_generation(
        self,
        identity: RagGenerationIdentity,
        *,
        expected_revision: int,
    ) -> GenerationBuild:
        """Retire one generation using the same identity/revision guard."""
        session = self._session_factory()
        try:
            with session.begin():
                row = self._require_generation(session, identity, expected_revision)
                row.status = GenerationStatus.RETIRED.value
                session.flush()
                return self._generation_from_row(row)
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.GENERATION_ACTIVATION_FAILED, error) from error
        finally:
            session.close()

    def bump_content_revision(
        self,
        identity: RagGenerationIdentity,
        *,
        expected_revision: int,
    ) -> RagGenerationIdentity:
        """Compare-and-swap a generation's corpus revision exactly once."""
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
            or expected_revision != identity.content_revision
        ):
            raise RagOperationalError(RagErrorCode.GENERATION_ACTIVATION_FAILED)
        session = self._session_factory()
        try:
            with session.begin():
                return self._bump_content_revision_in_session(
                    session, identity, expected_revision=expected_revision
                )
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.GENERATION_ACTIVATION_FAILED, error) from error
        finally:
            session.close()

    update_content_revision = bump_content_revision

    @staticmethod
    def _bump_content_revision_in_session(
        session: Session,
        identity: RagGenerationIdentity,
        *,
        expected_revision: int,
    ) -> RagGenerationIdentity:
        if (
            not isinstance(identity, RagGenerationIdentity)
            or isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
            or expected_revision != identity.content_revision
        ):
            raise RagOperationalError(RagErrorCode.GENERATION_ACTIVATION_FAILED)
        result = cast(
            CursorResult[Any],
            session.execute(
                update(RagIndexGenerationORM)
                .where(
                    RagIndexGenerationORM.id == identity.generation_id,
                    RagIndexGenerationORM.config_fingerprint == identity.config_fingerprint,
                    RagIndexGenerationORM.embedding_dimensions == identity.embedding_dimensions,
                    RagIndexGenerationORM.content_revision == expected_revision,
                )
                .values(content_revision=RagIndexGenerationORM.content_revision + 1)
            ),
        )
        if result.rowcount != 1:
            raise RagOperationalError(RagErrorCode.GENERATION_ACTIVATION_FAILED)
        return RagGenerationIdentity(
            generation_id=identity.generation_id,
            config_fingerprint=identity.config_fingerprint,
            content_revision=expected_revision + 1,
            embedding_dimensions=identity.embedding_dimensions,
        )

    def read_structure_units(
        self,
        document_version_id: int,
    ) -> tuple[DocumentStructureUnitRecord, ...]:
        """Read ordered structural provenance for one eligible STRUCTURE version."""

        if (
            isinstance(document_version_id, bool)
            or not isinstance(document_version_id, int)
            or document_version_id <= 0
        ):
            raise RagOperationalError(RagErrorCode.PROVENANCE_RESOLUTION_FAILED)

        session = self._session_factory()
        try:
            with session.begin():
                version_row = session.execute(
                    self._version_query().where(DocumentVersionORM.id == document_version_id)
                ).first()

                if version_row is None:
                    raise RagOperationalError(RagErrorCode.PROVENANCE_RESOLUTION_FAILED)

                version = self._record_version(version_row)
                if version.provenance_mode != "STRUCTURE":
                    raise RagOperationalError(RagErrorCode.PROVENANCE_RESOLUTION_FAILED)

                rows = session.scalars(
                    select(DocumentStructureUnitORM)
                    .where(DocumentStructureUnitORM.document_version_id == document_version_id)
                    .order_by(
                        DocumentStructureUnitORM.ordinal,
                        DocumentStructureUnitORM.id,
                    )
                ).all()

                return tuple(
                    DocumentStructureUnitRecord(
                        document_structure_unit_id=int(row.id),
                        document_version_id=int(row.document_version_id),
                        ordinal=int(row.ordinal),
                        kind=str(row.kind),
                        text=row.text,
                        heading_level=(
                            None if row.heading_level is None else int(row.heading_level)
                        ),
                        table_index=(None if row.table_index is None else int(row.table_index)),
                        row_index=(None if row.row_index is None else int(row.row_index)),
                        column_index=(None if row.column_index is None else int(row.column_index)),
                    )
                    for row in rows
                )
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(
                RagErrorCode.PROVENANCE_RESOLUTION_FAILED,
                error,
            ) from error
        finally:
            session.close()

    def _require_eligible_version(
        self, session: Session, document_version_id: int
    ) -> DocumentVersionRecord:
        if (
            isinstance(document_version_id, bool)
            or not isinstance(document_version_id, int)
            or document_version_id <= 0
        ):
            raise RagOperationalError(RagErrorCode.NO_ELIGIBLE_DOCUMENTS)
        row = session.execute(
            self._version_query().where(DocumentVersionORM.id == document_version_id)
        ).first()
        if row is None:
            raise RagOperationalError(RagErrorCode.NO_ELIGIBLE_DOCUMENTS)
        return self._record_version(row)

    @staticmethod
    def _generation_chunk_key(
        session: Session, identity: RagGenerationIdentity, chunk_key: str
    ) -> str:
        """Namespace a deterministic chunk key when another generation owns it."""
        owner = session.scalar(
            select(RagChunkORM.id).where(
                RagChunkORM.chunk_key == chunk_key,
                RagChunkORM.generation_id != identity.generation_id,
            )
        )
        if owner is None:
            return chunk_key
        return sha256(
            f"generation:{identity.generation_id}:{chunk_key}".encode("ascii")
        ).hexdigest()

    def get_eligible_document_version(self, document_version_id: int) -> DocumentVersionRecord:
        """Return one eligible immutable Phase 3 version without mutating it."""
        session = self._session_factory()
        try:
            with session.begin():
                return self._require_eligible_version(session, document_version_id)
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.PROVENANCE_RESOLUTION_FAILED, error) from error
        finally:
            session.close()

    @staticmethod
    def _record_index(row: RagDocumentIndexORM) -> DocumentIndexRecord:
        return DocumentIndexRecord(
            document_index_id=int(row.id),
            generation_id=int(row.generation_id),
            document_version_id=int(row.document_version_id),
            status=_as_index_status(row.status),
            coverage_status=_as_coverage_status(row.coverage_status),
            searchable_page_count=int(row.searchable_page_count),
            chunk_count=int(row.chunk_count),
            error_code=row.error_code,
        )

    def insert_document_index(
        self,
        identity: RagGenerationIdentity,
        *,
        expected_revision: int,
        document_version_id: int,
        status: DocumentIndexStatus,
        coverage_status: CoverageStatus,
        searchable_page_count: int,
        chunk_count: int,
        error_code: str | None = None,
    ) -> DocumentIndexRecord:
        """Persist one document-index state under a guarded generation revision."""
        if not isinstance(status, DocumentIndexStatus) or not isinstance(
            coverage_status, CoverageStatus
        ):
            raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
        if (
            isinstance(searchable_page_count, bool)
            or not isinstance(searchable_page_count, int)
            or searchable_page_count < 0
            or isinstance(chunk_count, bool)
            or not isinstance(chunk_count, int)
            or chunk_count < 0
        ):
            raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
        session = self._session_factory()
        try:
            with session.begin():
                self._require_generation(session, identity, expected_revision)
                self._require_eligible_version(session, document_version_id)
                row = RagDocumentIndexORM(
                    generation_id=identity.generation_id,
                    document_version_id=document_version_id,
                    status=status.value,
                    coverage_status=coverage_status.value,
                    searchable_page_count=searchable_page_count,
                    chunk_count=chunk_count,
                    error_code=error_code,
                    created_at=_now_utc(),
                    updated_at=_now_utc(),
                )
                session.add(row)
                session.flush()
                return self._record_index(row)
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.DOCUMENT_INDEX_FAILED, error) from error
        finally:
            session.close()

    @staticmethod
    def _delete_document_chunks(
        session: Session, *, generation_id: int, document_version_id: int
    ) -> None:
        """Remove only retryable derived rows owned by one generation/document."""
        chunk_ids = tuple(
            session.scalars(
                select(RagChunkORM.id).where(
                    RagChunkORM.generation_id == generation_id,
                    RagChunkORM.document_version_id == document_version_id,
                )
            ).all()
        )
        for chunk_id in chunk_ids:
            delete_fts_row(session, chunk_id=int(chunk_id))
        if chunk_ids:
            session.execute(
                delete(RagChunkSourceSpanORM).where(
                    RagChunkSourceSpanORM.rag_chunk_id.in_(chunk_ids)
                )
            )
            session.execute(
                delete(RagChunkStructureSourceSpanORM).where(
                    RagChunkStructureSourceSpanORM.rag_chunk_id.in_(chunk_ids)
                )
            )
            session.execute(delete(RagChunkORM).where(RagChunkORM.id.in_(chunk_ids)))

    def begin_document_preparation(
        self,
        identity: RagGenerationIdentity,
        *,
        expected_revision: int,
        document_version_id: int,
    ) -> DocumentIndexRecord:
        """Create or reset one retryable PREPARING document state and its derived rows."""
        session = self._session_factory()
        try:
            with session.begin():
                generation = self._require_generation(session, identity, expected_revision)
                if generation.status != GenerationStatus.BUILDING.value:
                    raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
                self._require_eligible_version(session, document_version_id)
                row = session.scalar(
                    select(RagDocumentIndexORM).where(
                        RagDocumentIndexORM.generation_id == identity.generation_id,
                        RagDocumentIndexORM.document_version_id == document_version_id,
                    )
                )
                if row is None:
                    row = RagDocumentIndexORM(
                        generation_id=identity.generation_id,
                        document_version_id=document_version_id,
                        status=DocumentIndexStatus.PREPARING.value,
                        coverage_status=CoverageStatus.NONE.value,
                        searchable_page_count=0,
                        chunk_count=0,
                        error_code=None,
                        created_at=_now_utc(),
                        updated_at=_now_utc(),
                    )
                    session.add(row)
                else:
                    self._delete_document_chunks(
                        session,
                        generation_id=identity.generation_id,
                        document_version_id=document_version_id,
                    )
                    row.status = DocumentIndexStatus.PREPARING.value
                    row.coverage_status = CoverageStatus.NONE.value
                    row.searchable_page_count = 0
                    row.chunk_count = 0
                    row.error_code = None
                    row.updated_at = _now_utc()
                session.flush()
                return self._record_index(row)
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.DOCUMENT_INDEX_FAILED, error) from error
        finally:
            session.close()

    def begin_document_staging(
        self,
        identity: RagGenerationIdentity,
        *,
        expected_revision: int,
        document_version_id: int,
    ) -> DocumentIndexRecord:
        """Create/reset retryable PREPARING state in the exact ACTIVE generation."""
        session = self._session_factory()
        try:
            with session.begin():
                generation = self._require_generation(session, identity, expected_revision)
                if generation.status != GenerationStatus.ACTIVE.value:
                    raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
                self._require_eligible_version(session, document_version_id)
                row = session.scalar(
                    select(RagDocumentIndexORM).where(
                        RagDocumentIndexORM.generation_id == identity.generation_id,
                        RagDocumentIndexORM.document_version_id == document_version_id,
                    )
                )
                if row is None:
                    row = RagDocumentIndexORM(
                        generation_id=identity.generation_id,
                        document_version_id=document_version_id,
                        status=DocumentIndexStatus.PREPARING.value,
                        coverage_status=CoverageStatus.NONE.value,
                        searchable_page_count=0,
                        chunk_count=0,
                        error_code=None,
                        created_at=_now_utc(),
                        updated_at=_now_utc(),
                    )
                    session.add(row)
                else:
                    self._delete_document_chunks(
                        session,
                        generation_id=identity.generation_id,
                        document_version_id=document_version_id,
                    )
                    row.status = DocumentIndexStatus.PREPARING.value
                    row.coverage_status = CoverageStatus.NONE.value
                    row.searchable_page_count = 0
                    row.chunk_count = 0
                    row.error_code = None
                    row.updated_at = _now_utc()
                session.flush()
                return self._record_index(row)
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.DOCUMENT_INDEX_FAILED, error) from error
        finally:
            session.close()

    def record_staged_document(
        self,
        identity: RagGenerationIdentity,
        *,
        expected_revision: int,
        document_version_id: int,
        intended_status: DocumentIndexStatus,
        coverage_status: CoverageStatus,
        searchable_page_count: int,
        chunk_count: int,
        verification: DocumentVerificationRecord | None,
    ) -> DocumentIndexRecord:
        """Validate and retain coherent PREPARING metadata for later publication."""
        if (
            isinstance(document_version_id, bool)
            or not isinstance(document_version_id, int)
            or document_version_id <= 0
            or not isinstance(coverage_status, CoverageStatus)
            or isinstance(searchable_page_count, bool)
            or not isinstance(searchable_page_count, int)
            or searchable_page_count < 0
            or isinstance(chunk_count, bool)
            or not isinstance(chunk_count, int)
            or chunk_count < 0
            or intended_status
            not in {
                DocumentIndexStatus.READY,
                DocumentIndexStatus.NOT_SEARCHABLE,
            }
        ):
            raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
        session = self._session_factory()
        try:
            with session.begin():
                generation = self._require_generation(session, identity, expected_revision)
                if generation.status != GenerationStatus.ACTIVE.value:
                    raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
                version = self._require_eligible_version(session, document_version_id)
                row = session.scalar(
                    select(RagDocumentIndexORM).where(
                        RagDocumentIndexORM.generation_id == identity.generation_id,
                        RagDocumentIndexORM.document_version_id == document_version_id,
                    )
                )
                if row is None or row.status != DocumentIndexStatus.PREPARING.value:
                    raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
                self._validate_staged_metadata(
                    session,
                    identity,
                    version,
                    intended_status=intended_status,
                    coverage_status=coverage_status,
                    searchable_page_count=searchable_page_count,
                    chunk_count=chunk_count,
                    verification=verification,
                )
                row.coverage_status = coverage_status.value
                row.searchable_page_count = searchable_page_count
                row.chunk_count = chunk_count
                row.error_code = None
                row.updated_at = _now_utc()
                session.flush()
                return self._record_index(row)
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.DOCUMENT_INDEX_FAILED, error) from error
        finally:
            session.close()

    def publish_staged_documents(
        self,
        identity: RagGenerationIdentity,
        *,
        expected_revision: int,
        staged_documents: tuple[StagedDocumentIndexResult, ...],
    ) -> RagGenerationIdentity:
        """Atomically publish a verified ACTIVE batch and its one revision CAS."""
        if (
            not staged_documents
            or any(not isinstance(item, StagedDocumentIndexResult) for item in staged_documents)
            or len({item.document_version_id for item in staged_documents}) != len(staged_documents)
        ):
            raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
        session = self._session_factory()
        try:
            with session.begin():
                generation = self._require_generation(session, identity, expected_revision)
                if generation.status != GenerationStatus.ACTIVE.value:
                    raise RagOperationalError(RagErrorCode.NO_ACTIVE_GENERATION)
                rows: list[RagDocumentIndexORM] = []
                for staged in staged_documents:
                    if staged.generation != identity:
                        raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
                    version = self._require_eligible_version(session, staged.document_version_id)
                    row = session.scalar(
                        select(RagDocumentIndexORM).where(
                            RagDocumentIndexORM.generation_id == identity.generation_id,
                            RagDocumentIndexORM.document_version_id == staged.document_version_id,
                        )
                    )
                    if (
                        row is None
                        or row.status != DocumentIndexStatus.PREPARING.value
                        or _as_coverage_status(row.coverage_status) is not staged.coverage_status
                        or int(row.searchable_page_count) != staged.searchable_page_count
                        or int(row.chunk_count) != staged.chunk_count
                        or row.error_code is not None
                    ):
                        raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
                    self._validate_staged_metadata(
                        session,
                        identity,
                        version,
                        intended_status=staged.intended_status,
                        coverage_status=staged.coverage_status,
                        searchable_page_count=staged.searchable_page_count,
                        chunk_count=staged.chunk_count,
                        verification=staged.verification,
                    )
                    rows.append(row)
                for row, staged in zip(rows, staged_documents, strict=True):
                    row.status = staged.intended_status.value
                    row.updated_at = _now_utc()
                revised = self._bump_content_revision_in_session(
                    session, identity, expected_revision=expected_revision
                )
                session.flush()
                return revised
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.DOCUMENT_INDEX_FAILED, error) from error
        finally:
            session.close()

    @staticmethod
    def _ready_coverage_invalid(
        version: DocumentVersionRecord,
        coverage_status: CoverageStatus,
        searchable_page_count: int,
    ) -> bool:
        """Validate READY coverage without inventing STRUCTURE pages."""

        if coverage_status is CoverageStatus.NONE:
            return True

        if version.provenance_mode == "PAGE":
            page_count = version.page_count
            if page_count is None:
                return True
            return (
                searchable_page_count <= 0
                or searchable_page_count > page_count
                or (coverage_status is CoverageStatus.FULL and searchable_page_count != page_count)
                or (
                    coverage_status is CoverageStatus.PARTIAL
                    and searchable_page_count >= page_count
                )
            )

        if version.provenance_mode == "STRUCTURE":
            return version.page_count is not None or searchable_page_count != 0

        return True

    def _validate_staged_metadata(
        self,
        session: Session,
        identity: RagGenerationIdentity,
        version: DocumentVersionRecord,
        *,
        intended_status: DocumentIndexStatus,
        coverage_status: CoverageStatus,
        searchable_page_count: int,
        chunk_count: int,
        verification: DocumentVerificationRecord | None,
    ) -> None:
        if intended_status is DocumentIndexStatus.NOT_SEARCHABLE:
            if (
                coverage_status is not CoverageStatus.NONE
                or searchable_page_count != 0
                or chunk_count != 0
                or verification is not None
            ):
                raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
            return
        if (
            self._ready_coverage_invalid(
                version,
                coverage_status,
                searchable_page_count,
            )
            or chunk_count <= 0
            or verification is None
            or verification.generation_id != identity.generation_id
            or verification.document_version_id != version.document_version_id
            or verification.chunk_count != chunk_count
            or self._verify_generation_document_in_session(
                session, identity, version.document_version_id
            )
            != verification
        ):
            raise RagOperationalError(RagErrorCode.INDEX_INTEGRITY_FAILED)

    def finish_document_preparation(
        self,
        identity: RagGenerationIdentity,
        *,
        expected_revision: int,
        document_version_id: int,
        status: DocumentIndexStatus,
        coverage_status: CoverageStatus,
        searchable_page_count: int,
        chunk_count: int,
        error_code: RagErrorCode | None = None,
        verification: DocumentVerificationRecord | None = None,
    ) -> DocumentIndexRecord:
        """Publish a verified state or safely record an invisible failed state."""
        if status not in {
            DocumentIndexStatus.READY,
            DocumentIndexStatus.NOT_SEARCHABLE,
            DocumentIndexStatus.FAILED,
        } or not isinstance(coverage_status, CoverageStatus):
            raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
        if (
            isinstance(searchable_page_count, bool)
            or not isinstance(searchable_page_count, int)
            or searchable_page_count < 0
            or isinstance(chunk_count, bool)
            or not isinstance(chunk_count, int)
            or chunk_count < 0
        ):
            raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
        session = self._session_factory()
        try:
            with session.begin():
                self._require_generation(session, identity, expected_revision)
                version = self._require_eligible_version(session, document_version_id)
                row = session.scalar(
                    select(RagDocumentIndexORM).where(
                        RagDocumentIndexORM.generation_id == identity.generation_id,
                        RagDocumentIndexORM.document_version_id == document_version_id,
                    )
                )
                if row is None or row.status != DocumentIndexStatus.PREPARING.value:
                    raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
                if status is DocumentIndexStatus.FAILED and (
                    coverage_status is not CoverageStatus.NONE
                    or searchable_page_count != 0
                    or chunk_count != 0
                    or error_code is None
                    or verification is not None
                ):
                    raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
                if status is DocumentIndexStatus.FAILED:
                    self._delete_document_chunks(
                        session,
                        generation_id=identity.generation_id,
                        document_version_id=document_version_id,
                    )
                    coverage_status = CoverageStatus.NONE
                    searchable_page_count = 0
                    chunk_count = 0
                if status is DocumentIndexStatus.NOT_SEARCHABLE and (
                    coverage_status is not CoverageStatus.NONE
                    or searchable_page_count != 0
                    or chunk_count != 0
                    or error_code is not None
                    or verification is not None
                ):
                    raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
                if status is DocumentIndexStatus.READY and (
                    self._ready_coverage_invalid(
                        version,
                        coverage_status,
                        searchable_page_count,
                    )
                    or chunk_count <= 0
                    or error_code is not None
                    or verification is None
                    or verification.generation_id != identity.generation_id
                    or verification.document_version_id != document_version_id
                    or verification.chunk_count != chunk_count
                ):
                    raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
                if status is DocumentIndexStatus.READY:
                    current = self._verify_generation_document_in_session(
                        session, identity, document_version_id
                    )
                    if current != verification:
                        raise RagOperationalError(RagErrorCode.INDEX_INTEGRITY_FAILED)
                row.status = status.value
                row.coverage_status = coverage_status.value
                row.searchable_page_count = searchable_page_count
                row.chunk_count = chunk_count
                row.error_code = error_code.value if error_code is not None else None
                row.updated_at = _now_utc()
                session.flush()
                return self._record_index(row)
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.DOCUMENT_INDEX_FAILED, error) from error
        finally:
            session.close()

    def verify_generation_document(
        self, identity: RagGenerationIdentity, document_version_id: int
    ) -> DocumentVerificationRecord:
        """Verify chunk, span, and FTS membership before a row may become READY."""
        session = self._session_factory()
        try:
            with session.begin():
                return self._verify_generation_document_in_session(
                    session, identity, document_version_id
                )
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.INDEX_INTEGRITY_FAILED, error) from error
        finally:
            session.close()

    def _verify_generation_document_in_session(
        self, session: Session, identity: RagGenerationIdentity, document_version_id: int
    ) -> DocumentVerificationRecord:
        """Verify exact current SQLite/FTS membership inside a caller transaction."""
        self._require_generation(session, identity, identity.content_revision)
        index = session.scalar(
            select(RagDocumentIndexORM).where(
                RagDocumentIndexORM.generation_id == identity.generation_id,
                RagDocumentIndexORM.document_version_id == document_version_id,
            )
        )
        chunks = tuple(
            session.scalars(
                select(RagChunkORM)
                .where(
                    RagChunkORM.generation_id == identity.generation_id,
                    RagChunkORM.document_version_id == document_version_id,
                )
                .order_by(RagChunkORM.ordinal, RagChunkORM.id)
            ).all()
        )
        chunk_ids = tuple(int(chunk.id) for chunk in chunks)
        fts_ids = tuple(
            int(row.rowid)
            for row in session.execute(
                text(
                    "SELECT rowid FROM rag_chunks_fts WHERE generation_id = :generation_id "
                    "AND document_version_id = :document_version_id ORDER BY rowid"
                ),
                {
                    "generation_id": identity.generation_id,
                    "document_version_id": document_version_id,
                },
            )
        )
        page_spans = (
            tuple(
                session.execute(
                    select(
                        RagChunkSourceSpanORM,
                        DocumentPageORM,
                        RagChunkORM,
                    )
                    .join(
                        DocumentPageORM,
                        RagChunkSourceSpanORM.document_page_id == DocumentPageORM.id,
                    )
                    .join(
                        RagChunkORM,
                        RagChunkSourceSpanORM.rag_chunk_id == RagChunkORM.id,
                    )
                    .where(RagChunkSourceSpanORM.rag_chunk_id.in_(chunk_ids))
                    .order_by(
                        RagChunkSourceSpanORM.rag_chunk_id,
                        RagChunkSourceSpanORM.ordinal,
                    )
                )
            )
            if chunk_ids
            else ()
        )

        structure_spans = (
            tuple(
                session.execute(
                    select(
                        RagChunkStructureSourceSpanORM,
                        DocumentStructureUnitORM,
                        RagChunkORM,
                    )
                    .join(
                        DocumentStructureUnitORM,
                        RagChunkStructureSourceSpanORM.document_structure_unit_id
                        == DocumentStructureUnitORM.id,
                    )
                    .join(
                        RagChunkORM,
                        RagChunkStructureSourceSpanORM.rag_chunk_id == RagChunkORM.id,
                    )
                    .where(RagChunkStructureSourceSpanORM.rag_chunk_id.in_(chunk_ids))
                    .order_by(
                        RagChunkStructureSourceSpanORM.rag_chunk_id,
                        RagChunkStructureSourceSpanORM.ordinal,
                    )
                )
            )
            if chunk_ids
            else ()
        )

        page_span_chunk_ids: set[int] = set()
        for span, page, chunk in page_spans:
            if not (
                chunk.provenance_mode == "PAGE"
                and span.document_page_id == page.id
                and page.document_version_id == chunk.document_version_id
                and page.page_number == span.page_number
                and 0 <= span.page_char_start < span.page_char_end <= len(page.text)
                and 0 <= span.chunk_char_start < span.chunk_char_end <= len(chunk.exact_text)
                and page.text[span.page_char_start : span.page_char_end]
                == chunk.exact_text[span.chunk_char_start : span.chunk_char_end]
            ):
                raise RagOperationalError(RagErrorCode.INDEX_INTEGRITY_FAILED)

            page_span_chunk_ids.add(int(chunk.id))

        structure_span_chunk_ids: set[int] = set()
        for span, unit, chunk in structure_spans:
            if not (
                chunk.provenance_mode == "STRUCTURE"
                and span.document_structure_unit_id == unit.id
                and unit.document_version_id == chunk.document_version_id
                and 0 <= span.source_char_start < span.source_char_end <= len(unit.text)
                and 0 <= span.chunk_char_start < span.chunk_char_end <= len(chunk.exact_text)
                and unit.text[span.source_char_start : span.source_char_end]
                == chunk.exact_text[span.chunk_char_start : span.chunk_char_end]
            ):
                raise RagOperationalError(RagErrorCode.INDEX_INTEGRITY_FAILED)

            structure_span_chunk_ids.add(int(chunk.id))

        valid_span_ids: set[int] = set()

        for chunk in chunks:
            chunk_id = int(chunk.id)

            if chunk.provenance_mode == "PAGE":
                if (
                    chunk_id not in page_span_chunk_ids
                    or chunk_id in structure_span_chunk_ids
                    or chunk.page_start is None
                    or chunk.page_end is None
                ):
                    raise RagOperationalError(RagErrorCode.INDEX_INTEGRITY_FAILED)

            elif chunk.provenance_mode == "STRUCTURE":
                if (
                    chunk_id not in structure_span_chunk_ids
                    or chunk_id in page_span_chunk_ids
                    or chunk.page_start is not None
                    or chunk.page_end is not None
                ):
                    raise RagOperationalError(RagErrorCode.INDEX_INTEGRITY_FAILED)

            else:
                raise RagOperationalError(RagErrorCode.INDEX_INTEGRITY_FAILED)

            valid_span_ids.add(chunk_id)

        if (
            index is None
            or index.status
            not in {
                DocumentIndexStatus.PREPARING.value,
                DocumentIndexStatus.READY.value,
            }
            or not chunks
            or tuple(sorted(chunk_ids)) != fts_ids
            or valid_span_ids != set(chunk_ids)
            or len({chunk.chunk_key for chunk in chunks}) != len(chunks)
        ):
            raise RagOperationalError(RagErrorCode.INDEX_INTEGRITY_FAILED)

        return DocumentVerificationRecord(
            generation_id=identity.generation_id,
            document_version_id=document_version_id,
            chunk_keys=tuple(chunk.chunk_key for chunk in chunks),
            chunk_count=len(chunks),
            source_span_count=(len(page_spans) + len(structure_spans)),
            fts_row_count=len(fts_ids),
        )

    def get_document_index(
        self, identity: RagGenerationIdentity, document_version_id: int
    ) -> DocumentIndexRecord:
        """Read one document-index row for an exact generation identity."""
        session = self._session_factory()
        try:
            with session.begin():
                self._require_generation(session, identity, identity.content_revision)
                row = session.scalar(
                    select(RagDocumentIndexORM).where(
                        RagDocumentIndexORM.generation_id == identity.generation_id,
                        RagDocumentIndexORM.document_version_id == document_version_id,
                    )
                )
                if row is None:
                    raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
                if row.status not in {member.value for member in DocumentIndexStatus}:
                    raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
                return self._record_index(row)
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.DOCUMENT_INDEX_FAILED, error) from error
        finally:
            session.close()

    def list_document_indexes(
        self, identity: RagGenerationIdentity
    ) -> tuple[DocumentIndexRecord, ...]:
        """Read document-index rows in stable document-version order."""
        session = self._session_factory()
        try:
            with session.begin():
                self._require_generation(session, identity, identity.content_revision)
                rows = session.scalars(
                    select(RagDocumentIndexORM)
                    .where(RagDocumentIndexORM.generation_id == identity.generation_id)
                    .order_by(RagDocumentIndexORM.document_version_id, RagDocumentIndexORM.id)
                ).all()
                return tuple(self._record_index(row) for row in rows)
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.DOCUMENT_INDEX_FAILED, error) from error
        finally:
            session.close()

    @staticmethod
    def _record_chunk(row: RagChunkORM) -> ChunkRecord:
        return ChunkRecord(
            chunk_id=int(row.id),
            chunk_key=row.chunk_key,
            generation_id=int(row.generation_id),
            document_version_id=int(row.document_version_id),
            ordinal=int(row.ordinal),
            heading=row.heading,
            exact_text=row.exact_text,
            token_count=int(row.token_count),
            text_sha256=row.text_sha256,
            provenance_mode=str(row.provenance_mode),
            page_start=(None if row.page_start is None else int(row.page_start)),
            page_end=(None if row.page_end is None else int(row.page_end)),
        )

    def insert_chunk(
        self,
        identity: RagGenerationIdentity,
        *,
        expected_revision: int,
        document_version_id: int,
        chunk_key: str,
        ordinal: int,
        heading: str | None,
        exact_text: str,
        token_count: int,
        text_sha256: str,
        provenance_mode: str = "PAGE",
        page_start: int | None,
        page_end: int | None,
    ) -> ChunkRecord:
        """Persist one authoritative chunk under a guarded generation revision."""
        if any(
            not isinstance(value, str) or not value
            for value in (chunk_key, exact_text, text_sha256)
        ):
            raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
        session = self._session_factory()
        try:
            with session.begin():
                self._require_generation(session, identity, expected_revision)
                version = self._require_eligible_version(session, document_version_id)

                if (
                    provenance_mode not in {"PAGE", "STRUCTURE"}
                    or provenance_mode != version.provenance_mode
                ):
                    raise RagOperationalError(RagErrorCode.PROVENANCE_RESOLUTION_FAILED)

                if provenance_mode == "PAGE":
                    if (
                        version.page_count is None
                        or isinstance(page_start, bool)
                        or not isinstance(page_start, int)
                        or isinstance(page_end, bool)
                        or not isinstance(page_end, int)
                        or page_start <= 0
                        or page_end < page_start
                        or page_end > version.page_count
                    ):
                        raise RagOperationalError(RagErrorCode.PROVENANCE_RESOLUTION_FAILED)
                elif (
                    version.page_count is not None or page_start is not None or page_end is not None
                ):
                    raise RagOperationalError(RagErrorCode.PROVENANCE_RESOLUTION_FAILED)
                stored_chunk_key = self._generation_chunk_key(session, identity, chunk_key)
                row = RagChunkORM(
                    chunk_key=stored_chunk_key,
                    generation_id=identity.generation_id,
                    document_version_id=document_version_id,
                    ordinal=ordinal,
                    heading=heading,
                    exact_text=exact_text,
                    token_count=token_count,
                    text_sha256=text_sha256,
                    provenance_mode=provenance_mode,
                    page_start=page_start,
                    page_end=page_end,
                    created_at=_now_utc(),
                )
                session.add(row)
                session.flush()
                insert_fts_row(
                    session,
                    chunk_id=int(row.id),
                    exact_text=row.exact_text,
                    generation_id=int(row.generation_id),
                    document_version_id=int(row.document_version_id),
                )
                return self._record_chunk(row)
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.DOCUMENT_INDEX_FAILED, error) from error
        finally:
            session.close()

    def delete_chunk(
        self,
        identity: RagGenerationIdentity,
        rag_chunk_id: int,
        *,
        expected_revision: int,
    ) -> None:
        """Delete one authoritative chunk and its FTS row atomically."""
        if isinstance(rag_chunk_id, bool) or not isinstance(rag_chunk_id, int) or rag_chunk_id <= 0:
            raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
        session = self._session_factory()
        try:
            with session.begin():
                self._require_generation(session, identity, expected_revision)
                row = session.scalar(
                    select(RagChunkORM).where(
                        RagChunkORM.id == rag_chunk_id,
                        RagChunkORM.generation_id == identity.generation_id,
                    )
                )
                if row is None:
                    raise RagOperationalError(RagErrorCode.DOCUMENT_INDEX_FAILED)
                delete_fts_row(session, chunk_id=rag_chunk_id)
                session.delete(row)
                session.flush()
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.DOCUMENT_INDEX_FAILED, error) from error
        finally:
            session.close()

    def rebuild_fts(
        self,
        identity: RagGenerationIdentity,
        *,
        expected_revision: int,
    ) -> int:
        """Rebuild derived FTS rows for one generation transactionally."""
        session = self._session_factory()
        try:
            with session.begin():
                self._require_generation(session, identity, expected_revision)
                rows = tuple(
                    (
                        int(row.id),
                        row.exact_text,
                        int(row.document_version_id),
                    )
                    for row in session.scalars(
                        select(RagChunkORM)
                        .where(RagChunkORM.generation_id == identity.generation_id)
                        .order_by(RagChunkORM.id)
                    ).all()
                )
                return rebuild_fts_rows(
                    session,
                    generation_id=identity.generation_id,
                    rows=rows,
                )
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.FTS_INDEX_FAILED, error) from error
        finally:
            session.close()

    def list_chunks(
        self, identity: RagGenerationIdentity, document_version_id: int | None = None
    ) -> tuple[ChunkRecord, ...]:
        """Read chunks ordered by document version and ordinal."""
        session = self._session_factory()
        try:
            with session.begin():
                self._require_generation(session, identity, identity.content_revision)
                query = select(RagChunkORM).where(
                    RagChunkORM.generation_id == identity.generation_id
                )
                if document_version_id is not None:
                    query = query.where(RagChunkORM.document_version_id == document_version_id)
                rows = session.scalars(
                    query.order_by(
                        RagChunkORM.document_version_id, RagChunkORM.ordinal, RagChunkORM.id
                    )
                ).all()
                return tuple(self._record_chunk(row) for row in rows)
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.DOCUMENT_INDEX_FAILED, error) from error
        finally:
            session.close()

    def get_document_summary(self, key: SummaryCacheKey) -> StoredDocumentSummary | None:
        """Return the cached verified summary for exactly this identity, if any."""
        if not isinstance(key, SummaryCacheKey):
            raise RagOperationalError(RagErrorCode.INDEX_INTEGRITY_FAILED)
        session = self._session_factory()
        try:
            with session.begin():
                row = session.scalar(
                    select(RagDocumentSummaryORM).where(
                        RagDocumentSummaryORM.document_version_id == key.document_version_id,
                        RagDocumentSummaryORM.generation_id == key.generation_id,
                        RagDocumentSummaryORM.content_revision == key.content_revision,
                        RagDocumentSummaryORM.policy_version == key.policy_version,
                        RagDocumentSummaryORM.model_identifier == key.model_identifier,
                        RagDocumentSummaryORM.language == key.language,
                    )
                )
                if row is None:
                    return None
                try:
                    raw_points = json.loads(row.points_json)
                    points = tuple(
                        SummaryPoint(item["text"], tuple(item["chunk_keys"])) for item in raw_points
                    )
                    return StoredDocumentSummary(
                        key=key,
                        points=points,
                        sections_total=row.sections_total,
                        dropped_points=row.dropped_points,
                    )
                except (TypeError, ValueError, KeyError):
                    # A corrupt cache entry fails closed rather than being trusted.
                    raise RagOperationalError(RagErrorCode.INDEX_INTEGRITY_FAILED) from None
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.INDEX_INTEGRITY_FAILED, error) from error
        finally:
            session.close()

    def put_document_summary(self, summary: StoredDocumentSummary) -> None:
        """Cache a verified summary; an existing entry for the same identity wins."""
        if not isinstance(summary, StoredDocumentSummary):
            raise RagOperationalError(RagErrorCode.INDEX_INTEGRITY_FAILED)
        if self.get_document_summary(summary.key) is not None:
            return
        key = summary.key
        points_json = json.dumps(
            [
                {"text": point.text, "chunk_keys": list(point.chunk_keys)}
                for point in summary.points
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
        session = self._session_factory()
        try:
            with session.begin():
                session.add(
                    RagDocumentSummaryORM(
                        document_version_id=key.document_version_id,
                        generation_id=key.generation_id,
                        content_revision=key.content_revision,
                        policy_version=key.policy_version,
                        model_identifier=key.model_identifier,
                        language=key.language,
                        sections_total=summary.sections_total,
                        dropped_points=summary.dropped_points,
                        points_json=points_json,
                        created_at=_now_utc(),
                    )
                )
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.INDEX_INTEGRITY_FAILED, error) from error
        finally:
            session.close()

    def lexical_search(
        self,
        query: str,
        snapshot: QuerySnapshot,
        *,
        limit: int = 30,
    ) -> LexicalSearchResult:
        """Return BM25 candidates restricted to an authorized query snapshot."""
        if not isinstance(snapshot, QuerySnapshot):
            raise RagOperationalError(RagErrorCode.LEXICAL_INDEX_FAILED)
        session = self._session_factory()
        try:
            with session.begin():
                self._resolve_snapshot_generation(session, snapshot)
                return search_fts(session, query, snapshot, limit=limit)
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.LEXICAL_INDEX_FAILED, error) from error
        finally:
            session.close()

    def materialize_snapshot_chunks(
        self,
        snapshot: QuerySnapshot,
        chunk_keys: tuple[str, ...],
    ) -> tuple[AuthoritativeRetrievalChunk, ...]:
        """Materialize requested chunks from one durable snapshot read.

        The snapshot's generation identity and authorized version IDs are the
        complete authorization boundary.  Current revision, ACTIVE status, and
        current-version resolution are intentionally not consulted here.
        """
        if not isinstance(snapshot, QuerySnapshot) or not isinstance(chunk_keys, tuple):
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
        if any(not isinstance(key, str) or not key.strip() for key in chunk_keys):
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
        if len(set(chunk_keys)) != len(chunk_keys):
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
        if not chunk_keys:
            return ()

        session = self._session_factory()
        try:
            with session.begin():
                self._require_snapshot_generation(session, snapshot)
                generation_id = snapshot.generation.generation_id
                authorized_versions = set(snapshot.document_version_ids)
                requested_keys = set(chunk_keys)

                requested_chunks = tuple(
                    session.scalars(
                        select(RagChunkORM).where(
                            RagChunkORM.generation_id == generation_id,
                            RagChunkORM.chunk_key.in_(requested_keys),
                        )
                    ).all()
                )
                if len(requested_chunks) != len(chunk_keys):
                    raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
                requested_by_key = {chunk.chunk_key: chunk for chunk in requested_chunks}
                if set(requested_by_key) != requested_keys:
                    raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
                if any(
                    int(chunk.generation_id) != generation_id
                    or int(chunk.document_version_id) not in authorized_versions
                    for chunk in requested_chunks
                ):
                    raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)

                requested_version_ids = {
                    int(chunk.document_version_id) for chunk in requested_chunks
                }
                all_chunks = tuple(
                    session.scalars(
                        select(RagChunkORM)
                        .where(
                            RagChunkORM.generation_id == generation_id,
                            RagChunkORM.document_version_id.in_(requested_version_ids),
                        )
                        .order_by(
                            RagChunkORM.document_version_id, RagChunkORM.ordinal, RagChunkORM.id
                        )
                    ).all()
                )
                if not all_chunks or len({chunk.chunk_key for chunk in all_chunks}) != len(
                    all_chunks
                ):
                    raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
                by_key = {chunk.chunk_key: chunk for chunk in all_chunks}
                if any(requested_by_key[key].id != by_key[key].id for key in requested_keys):
                    raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)

                chunks_by_version: dict[int, tuple[RagChunkORM, ...]] = {}
                for document_version_id in requested_version_ids:
                    chunks_by_version[document_version_id] = tuple(
                        chunk
                        for chunk in all_chunks
                        if int(chunk.document_version_id) == document_version_id
                    )
                indexes = tuple(
                    session.scalars(
                        select(RagDocumentIndexORM).where(
                            RagDocumentIndexORM.generation_id == generation_id,
                            RagDocumentIndexORM.document_version_id.in_(authorized_versions),
                        )
                    ).all()
                )
                indexes_by_version = {int(index.document_version_id): index for index in indexes}
                for document_version_id, chunks in chunks_by_version.items():
                    index = indexes_by_version.get(document_version_id)
                    if (
                        index is None
                        or index.status != DocumentIndexStatus.READY.value
                        or int(index.chunk_count) != len(chunks)
                        or not chunks
                    ):
                        raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)

                chunk_ids = tuple(int(chunk.id) for chunk in all_chunks)
                version_ids = tuple(sorted(requested_version_ids))
                placeholders = ", ".join(
                    f":version_id_{index}" for index in range(len(version_ids))
                )
                fts_rows = tuple(
                    session.execute(
                        text(
                            "SELECT rowid, generation_id, document_version_id "
                            "FROM rag_chunks_fts WHERE generation_id = :generation_id "
                            "AND document_version_id IN ("
                            f"{placeholders})"
                        ),
                        {
                            "generation_id": generation_id,
                            **{
                                f"version_id_{index}": version_id
                                for index, version_id in enumerate(version_ids)
                            },
                        },
                    ).all()
                )
                if len(fts_rows) != len(all_chunks) or {int(row.rowid) for row in fts_rows} != set(
                    chunk_ids
                ):
                    raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
                chunks_by_id = {int(chunk.id): chunk for chunk in all_chunks}
                for row in fts_rows:
                    chunk = chunks_by_id.get(int(row.rowid))
                    if chunk is None:
                        raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
                    if int(row.generation_id) != generation_id or int(
                        row.document_version_id
                    ) != int(chunk.document_version_id):
                        raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)

                page_span_rows = tuple(
                    session.execute(
                        select(
                            RagChunkSourceSpanORM,
                            DocumentPageORM,
                            RagChunkORM,
                        )
                        .join(
                            DocumentPageORM,
                            RagChunkSourceSpanORM.document_page_id == DocumentPageORM.id,
                        )
                        .join(
                            RagChunkORM,
                            RagChunkSourceSpanORM.rag_chunk_id == RagChunkORM.id,
                        )
                        .where(RagChunkSourceSpanORM.rag_chunk_id.in_(chunk_ids))
                        .order_by(
                            RagChunkSourceSpanORM.rag_chunk_id,
                            RagChunkSourceSpanORM.ordinal,
                        )
                    )
                )

                structure_span_rows = tuple(
                    session.execute(
                        select(
                            RagChunkStructureSourceSpanORM,
                            DocumentStructureUnitORM,
                            RagChunkORM,
                        )
                        .join(
                            DocumentStructureUnitORM,
                            RagChunkStructureSourceSpanORM.document_structure_unit_id
                            == DocumentStructureUnitORM.id,
                        )
                        .join(
                            RagChunkORM,
                            RagChunkStructureSourceSpanORM.rag_chunk_id == RagChunkORM.id,
                        )
                        .where(RagChunkStructureSourceSpanORM.rag_chunk_id.in_(chunk_ids))
                        .order_by(
                            RagChunkStructureSourceSpanORM.rag_chunk_id,
                            RagChunkStructureSourceSpanORM.ordinal,
                        )
                    )
                )

                spans_by_chunk: dict[
                    int,
                    list[SourceSpanRecord | StructureSourceSpanRecord],
                ] = {}

                for span, page, chunk in page_span_rows:
                    if (
                        chunk.provenance_mode != "PAGE"
                        or int(span.rag_chunk_id) != int(chunk.id)
                        or int(span.document_page_id) != int(page.id)
                        or int(chunk.generation_id) != generation_id
                        or int(page.document_version_id) != int(chunk.document_version_id)
                        or int(page.page_number) != int(span.page_number)
                        or not (
                            0
                            <= int(span.page_char_start)
                            < int(span.page_char_end)
                            <= len(page.text)
                        )
                        or not (
                            0
                            <= int(span.chunk_char_start)
                            < int(span.chunk_char_end)
                            <= len(chunk.exact_text)
                        )
                        or page.text[int(span.page_char_start) : int(span.page_char_end)]
                        != chunk.exact_text[int(span.chunk_char_start) : int(span.chunk_char_end)]
                    ):
                        raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)

                    spans_by_chunk.setdefault(
                        int(chunk.id),
                        [],
                    ).append(self._record_span(span))

                for span, unit, chunk in structure_span_rows:
                    if (
                        chunk.provenance_mode != "STRUCTURE"
                        or int(span.rag_chunk_id) != int(chunk.id)
                        or int(span.document_structure_unit_id) != int(unit.id)
                        or int(chunk.generation_id) != generation_id
                        or int(unit.document_version_id) != int(chunk.document_version_id)
                        or not (
                            0
                            <= int(span.source_char_start)
                            < int(span.source_char_end)
                            <= len(unit.text)
                        )
                        or not (
                            0
                            <= int(span.chunk_char_start)
                            < int(span.chunk_char_end)
                            <= len(chunk.exact_text)
                        )
                        or unit.text[int(span.source_char_start) : int(span.source_char_end)]
                        != chunk.exact_text[int(span.chunk_char_start) : int(span.chunk_char_end)]
                    ):
                        raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)

                    spans_by_chunk.setdefault(
                        int(chunk.id),
                        [],
                    ).append(
                        self._record_structure_span(
                            span,
                            unit,
                        )
                    )

                for chunk in all_chunks:
                    spans = spans_by_chunk.get(
                        int(chunk.id),
                        [],
                    )

                    if not spans or tuple(span.ordinal for span in spans) != tuple(
                        range(
                            1,
                            len(spans) + 1,
                        )
                    ):
                        raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)

                    if chunk.provenance_mode == "PAGE":
                        if any(
                            not isinstance(
                                span,
                                SourceSpanRecord,
                            )
                            for span in spans
                        ):
                            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
                    elif chunk.provenance_mode == "STRUCTURE":
                        if any(
                            not isinstance(
                                span,
                                StructureSourceSpanRecord,
                            )
                            for span in spans
                        ):
                            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)
                    else:
                        raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)

                version_query = (
                    select(
                        DocumentVersionORM.id,
                        DocumentVersionORM.document_id,
                        RemoteFileORM.id.label("remote_file_id"),
                        CourseORM.id.label("course_id"),
                        DocumentVersionORM.version_number,
                        DocumentVersionORM.content_blob_id,
                        ContentBlobORM.sha256.label("content_sha256"),
                        DocumentVersionORM.source_filename,
                        DocumentVersionORM.provenance_mode,
                        DocumentVersionORM.page_count,
                    )
                    .join(DocumentORM, DocumentVersionORM.document_id == DocumentORM.id)
                    .join(ContentBlobORM, DocumentVersionORM.content_blob_id == ContentBlobORM.id)
                    .join(RemoteFileORM, DocumentORM.remote_file_id == RemoteFileORM.id)
                    .join(CourseORM, RemoteFileORM.course_id == CourseORM.id)
                    .where(DocumentVersionORM.id.in_(version_ids))
                )
                version_rows = session.execute(version_query).all()
                versions = {int(row.id): self._record_version(row) for row in version_rows}
                if set(versions) != set(version_ids):
                    raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY)

                return tuple(
                    AuthoritativeRetrievalChunk(
                        chunk=self._record_chunk(requested_by_key[key]),
                        document_version=versions[int(requested_by_key[key].document_version_id)],
                        source_spans=tuple(spans_by_chunk[int(requested_by_key[key].id)]),
                    )
                    for key in chunk_keys
                )
        except RagOperationalError:
            raise
        except Exception:
            raise RagOperationalError(RagErrorCode.RETRIEVAL_INCONSISTENCY) from None
        finally:
            session.close()

    @staticmethod
    def _record_structure_span(
        row: RagChunkStructureSourceSpanORM,
        unit: DocumentStructureUnitORM,
    ) -> StructureSourceSpanRecord:
        return StructureSourceSpanRecord(
            source_span_id=int(row.id),
            rag_chunk_id=int(row.rag_chunk_id),
            document_structure_unit_id=int(row.document_structure_unit_id),
            ordinal=int(row.ordinal),
            unit_ordinal=int(unit.ordinal),
            kind=str(unit.kind),
            heading_level=(int(unit.heading_level) if unit.heading_level is not None else None),
            table_index=(int(unit.table_index) if unit.table_index is not None else None),
            row_index=(int(unit.row_index) if unit.row_index is not None else None),
            column_index=(int(unit.column_index) if unit.column_index is not None else None),
            source_char_start=int(row.source_char_start),
            source_char_end=int(row.source_char_end),
            chunk_char_start=int(row.chunk_char_start),
            chunk_char_end=int(row.chunk_char_end),
        )

    def insert_structure_source_span(
        self,
        identity: RagGenerationIdentity,
        *,
        expected_revision: int,
        rag_chunk_id: int,
        document_structure_unit_id: int,
        ordinal: int,
        source_char_start: int,
        source_char_end: int,
        chunk_char_start: int,
        chunk_char_end: int,
    ) -> None:
        """Persist one exact STRUCTURE provenance span."""

        values = (
            rag_chunk_id,
            document_structure_unit_id,
            ordinal,
            source_char_start,
            source_char_end,
            chunk_char_start,
            chunk_char_end,
        )

        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise RagOperationalError(RagErrorCode.PROVENANCE_RESOLUTION_FAILED)

        if (
            rag_chunk_id <= 0
            or document_structure_unit_id <= 0
            or ordinal <= 0
            or source_char_start < 0
            or source_char_end <= source_char_start
            or chunk_char_start < 0
            or chunk_char_end <= chunk_char_start
        ):
            raise RagOperationalError(RagErrorCode.PROVENANCE_RESOLUTION_FAILED)

        session = self._session_factory()
        try:
            with session.begin():
                self._require_generation(
                    session,
                    identity,
                    expected_revision,
                )

                chunk = session.scalar(
                    select(RagChunkORM).where(
                        RagChunkORM.id == rag_chunk_id,
                        RagChunkORM.generation_id == identity.generation_id,
                    )
                )
                unit = session.get(
                    DocumentStructureUnitORM,
                    document_structure_unit_id,
                )

                if (
                    chunk is None
                    or chunk.provenance_mode != "STRUCTURE"
                    or unit is None
                    or unit.document_version_id != chunk.document_version_id
                    or source_char_end > len(unit.text)
                    or chunk_char_end > len(chunk.exact_text)
                    or unit.text[source_char_start:source_char_end]
                    != chunk.exact_text[chunk_char_start:chunk_char_end]
                ):
                    raise RagOperationalError(RagErrorCode.PROVENANCE_RESOLUTION_FAILED)

                row = RagChunkStructureSourceSpanORM(
                    rag_chunk_id=rag_chunk_id,
                    document_structure_unit_id=(document_structure_unit_id),
                    ordinal=ordinal,
                    source_char_start=source_char_start,
                    source_char_end=source_char_end,
                    chunk_char_start=chunk_char_start,
                    chunk_char_end=chunk_char_end,
                )
                session.add(row)
                session.flush()

        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(
                RagErrorCode.DOCUMENT_INDEX_FAILED,
                error,
            ) from error
        finally:
            session.close()

    @staticmethod
    def _record_span(row: RagChunkSourceSpanORM) -> SourceSpanRecord:
        return SourceSpanRecord(
            source_span_id=int(row.id),
            rag_chunk_id=int(row.rag_chunk_id),
            document_page_id=int(row.document_page_id),
            ordinal=int(row.ordinal),
            page_number=int(row.page_number),
            page_char_start=int(row.page_char_start),
            page_char_end=int(row.page_char_end),
            chunk_char_start=int(row.chunk_char_start),
            chunk_char_end=int(row.chunk_char_end),
        )

    def insert_source_span(
        self,
        identity: RagGenerationIdentity,
        *,
        expected_revision: int,
        rag_chunk_id: int,
        document_page_id: int,
        ordinal: int,
        page_number: int,
        page_char_start: int,
        page_char_end: int,
        chunk_char_start: int,
        chunk_char_end: int,
    ) -> SourceSpanRecord:
        """Persist a span only when its chunk and page provenance agree exactly."""
        session = self._session_factory()
        try:
            with session.begin():
                self._require_generation(session, identity, expected_revision)
                chunk = session.scalar(
                    select(RagChunkORM).where(
                        RagChunkORM.id == rag_chunk_id,
                        RagChunkORM.generation_id == identity.generation_id,
                    )
                )
                page = session.get(DocumentPageORM, document_page_id)
                if (
                    chunk is None
                    or chunk.provenance_mode != "PAGE"
                    or page is None
                    or page.document_version_id != chunk.document_version_id
                ):
                    raise RagOperationalError(RagErrorCode.PROVENANCE_RESOLUTION_FAILED)
                if page.page_number != page_number:
                    raise RagOperationalError(RagErrorCode.PROVENANCE_RESOLUTION_FAILED)
                offsets = (
                    page_char_start,
                    page_char_end,
                    chunk_char_start,
                    chunk_char_end,
                )
                if (
                    any(isinstance(value, bool) or not isinstance(value, int) for value in offsets)
                    or page_char_start < 0
                    or page_char_end <= page_char_start
                    or page_char_end > len(page.text)
                    or chunk_char_start < 0
                    or chunk_char_end <= chunk_char_start
                    or chunk_char_end > len(chunk.exact_text)
                    or page.text[page_char_start:page_char_end]
                    != chunk.exact_text[chunk_char_start:chunk_char_end]
                ):
                    raise RagOperationalError(RagErrorCode.PROVENANCE_RESOLUTION_FAILED)
                row = RagChunkSourceSpanORM(
                    rag_chunk_id=rag_chunk_id,
                    document_page_id=document_page_id,
                    ordinal=ordinal,
                    page_number=page_number,
                    page_char_start=page_char_start,
                    page_char_end=page_char_end,
                    chunk_char_start=chunk_char_start,
                    chunk_char_end=chunk_char_end,
                )
                session.add(row)
                session.flush()
                return self._record_span(row)
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.PROVENANCE_RESOLUTION_FAILED, error) from error
        finally:
            session.close()

    def list_source_spans(
        self, identity: RagGenerationIdentity, rag_chunk_id: int
    ) -> tuple[SourceSpanRecord, ...]:
        """Read spans in exact ordinal order for one generation-owned chunk."""
        session = self._session_factory()
        try:
            with session.begin():
                self._require_generation(session, identity, identity.content_revision)
                rows = session.scalars(
                    select(RagChunkSourceSpanORM)
                    .join(RagChunkORM, RagChunkSourceSpanORM.rag_chunk_id == RagChunkORM.id)
                    .where(
                        RagChunkSourceSpanORM.rag_chunk_id == rag_chunk_id,
                        RagChunkORM.generation_id == identity.generation_id,
                    )
                    .order_by(RagChunkSourceSpanORM.ordinal, RagChunkSourceSpanORM.id)
                ).all()
                return tuple(self._record_span(row) for row in rows)
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.PROVENANCE_RESOLUTION_FAILED, error) from error
        finally:
            session.close()


__all__ = [
    "AuthoritativeRetrievalChunk",
    "ChunkRecord",
    "DocumentIndexRecord",
    "DocumentVerificationRecord",
    "DocumentPageRecord",
    "DocumentVersionRecord",
    "GenerationBuild",
    "QuerySnapshot",
    "RagRepository",
    "SourceSpanRecord",
]
