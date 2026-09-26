from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from aaos.documents.contracts import IngestionStatus
from aaos.persistence.academic_models import Base, CourseORM, RemoteFileORM
from aaos.persistence.database import create_database_engine, create_session_factory
from aaos.persistence.document_models import (
    ContentBlobORM,
    DocumentORM,
    DocumentVersionORM,
)
from aaos.persistence.rag_models import RagDocumentIndexORM, RagIndexGenerationORM
from aaos.web.contracts import (
    DocumentExtractionStatusView,
    DocumentIndexCoverageView,
    DocumentIndexReadinessView,
    DocumentIngestionStatusView,
)
from aaos.web.errors import Phase7ApiError

if TYPE_CHECKING:
    from aaos.web.documents import DocumentReadService

NOW = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    db_engine = create_database_engine(tmp_path / "phase7-documents.db")
    Base.metadata.create_all(db_engine)
    try:
        yield db_engine
    finally:
        db_engine.dispose()


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(engine)


def _seed_document(
    sessions: sessionmaker[Session],
    *,
    document_id: int,
    course_id: int = 1,
    course_active: bool = True,
    remote_active: bool = True,
    title: str | None = None,
    ingestion_status: IngestionStatus = IngestionStatus.READY,
    updated_at: datetime = NOW,
    versions: tuple[tuple[int, str, int], ...] = (),
    generations: tuple[tuple[int, str], ...] = (),
    indexes: tuple[tuple[int, int, str, str], ...] = (),
) -> None:
    with sessions() as session, session.begin():
        course = session.get(CourseORM, course_id)
        if course is None:
            course = CourseORM(
                id=course_id,
                source_system="test",
                source_id=f"course-{course_id}",
                title=f"Course {course_id}",
                is_active=course_active,
            )
            session.add(course)
            session.flush()
        else:
            course.is_active = course_active

        remote_id = 10_000 + document_id
        remote = RemoteFileORM(
            id=remote_id,
            course_id=course_id,
            source_system="test",
            source_id=f"remote-{document_id}",
            title=title or f"Document {document_id}",
            is_active=remote_active,
        )
        session.add(remote)
        session.flush()

        document = DocumentORM(
            id=document_id,
            remote_file_id=remote_id,
            ingestion_status=ingestion_status.value,
            created_at=NOW,
            updated_at=updated_at,
        )
        session.add(document)
        session.flush()

        version_ids: dict[int, int] = {}
        for version_number, extraction_status, page_count in versions:
            version_id = document_id * 100 + version_number
            blob_id = document_id * 1_000 + version_number
            digest = f"{version_id:064x}"[-64:]
            session.add(
                ContentBlobORM(
                    id=blob_id,
                    sha256=digest,
                    size_bytes=100,
                    media_type="application/pdf",
                    storage_key=f"sha256/{digest[:2]}/{digest}.pdf",
                    created_at=NOW,
                )
            )
            session.flush()
            session.add(
                DocumentVersionORM(
                    id=version_id,
                    document_id=document_id,
                    version_number=version_number,
                    content_blob_id=blob_id,
                    source_filename=f"document-{document_id}-{version_number}.pdf",
                    ingested_at=NOW,
                    extraction_status=extraction_status,
                    page_count=page_count,
                )
            )
            version_ids[version_number] = version_id
        session.flush()

        for generation_id, status in generations:
            if session.get(RagIndexGenerationORM, generation_id) is None:
                session.add(
                    RagIndexGenerationORM(
                        id=generation_id,
                        status=status,
                        config_fingerprint=f"{generation_id:064x}"[-64:],
                        chunker_version="test",
                        chunking_config="{}",
                        lexical_normalizer_version="test",
                        embedding_model="test",
                        embedding_revision="test",
                        embedding_dimensions=3,
                        retrieval_config="{}",
                        content_revision=0,
                        created_at=NOW,
                        activated_at=NOW if status == "ACTIVE" else None,
                    )
                )
        session.flush()

        for index_id, generation_id, version_number, status, coverage in (
            (document_id * 10_000 + position, generation_id, version_number, status, coverage)
            for position, (generation_id, version_number, status, coverage) in enumerate(indexes, 1)
        ):
            version_id = version_ids[version_number]
            session.add(
                RagDocumentIndexORM(
                    id=index_id,
                    generation_id=generation_id,
                    document_version_id=version_id,
                    status=status,
                    coverage_status=coverage,
                    searchable_page_count=1 if status == "READY" else 0,
                    chunk_count=1 if status == "READY" else 0,
                    error_code=None,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )


def _service(sessions: sessionmaker[Session]) -> DocumentReadService:
    from aaos.web.documents import DocumentReadService

    return DocumentReadService(sessions)


def test_list_documents_projects_document_without_latest_version(
    sessions: sessionmaker[Session],
) -> None:
    _seed_document(
        sessions,
        document_id=1,
        ingestion_status=IngestionStatus.NEVER_ATTEMPTED,
    )

    result = _service(sessions).list_documents()

    assert len(result) == 1
    assert result[0].extraction_status is None
    assert result[0].page_count is None
    assert result[0].index_readiness is None
    assert result[0].index_coverage is None
    assert result[0].ingestion_status is DocumentIngestionStatusView.NEVER_ATTEMPTED
    assert result[0].document_version_id is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("SUCCESS", DocumentExtractionStatusView.SUCCESS),
        ("PARTIAL", DocumentExtractionStatusView.PARTIAL),
        ("NO_TEXT", DocumentExtractionStatusView.NO_TEXT),
    ],
)
def test_latest_version_maps_exact_extraction_status(
    sessions: sessionmaker[Session],
    status: str,
    expected: DocumentExtractionStatusView,
) -> None:
    _seed_document(
        sessions,
        document_id=2,
        versions=((1, status, 3),),
    )

    result = _service(sessions).get_document(2)

    assert result.extraction_status is expected
    assert result.page_count == 3


def test_latest_version_wins_for_page_count_and_extraction(
    sessions: sessionmaker[Session],
) -> None:
    _seed_document(
        sessions,
        document_id=3,
        versions=((1, "SUCCESS", 2), (2, "PARTIAL", 7)),
    )

    result = _service(sessions).get_document(3)

    assert result.extraction_status is DocumentExtractionStatusView.PARTIAL
    assert result.page_count == 7
    assert result.document_version_id == 302


@pytest.mark.parametrize(
    "generations",
    [(), ((31, "RETIRED"),), ((32, "BUILDING"),)],
)
def test_missing_active_generation_is_projected_as_absent(
    sessions: sessionmaker[Session],
    generations: tuple[tuple[int, str], ...],
) -> None:
    _seed_document(
        sessions,
        document_id=4,
        versions=((1, "SUCCESS", 2),),
        generations=generations,
    )

    result = _service(sessions).get_document(4)

    assert result.index_readiness is None
    assert result.index_coverage is None


def test_active_generation_precedes_retired_and_building_generations(
    sessions: sessionmaker[Session],
) -> None:
    _seed_document(
        sessions,
        document_id=5,
        versions=((1, "SUCCESS", 2),),
        generations=((41, "RETIRED"), (42, "BUILDING"), (43, "ACTIVE")),
        indexes=(
            (41, 1, "FAILED", "NONE"),
            (42, 1, "PREPARING", "NONE"),
            (43, 1, "READY", "FULL"),
        ),
    )

    result = _service(sessions).get_document(5)

    assert result.index_readiness is DocumentIndexReadinessView.READY
    assert result.index_coverage is DocumentIndexCoverageView.FULL


@pytest.mark.parametrize(
    ("status", "coverage", "expected_readiness", "expected_coverage"),
    [
        ("PREPARING", "NONE", DocumentIndexReadinessView.PREPARING, None),
        ("FAILED", "NONE", DocumentIndexReadinessView.FAILED, None),
        (
            "NOT_SEARCHABLE",
            "NONE",
            DocumentIndexReadinessView.NOT_SEARCHABLE,
            DocumentIndexCoverageView.NONE,
        ),
        ("READY", "FULL", DocumentIndexReadinessView.READY, DocumentIndexCoverageView.FULL),
        (
            "READY",
            "PARTIAL",
            DocumentIndexReadinessView.READY,
            DocumentIndexCoverageView.PARTIAL,
        ),
    ],
)
def test_active_index_maps_readiness_and_coverage_exactly(
    sessions: sessionmaker[Session],
    status: str,
    coverage: str,
    expected_readiness: DocumentIndexReadinessView,
    expected_coverage: DocumentIndexCoverageView | None,
) -> None:
    _seed_document(
        sessions,
        document_id=6,
        versions=((1, "SUCCESS", 2),),
        generations=((61, "ACTIVE"),),
        indexes=((61, 1, status, coverage),),
    )

    result = _service(sessions).get_document(6)

    assert result.index_readiness is expected_readiness
    assert result.index_coverage is expected_coverage


def test_ready_with_none_coverage_fails_closed(
    sessions: sessionmaker[Session],
) -> None:
    _seed_document(
        sessions,
        document_id=7,
        versions=((1, "SUCCESS", 2),),
        generations=((71, "ACTIVE"),),
        indexes=((71, 1, "READY", "NONE"),),
    )

    with pytest.raises(Phase7ApiError) as raised:
        _service(sessions).get_document(7)

    assert raised.value.status_code == 500
    assert raised.value.code == "INTERNAL_ERROR"


def test_active_generation_without_latest_version_index_is_absent(
    sessions: sessionmaker[Session],
) -> None:
    _seed_document(
        sessions,
        document_id=8,
        versions=((1, "SUCCESS", 2), (2, "PARTIAL", 4)),
        generations=((81, "ACTIVE"),),
        indexes=((81, 1, "READY", "FULL"),),
    )

    result = _service(sessions).get_document(8)

    assert result.page_count == 4
    assert result.index_readiness is None
    assert result.index_coverage is None


def test_list_documents_orders_by_title_then_id_and_filters_inactive_parents(
    sessions: sessionmaker[Session],
) -> None:
    _seed_document(sessions, document_id=12, title="Zulu")
    _seed_document(sessions, document_id=10, title="alpha")
    _seed_document(sessions, document_id=11, title="Alpha")
    _seed_document(
        sessions, document_id=13, title="Hidden course", course_id=13, course_active=False
    )
    _seed_document(sessions, document_id=14, title="Hidden file", remote_active=False)

    result = _service(sessions).list_documents()

    assert [item.id for item in result] == [10, 11, 12]


def test_list_documents_filters_active_course_and_hides_missing_or_inactive_courses(
    sessions: sessionmaker[Session],
) -> None:
    _seed_document(sessions, document_id=20, course_id=20, title="Course 20")
    _seed_document(sessions, document_id=21, course_id=21, title="Course 21")
    _seed_document(sessions, document_id=22, course_id=22, course_active=False)

    result = _service(sessions).list_documents(course_id=20)
    assert [item.id for item in result] == [20]

    for course_id in (22, 404):
        with pytest.raises(Phase7ApiError) as raised:
            _service(sessions).list_documents(course_id)
        assert raised.value.status_code == 404
        assert raised.value.code == "COURSE_NOT_FOUND"


def test_document_projection_contains_only_safe_fields_and_document_updated_at(
    sessions: sessionmaker[Session],
) -> None:
    updated_at = datetime(2026, 9, 16, 9, 30, tzinfo=timezone.utc)
    _seed_document(
        sessions,
        document_id=30,
        title="Safe title",
        updated_at=updated_at,
        versions=((1, "SUCCESS", 3),),
    )

    rendered = _service(sessions).get_document(30).model_dump()

    assert set(rendered) == {
        "id",
        "course_id",
        "title",
        "ingestion_status",
        "extraction_status",
        "page_count",
        "index_readiness",
        "index_coverage",
        "updated_at",
    }
    assert rendered["updated_at"] == updated_at
    for forbidden in (
        "source_id",
        "source_system",
        "remote_url",
        "storage_key",
        "source_filename",
        "text",
        "provider",
        "path",
    ):
        assert forbidden not in rendered
