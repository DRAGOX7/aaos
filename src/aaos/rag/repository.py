"""Authoritative SQLite repository for Phase 4 derived RAG state.

The repository is the only layer that knows about the SQLAlchemy mappings.  It
returns immutable values and translates persistence failures into the fixed
RAG error contract.  Phase 3 rows are queried but never mutated here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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
    DocumentVersionORM,
)
from aaos.persistence.rag_models import (
    RagChunkORM,
    RagChunkSourceSpanORM,
    RagDocumentIndexORM,
    RagIndexGenerationORM,
)
from aaos.rag.contracts import (
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
    page_count: int

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
    page_start: int
    page_end: int


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
class DocumentVerificationRecord:
    """SQLite-side integrity evidence for one publishable document index."""

    generation_id: int
    document_version_id: int
    chunk_keys: tuple[str, ...]
    chunk_count: int
    source_span_count: int
    fts_row_count: int


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _safe_database_error(code: RagErrorCode, error: SQLAlchemyError) -> RagOperationalError:
    return RagOperationalError(code)


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
            page_count=int(row.page_count),
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

    def resolve_historical_versions(
        self, scope: HistoricalScope
    ) -> tuple[DocumentVersionRecord, ...]:
        """Resolve only explicitly requested eligible historical versions."""
        return self._resolve_versions(scope, current=False)

    def pin_query_snapshot(self, scope: RagScope) -> QuerySnapshot:
        """Pin the active generation and an immutable eligible version universe."""
        session = self._session_factory()
        try:
            with session.begin():
                generation = session.scalar(
                    select(RagIndexGenerationORM)
                    .where(RagIndexGenerationORM.status == GenerationStatus.ACTIVE.value)
                    .order_by(RagIndexGenerationORM.id)
                )
                if generation is None:
                    raise RagOperationalError(RagErrorCode.NO_ACTIVE_GENERATION)
                records = self._resolve_versions_in_session(
                    session,
                    scope,
                    current=not isinstance(scope, HistoricalScope),
                    ready_generation_id=int(generation.id),
                )
                return QuerySnapshot(
                    generation=self._generation_from_row(generation).identity,
                    scope=scope,
                    document_version_ids=tuple(item.document_version_id for item in records),
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

    def activate_generation(
        self,
        identity: RagGenerationIdentity,
        *,
        expected_revision: int,
    ) -> GenerationBuild:
        """Atomically retire the current active row and activate this BUILDING row."""
        session = self._session_factory()
        try:
            with session.begin():
                row = self._require_generation(session, identity, expected_revision)
                if row.status != GenerationStatus.BUILDING.value:
                    raise RagOperationalError(RagErrorCode.GENERATION_ACTIVATION_FAILED)
                session.execute(
                    update(RagIndexGenerationORM)
                    .where(RagIndexGenerationORM.status == GenerationStatus.ACTIVE.value)
                    .values(status=GenerationStatus.RETIRED.value)
                )
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
                result = cast(
                    CursorResult[Any],
                    session.execute(
                        update(RagIndexGenerationORM)
                        .where(
                            RagIndexGenerationORM.id == identity.generation_id,
                            RagIndexGenerationORM.config_fingerprint == identity.config_fingerprint,
                            RagIndexGenerationORM.embedding_dimensions
                            == identity.embedding_dimensions,
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
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.GENERATION_ACTIVATION_FAILED, error) from error
        finally:
            session.close()

    update_content_revision = bump_content_revision

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
                    coverage_status is CoverageStatus.NONE
                    or searchable_page_count <= 0
                    or searchable_page_count > version.page_count
                    or (
                        coverage_status is CoverageStatus.FULL
                        and searchable_page_count != version.page_count
                    )
                    or (
                        coverage_status is CoverageStatus.PARTIAL
                        and searchable_page_count >= version.page_count
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
        spans = (
            tuple(
                session.execute(
                    select(RagChunkSourceSpanORM, DocumentPageORM, RagChunkORM)
                    .join(
                        DocumentPageORM,
                        RagChunkSourceSpanORM.document_page_id == DocumentPageORM.id,
                    )
                    .join(RagChunkORM, RagChunkSourceSpanORM.rag_chunk_id == RagChunkORM.id)
                    .where(RagChunkSourceSpanORM.rag_chunk_id.in_(chunk_ids))
                    .order_by(RagChunkSourceSpanORM.rag_chunk_id, RagChunkSourceSpanORM.ordinal)
                )
            )
            if chunk_ids
            else ()
        )
        valid_span_ids: set[int] = set()
        for span, page, chunk in spans:
            if not (
                span.document_page_id == page.id
                and page.document_version_id == chunk.document_version_id
                and page.page_number == span.page_number
                and 0 <= span.page_char_start < span.page_char_end <= len(page.text)
                and 0 <= span.chunk_char_start < span.chunk_char_end <= len(chunk.exact_text)
                and page.text[span.page_char_start : span.page_char_end]
                == chunk.exact_text[span.chunk_char_start : span.chunk_char_end]
            ):
                raise RagOperationalError(RagErrorCode.INDEX_INTEGRITY_FAILED)
            valid_span_ids.add(int(chunk.id))
        if (
            index is None
            or index.status
            not in {DocumentIndexStatus.PREPARING.value, DocumentIndexStatus.READY.value}
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
            source_span_count=len(spans),
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
            page_start=int(row.page_start),
            page_end=int(row.page_end),
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
        page_start: int,
        page_end: int,
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
                    isinstance(page_start, bool)
                    or not isinstance(page_start, int)
                    or isinstance(page_end, bool)
                    or not isinstance(page_end, int)
                    or page_start <= 0
                    or page_end < page_start
                    or page_end > version.page_count
                ):
                    raise RagOperationalError(RagErrorCode.PROVENANCE_RESOLUTION_FAILED)
                row = RagChunkORM(
                    chunk_key=chunk_key,
                    generation_id=identity.generation_id,
                    document_version_id=document_version_id,
                    ordinal=ordinal,
                    heading=heading,
                    exact_text=exact_text,
                    token_count=token_count,
                    text_sha256=text_sha256,
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
                self._require_generation(
                    session,
                    snapshot.generation,
                    snapshot.generation.content_revision,
                )
                return search_fts(session, query, snapshot, limit=limit)
        except RagOperationalError:
            raise
        except SQLAlchemyError as error:
            raise _safe_database_error(RagErrorCode.LEXICAL_INDEX_FAILED, error) from error
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
