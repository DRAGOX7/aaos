"""Database-enforced invariants for Phase 3 document mappings."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from aaos.documents.contracts import (
    BlobInstall,
    DocumentIngestionFailure,
    ExtractedPage,
    ExtractedStructureUnit,
    ExtractionStatus,
    IngestionErrorCode,
    IngestionMode,
    IngestionStatus,
    ParsedDocx,
    ParsedPdf,
    RemoteFileSelection,
    StructureUnitKind,
    TextSource,
)
from aaos.documents.repository import DocumentRepository
from aaos.persistence.academic_models import CourseORM, RemoteFileORM
from aaos.persistence.database import create_database_engine, create_session_factory
from aaos.persistence.document_models import (
    ContentBlobORM,
    DocumentORM,
    DocumentPageORM,
    DocumentStructureUnitORM,
    DocumentVersionORM,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
LATER = datetime(2026, 8, 27, 13, tzinfo=timezone.utc)
SessionFactory = Callable[[], Session]


def make_config(database_path: Path) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("aaos.database_path", str(database_path))
    return config


@contextmanager
def migrated_session(tmp_path: Path) -> Iterator[tuple[Session, int]]:
    database_path = tmp_path / "document.db"
    command.upgrade(make_config(database_path), "head")
    engine = create_database_engine(database_path)
    sessions = create_session_factory(engine)
    try:
        with sessions() as session:
            course = CourseORM(source_system="mock:phase3", source_id="course-1", title="Course")
            session.add(course)
            session.flush()
            remote_file = RemoteFileORM(
                course_id=course.id,
                source_system="mock:phase3",
                source_id="resource-cm:1",
                title="Document",
            )
            session.add(remote_file)
            session.commit()
            yield session, remote_file.id
    finally:
        engine.dispose()


@contextmanager
def repository_database(
    tmp_path: Path,
) -> Iterator[tuple[DocumentRepository, SessionFactory]]:
    database_path = tmp_path / "repository.db"
    command.upgrade(make_config(database_path), "head")
    engine = create_database_engine(database_path)
    sessions = create_session_factory(engine)
    try:
        yield DocumentRepository(sessions), sessions
    finally:
        engine.dispose()


def seed_remote_files(
    sessions: SessionFactory,
    *records: tuple[str, str, bool],
) -> tuple[int, ...]:
    with sessions() as session, session.begin():
        course = CourseORM(
            source_system="mock:phase3",
            source_id=f"course-{records[0][1]}",
            title="Course",
        )
        session.add(course)
        session.flush()
        rows = [
            RemoteFileORM(
                course_id=course.id,
                source_system=source_system,
                source_id=source_id,
                title=f"Document {source_id}",
                is_active=is_active,
            )
            for source_system, source_id, is_active in records
        ]
        session.add_all(rows)
        session.flush()
        return tuple(row.id for row in rows)


def selected_remote_file(
    repository: DocumentRepository,
    *,
    source_system: str,
    remote_file_id: int,
) -> RemoteFileSelection:
    plan = repository.plan_selection(
        mode=IngestionMode.REFRESH,
        source_system=source_system,
        remote_file_id=remote_file_id,
    )
    return plan.selected[0]


def installed_blob(character: str = "a", *, created: bool = True) -> BlobInstall:
    sha256 = character * 64
    return BlobInstall(
        sha256=sha256,
        size_bytes=100,
        storage_key=f"sha256/{sha256[:2]}/{sha256}.pdf",
        created=created,
    )


def parsed_pdf(*, invalid_second_page: bool = False) -> ParsedPdf:
    return ParsedPdf(
        page_count=2,
        extraction_status=ExtractionStatus.SUCCESS,
        pages=(
            ExtractedPage(
                page_number=1,
                text="First page",
                text_source=TextSource.EMBEDDED_PDF,
                width_points=612.0,
                height_points=792.0,
            ),
            ExtractedPage(
                page_number=0 if invalid_second_page else 2,
                text="Second page",
                text_source=TextSource.EMBEDDED_PDF,
                width_points=612.0,
                height_points=792.0,
            ),
        ),
    )


def document(remote_file_id: int, **overrides: object) -> DocumentORM:
    values: dict[str, object] = {
        "remote_file_id": remote_file_id,
        "ingestion_status": "NEVER_ATTEMPTED",
        "last_attempted_at": None,
        "last_error_code": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return DocumentORM(**values)


def blob(
    sha_character: str = "a",
    *,
    storage_character: str | None = None,
    **overrides: object,
) -> ContentBlobORM:
    storage_hash = (storage_character or sha_character) * 64
    values: dict[str, object] = {
        "sha256": sha_character * 64,
        "size_bytes": 100,
        "media_type": "application/pdf",
        "storage_key": f"sha256/{storage_hash[:2]}/{storage_hash}.pdf",
        "created_at": NOW,
    }
    values.update(overrides)
    return ContentBlobORM(**values)


def version(
    document_id: int,
    content_blob_id: int,
    **overrides: object,
) -> DocumentVersionORM:
    values: dict[str, object] = {
        "document_id": document_id,
        "version_number": 1,
        "content_blob_id": content_blob_id,
        "source_filename": "lecture.pdf",
        "ingested_at": NOW,
        "extraction_status": "SUCCESS",
        "page_count": 1,
    }
    values.update(overrides)
    return DocumentVersionORM(**values)


def page(document_version_id: int, **overrides: object) -> DocumentPageORM:
    values: dict[str, object] = {
        "document_version_id": document_version_id,
        "page_number": 1,
        "text": "",
        "text_source": "EMBEDDED_PDF",
        "width_points": 612.0,
        "height_points": 792.0,
        "created_at": NOW,
    }
    values.update(overrides)
    return DocumentPageORM(**values)


def persist_document_and_blob(session: Session, remote_file_id: int) -> tuple[int, int]:
    document_row = document(remote_file_id)
    blob_row = blob()
    session.add_all((document_row, blob_row))
    session.commit()
    return document_row.id, blob_row.id


def test_document_remote_file_is_one_to_one(tmp_path: Path) -> None:
    with migrated_session(tmp_path) as (session, remote_file_id):
        session.add_all((document(remote_file_id), document(remote_file_id)))
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.parametrize("status", ("", "READY_WITH_WARNING", "ready"))
def test_invalid_document_ingestion_status_is_rejected(tmp_path: Path, status: str) -> None:
    with migrated_session(tmp_path) as (session, remote_file_id):
        session.add(document(remote_file_id, ingestion_status=status))
        with pytest.raises(IntegrityError):
            session.commit()


def test_invalid_document_error_code_is_rejected(tmp_path: Path) -> None:
    with migrated_session(tmp_path) as (session, remote_file_id):
        session.add(document(remote_file_id, last_error_code="raw parser exception"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_explicit_negative_document_id_is_rejected(tmp_path: Path) -> None:
    with migrated_session(tmp_path) as (session, remote_file_id):
        session.add(document(remote_file_id, id=-1))
        with pytest.raises(IntegrityError):
            session.commit()


def test_duplicate_blob_sha256_is_rejected(tmp_path: Path) -> None:
    with migrated_session(tmp_path) as (session, _):
        session.add_all((blob("a"), blob("a", storage_character="b")))
        with pytest.raises(IntegrityError):
            session.commit()


def test_explicit_negative_blob_id_is_rejected(tmp_path: Path) -> None:
    with migrated_session(tmp_path) as (session, _):
        session.add(blob(id=-1))
        with pytest.raises(IntegrityError):
            session.commit()


def test_duplicate_blob_storage_key_is_rejected(tmp_path: Path) -> None:
    with migrated_session(tmp_path) as (session, _):
        session.add_all((blob("a"), blob("b", storage_character="a")))
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.parametrize("size_bytes", (0, 52_428_801))
def test_invalid_blob_size_is_rejected(tmp_path: Path, size_bytes: int) -> None:
    with migrated_session(tmp_path) as (session, _):
        session.add(blob(size_bytes=size_bytes))
        with pytest.raises(IntegrityError):
            session.commit()


def test_invalid_blob_media_type_is_rejected(tmp_path: Path) -> None:
    with migrated_session(tmp_path) as (session, _):
        session.add(blob(media_type="text/html"))
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.parametrize(
    "row",
    (
        blob(sha256="A" * 64),
        blob(sha256="a" * 63),
        blob(storage_key="../escape.pdf"),
        blob(storage_key="sha256/aa/" + "A" * 64 + ".pdf"),
    ),
)
def test_invalid_blob_identity_metadata_is_rejected(
    tmp_path: Path,
    row: ContentBlobORM,
) -> None:
    with migrated_session(tmp_path) as (session, _):
        session.add(row)
        with pytest.raises(IntegrityError):
            session.commit()


def test_duplicate_document_version_number_is_rejected(tmp_path: Path) -> None:
    with migrated_session(tmp_path) as (session, remote_file_id):
        document_id, blob_id = persist_document_and_blob(session, remote_file_id)
        second_blob = blob("b")
        session.add(second_blob)
        session.commit()
        session.add_all((version(document_id, blob_id), version(document_id, second_blob.id)))
        with pytest.raises(IntegrityError):
            session.commit()


def test_duplicate_document_blob_version_is_rejected(tmp_path: Path) -> None:
    with migrated_session(tmp_path) as (session, remote_file_id):
        document_id, blob_id = persist_document_and_blob(session, remote_file_id)
        session.add_all(
            (
                version(document_id, blob_id),
                version(document_id, blob_id, version_number=2),
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_explicit_negative_document_version_id_is_rejected(tmp_path: Path) -> None:
    with migrated_session(tmp_path) as (session, remote_file_id):
        document_id, blob_id = persist_document_and_blob(session, remote_file_id)
        session.add(version(document_id, blob_id, id=-1))
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("version_number", 0),
        ("page_count", 0),
        ("extraction_status", "UNKNOWN"),
    ),
)
def test_invalid_document_version_values_are_rejected(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    with migrated_session(tmp_path) as (session, remote_file_id):
        document_id, blob_id = persist_document_and_blob(session, remote_file_id)
        session.add(version(document_id, blob_id, **{field: value}))
        with pytest.raises(IntegrityError):
            session.commit()


def test_duplicate_document_page_number_is_rejected(tmp_path: Path) -> None:
    with migrated_session(tmp_path) as (session, remote_file_id):
        document_id, blob_id = persist_document_and_blob(session, remote_file_id)
        version_row = version(document_id, blob_id)
        session.add(version_row)
        session.commit()
        session.add_all((page(version_row.id), page(version_row.id)))
        with pytest.raises(IntegrityError):
            session.commit()


def test_explicit_negative_document_page_id_is_rejected(tmp_path: Path) -> None:
    with migrated_session(tmp_path) as (session, remote_file_id):
        document_id, blob_id = persist_document_and_blob(session, remote_file_id)
        version_row = version(document_id, blob_id)
        session.add(version_row)
        session.commit()
        session.add(page(version_row.id, id=-1))
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("page_number", 0),
        ("width_points", 0.0),
        ("height_points", -1.0),
        ("text_source", "OCR"),
    ),
)
def test_invalid_document_page_values_are_rejected(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    with migrated_session(tmp_path) as (session, remote_file_id):
        document_id, blob_id = persist_document_and_blob(session, remote_file_id)
        version_row = version(document_id, blob_id)
        session.add(version_row)
        session.commit()
        session.add(page(version_row.id, **{field: value}))
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.parametrize("field", ("width_points", "height_points"))
def test_infinite_document_page_dimension_is_rejected(
    tmp_path: Path,
    field: str,
) -> None:
    with migrated_session(tmp_path) as (session, remote_file_id):
        document_id, blob_id = persist_document_and_blob(session, remote_file_id)
        version_row = version(document_id, blob_id)
        session.add(version_row)
        session.commit()
        session.add(page(version_row.id, **{field: float("inf")}))
        with pytest.raises(IntegrityError):
            session.commit()


def test_document_remote_file_foreign_key_is_rejected(tmp_path: Path) -> None:
    with migrated_session(tmp_path) as (session, _):
        session.add(document(999))
        with pytest.raises(IntegrityError):
            session.commit()


def test_version_document_foreign_key_is_independently_rejected(tmp_path: Path) -> None:
    with migrated_session(tmp_path) as (session, _):
        blob_row = blob()
        session.add(blob_row)
        session.commit()
        session.add(version(999, blob_row.id))
        with pytest.raises(IntegrityError):
            session.commit()


def test_version_blob_foreign_key_is_independently_rejected(tmp_path: Path) -> None:
    with migrated_session(tmp_path) as (session, remote_file_id):
        document_row = document(remote_file_id)
        session.add(document_row)
        session.commit()
        session.add(version(document_row.id, 999))
        with pytest.raises(IntegrityError):
            session.commit()


def test_page_version_foreign_key_is_rejected(tmp_path: Path) -> None:
    with migrated_session(tmp_path) as (session, _):
        session.add(page(999))
        with pytest.raises(IntegrityError):
            session.commit()


def test_referenced_remote_file_deletion_is_restrictive(tmp_path: Path) -> None:
    with migrated_session(tmp_path) as (session, remote_file_id):
        session.add(document(remote_file_id))
        session.commit()
        remote_file = session.get(RemoteFileORM, remote_file_id)
        assert remote_file is not None
        session.delete(remote_file)
        with pytest.raises(IntegrityError):
            session.commit()


def test_empty_page_text_and_aware_utc_timestamps_round_trip(tmp_path: Path) -> None:
    source_time = datetime(2026, 8, 27, 15, tzinfo=timezone(timedelta(hours=3)))
    expected_utc = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
    with migrated_session(tmp_path) as (session, remote_file_id):
        document_row = document(
            remote_file_id,
            ingestion_status="READY",
            last_attempted_at=source_time,
            created_at=source_time,
            updated_at=source_time,
        )
        blob_row = blob(created_at=source_time)
        session.add_all((document_row, blob_row))
        session.commit()
        version_row = version(document_row.id, blob_row.id, ingested_at=source_time)
        session.add(version_row)
        session.commit()
        page_row = page(version_row.id, text="", created_at=source_time)
        session.add(page_row)
        session.commit()
        session.expire_all()

        assert document_row.last_attempted_at == expected_utc
        assert document_row.created_at == expected_utc
        assert document_row.updated_at == expected_utc
        assert blob_row.created_at == expected_utc
        assert version_row.ingested_at == expected_utc
        assert page_row.created_at == expected_utc
        assert page_row.text == ""


def test_naive_document_timestamp_is_rejected(tmp_path: Path) -> None:
    with migrated_session(tmp_path) as (session, remote_file_id):
        session.add(document(remote_file_id, created_at=datetime(2026, 8, 27, 12)))
        with pytest.raises(StatementError, match="timezone"):
            session.commit()


def test_plan_selection_filters_source_active_rows_and_orders_by_id(
    tmp_path: Path,
) -> None:
    with repository_database(tmp_path) as (repository, sessions):
        ids = seed_remote_files(
            sessions,
            ("moodle:other", "other", True),
            ("moodle:fixture", "second", True),
            ("moodle:fixture", "inactive", False),
            ("moodle:fixture", "fourth", True),
        )

        plan = repository.plan_selection(
            mode=IngestionMode.REFRESH,
            source_system="moodle:fixture",
            remote_file_id=None,
        )

        assert tuple(item.remote_file_id for item in plan.selected) == (ids[1], ids[3])
        assert plan.fetch == plan.selected
        assert plan.skipped == ()


@pytest.mark.parametrize("invalid_id", (0, -1, 999_999))
def test_specific_missing_or_nonpositive_remote_file_is_rejected(
    tmp_path: Path,
    invalid_id: int,
) -> None:
    with repository_database(tmp_path) as (repository, _):
        with pytest.raises(DocumentIngestionFailure) as caught:
            repository.plan_selection(
                mode=IngestionMode.INGEST_NEW,
                source_system="moodle:fixture",
                remote_file_id=invalid_id,
            )

    assert caught.value.code is IngestionErrorCode.UNAUTHORIZED_ROUTE
    assert str(caught.value) == "document ingestion failed"


def test_specific_inactive_remote_file_is_rejected(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions):
        (remote_file_id,) = seed_remote_files(
            sessions,
            ("moodle:fixture", "inactive", False),
        )

        with pytest.raises(DocumentIngestionFailure) as caught:
            repository.plan_selection(
                mode=IngestionMode.INGEST_NEW,
                source_system="moodle:fixture",
                remote_file_id=remote_file_id,
            )

    assert caught.value.code is IngestionErrorCode.UNAUTHORIZED_ROUTE


def test_specific_wrong_source_remote_file_is_rejected(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions):
        (remote_file_id,) = seed_remote_files(
            sessions,
            ("moodle:other", "wrong-source", True),
        )

        with pytest.raises(DocumentIngestionFailure) as caught:
            repository.plan_selection(
                mode=IngestionMode.INGEST_NEW,
                source_system="moodle:fixture",
                remote_file_id=remote_file_id,
            )

    assert caught.value.code is IngestionErrorCode.UNAUTHORIZED_ROUTE


def test_ingest_new_separates_ready_document_without_fetching_it(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions):
        ready_id, new_id = seed_remote_files(
            sessions,
            ("moodle:fixture", "ready", True),
            ("moodle:fixture", "new", True),
        )
        ready_selection = selected_remote_file(
            repository,
            source_system="moodle:fixture",
            remote_file_id=ready_id,
        )
        repository.persist_success(
            selection=ready_selection,
            blob=installed_blob(),
            source_filename="ready.pdf",
            parsed_pdf=parsed_pdf(),
            attempted_at=NOW,
        )

        plan = repository.plan_selection(
            mode=IngestionMode.INGEST_NEW,
            source_system="moodle:fixture",
            remote_file_id=None,
        )

        assert tuple(item.remote_file_id for item in plan.selected) == (ready_id, new_id)
        assert tuple(item.remote_file_id for item in plan.fetch) == (new_id,)
        assert tuple(item.remote_file_id for item in plan.skipped) == (ready_id,)


def test_refresh_fetches_every_selected_active_remote_file(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions):
        first_id, second_id = seed_remote_files(
            sessions,
            ("moodle:fixture", "first", True),
            ("moodle:fixture", "second", True),
        )
        first_selection = selected_remote_file(
            repository,
            source_system="moodle:fixture",
            remote_file_id=first_id,
        )
        repository.persist_success(
            selection=first_selection,
            blob=installed_blob(),
            source_filename="first.pdf",
            parsed_pdf=parsed_pdf(),
            attempted_at=NOW,
        )

        plan = repository.plan_selection(
            mode=IngestionMode.REFRESH,
            source_system="moodle:fixture",
            remote_file_id=None,
        )

        assert tuple(item.remote_file_id for item in plan.fetch) == (first_id, second_id)
        assert plan.skipped == ()


def test_persist_success_creates_ready_document_version_and_page_provenance(
    tmp_path: Path,
) -> None:
    with repository_database(tmp_path) as (repository, sessions):
        (remote_file_id,) = seed_remote_files(
            sessions,
            ("moodle:fixture", "resource", True),
        )
        selection = selected_remote_file(
            repository,
            source_system="moodle:fixture",
            remote_file_id=remote_file_id,
        )

        result = repository.persist_success(
            selection=selection,
            blob=installed_blob(),
            source_filename="lecture.pdf",
            parsed_pdf=parsed_pdf(),
            attempted_at=NOW,
        )

        assert result.version_number == 1
        assert result.content_blob_created is True
        with sessions() as session:
            document_row = session.scalar(
                select(DocumentORM).where(DocumentORM.remote_file_id == remote_file_id)
            )
            assert document_row is not None
            assert document_row.id == result.document_id
            assert document_row.ingestion_status == IngestionStatus.READY.value
            assert document_row.last_attempted_at == NOW
            assert document_row.last_error_code is None
            version_row = session.scalar(
                select(DocumentVersionORM).where(
                    DocumentVersionORM.document_id == document_row.id,
                    DocumentVersionORM.version_number == result.version_number,
                )
            )
            assert version_row is not None
            assert version_row.document_id == document_row.id
            assert version_row.content_blob_id == result.content_blob_id
            assert version_row.source_filename == "lecture.pdf"
            assert version_row.ingested_at == NOW
            assert version_row.page_count == 2
            assert version_row.provenance_mode == "PAGE"
            assert version_row.extraction_status == ExtractionStatus.SUCCESS.value
            page_rows = session.scalars(
                select(DocumentPageORM)
                .where(DocumentPageORM.document_version_id == version_row.id)
                .order_by(DocumentPageORM.page_number)
            ).all()
            assert [row.page_number for row in page_rows] == [1, 2]
            assert [row.text for row in page_rows] == ["First page", "Second page"]
            assert all(row.text_source == TextSource.EMBEDDED_PDF.value for row in page_rows)
            assert all(row.width_points == 612.0 for row in page_rows)
            assert all(row.height_points == 792.0 for row in page_rows)
            assert all(row.created_at == NOW for row in page_rows)


def test_same_remote_file_and_sha_returns_existing_version(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions):
        (remote_file_id,) = seed_remote_files(
            sessions,
            ("moodle:fixture", "resource", True),
        )
        selection = selected_remote_file(
            repository,
            source_system="moodle:fixture",
            remote_file_id=remote_file_id,
        )
        first = repository.persist_success(
            selection=selection,
            blob=installed_blob(),
            source_filename="first.pdf",
            parsed_pdf=parsed_pdf(),
            attempted_at=NOW,
        )

        second = repository.persist_success(
            selection=selection,
            blob=installed_blob(created=False),
            source_filename="renamed.pdf",
            parsed_pdf=parsed_pdf(),
            attempted_at=LATER,
        )

        assert second.document_id == first.document_id
        assert second.version_number == first.version_number
        assert second.content_blob_id == first.content_blob_id
        assert second.content_blob_created is False
        assert (
            repository.existing_version_for_sha256(
                remote_file_id=remote_file_id,
                sha256="a" * 64,
            )
            == 1
        )
        with sessions() as session:
            assert session.scalar(select(func.count()).select_from(DocumentVersionORM)) == 1
            assert session.scalar(select(func.count()).select_from(ContentBlobORM)) == 1
            assert session.scalar(select(func.count()).select_from(DocumentPageORM)) == 2
            document_row = session.scalar(select(DocumentORM))
            assert document_row is not None
            assert document_row.last_attempted_at == LATER
            assert document_row.last_error_code is None


def test_changed_sha_allocates_next_contiguous_version(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions):
        (remote_file_id,) = seed_remote_files(
            sessions,
            ("moodle:fixture", "resource", True),
        )
        selection = selected_remote_file(
            repository,
            source_system="moodle:fixture",
            remote_file_id=remote_file_id,
        )
        first = repository.persist_success(
            selection=selection,
            blob=installed_blob("a"),
            source_filename="first.pdf",
            parsed_pdf=parsed_pdf(),
            attempted_at=NOW,
        )

        second = repository.persist_success(
            selection=selection,
            blob=installed_blob("b"),
            source_filename="second.pdf",
            parsed_pdf=parsed_pdf(),
            attempted_at=LATER,
        )

        assert (first.version_number, second.version_number) == (1, 2)
        with sessions() as session:
            version_numbers = session.scalars(
                select(DocumentVersionORM.version_number).order_by(
                    DocumentVersionORM.version_number
                )
            ).all()
            assert version_numbers == [1, 2]


def test_different_remote_files_share_global_blob_but_not_version_identity(
    tmp_path: Path,
) -> None:
    with repository_database(tmp_path) as (repository, sessions):
        first_id, second_id = seed_remote_files(
            sessions,
            ("moodle:fixture", "first", True),
            ("moodle:fixture", "second", True),
        )
        first_selection = selected_remote_file(
            repository,
            source_system="moodle:fixture",
            remote_file_id=first_id,
        )
        second_selection = selected_remote_file(
            repository,
            source_system="moodle:fixture",
            remote_file_id=second_id,
        )

        first = repository.persist_success(
            selection=first_selection,
            blob=installed_blob(),
            source_filename="first.pdf",
            parsed_pdf=parsed_pdf(),
            attempted_at=NOW,
        )
        assert (
            repository.existing_version_for_sha256(
                remote_file_id=second_id,
                sha256="a" * 64,
            )
            is None
        )
        second = repository.persist_success(
            selection=second_selection,
            blob=installed_blob(created=False),
            source_filename="second.pdf",
            parsed_pdf=parsed_pdf(),
            attempted_at=NOW,
        )

        assert first.document_id != second.document_id
        assert first.content_blob_id == second.content_blob_id
        assert first.content_blob_created is True
        assert second.content_blob_created is False
        with sessions() as session:
            assert session.scalar(select(func.count()).select_from(ContentBlobORM)) == 1
            assert session.scalar(select(func.count()).select_from(DocumentVersionORM)) == 2


def test_first_failure_creates_failed_document_without_versions(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions):
        (remote_file_id,) = seed_remote_files(
            sessions,
            ("moodle:fixture", "resource", True),
        )
        selection = selected_remote_file(
            repository,
            source_system="moodle:fixture",
            remote_file_id=remote_file_id,
        )

        repository.record_failure(
            selection=selection,
            code=IngestionErrorCode.NETWORK_FAILURE,
            attempted_at=NOW,
        )

        with sessions() as session:
            document_row = session.scalar(select(DocumentORM))
            assert document_row is not None
            assert document_row.ingestion_status == IngestionStatus.FAILED.value
            assert document_row.last_error_code == IngestionErrorCode.NETWORK_FAILURE.value
            assert document_row.last_attempted_at == NOW
            assert session.scalar(select(func.count()).select_from(DocumentVersionORM)) == 0


def test_failed_refresh_preserves_ready_status_and_versions(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions):
        (remote_file_id,) = seed_remote_files(
            sessions,
            ("moodle:fixture", "resource", True),
        )
        selection = selected_remote_file(
            repository,
            source_system="moodle:fixture",
            remote_file_id=remote_file_id,
        )
        repository.persist_success(
            selection=selection,
            blob=installed_blob(),
            source_filename="lecture.pdf",
            parsed_pdf=parsed_pdf(),
            attempted_at=NOW,
        )

        repository.record_failure(
            selection=selection,
            code=IngestionErrorCode.PDF_PARSE_FAILED,
            attempted_at=LATER,
        )

        with sessions() as session:
            document_row = session.scalar(select(DocumentORM))
            assert document_row is not None
            assert document_row.ingestion_status == IngestionStatus.READY.value
            assert document_row.last_error_code == IngestionErrorCode.PDF_PARSE_FAILED.value
            assert document_row.last_attempted_at == LATER
            assert session.scalar(select(func.count()).select_from(DocumentVersionORM)) == 1
            assert session.scalar(select(func.count()).select_from(DocumentPageORM)) == 2


def test_naive_repository_timestamp_fails_closed_without_state(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions):
        (remote_file_id,) = seed_remote_files(
            sessions,
            ("moodle:fixture", "resource", True),
        )
        selection = selected_remote_file(
            repository,
            source_system="moodle:fixture",
            remote_file_id=remote_file_id,
        )

        with pytest.raises(DocumentIngestionFailure) as caught:
            repository.record_failure(
                selection=selection,
                code=IngestionErrorCode.NETWORK_FAILURE,
                attempted_at=datetime(2026, 8, 27, 12),
            )

        assert caught.value.code is IngestionErrorCode.PERSISTENCE_FAILED
        assert str(caught.value) == "document ingestion failed"
        with sessions() as session:
            assert session.scalar(select(func.count()).select_from(DocumentORM)) == 0


def test_persist_success_rolls_back_partial_version_pages_and_state(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions):
        (remote_file_id,) = seed_remote_files(
            sessions,
            ("moodle:fixture", "resource", True),
        )
        selection = selected_remote_file(
            repository,
            source_system="moodle:fixture",
            remote_file_id=remote_file_id,
        )

        with pytest.raises(DocumentIngestionFailure) as caught:
            repository.persist_success(
                selection=selection,
                blob=installed_blob(),
                source_filename="lecture.pdf",
                parsed_pdf=parsed_pdf(invalid_second_page=True),
                attempted_at=NOW,
            )

        assert caught.value.code is IngestionErrorCode.PERSISTENCE_FAILED
        assert str(caught.value) == "document ingestion failed"
        with sessions() as session:
            assert session.scalar(select(func.count()).select_from(DocumentORM)) == 0
            assert session.scalar(select(func.count()).select_from(ContentBlobORM)) == 0
            assert session.scalar(select(func.count()).select_from(DocumentVersionORM)) == 0
            assert session.scalar(select(func.count()).select_from(DocumentPageORM)) == 0


def test_blob_is_referenced_reports_exact_global_reference_state(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions):
        (remote_file_id,) = seed_remote_files(
            sessions,
            ("moodle:fixture", "resource", True),
        )
        selection = selected_remote_file(
            repository,
            source_system="moodle:fixture",
            remote_file_id=remote_file_id,
        )
        assert repository.blob_is_referenced(sha256="a" * 64) is False

        repository.persist_success(
            selection=selection,
            blob=installed_blob(),
            source_filename="lecture.pdf",
            parsed_pdf=parsed_pdf(),
            attempted_at=NOW,
        )

        assert repository.blob_is_referenced(sha256="a" * 64) is True
        assert repository.blob_is_referenced(sha256="b" * 64) is False


_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def installed_docx_blob(
    sha_character: str = "d",
    *,
    created: bool = True,
) -> BlobInstall:
    sha256 = sha_character * 64
    return BlobInstall(
        sha256=sha256,
        size_bytes=100,
        storage_key=f"sha256/{sha256[:2]}/{sha256}.docx",
        created=created,
    )


def parsed_docx(*, invalid_ordinal: bool = False) -> ParsedDocx:
    return ParsedDocx(
        units=(
            ExtractedStructureUnit(
                ordinal=0 if invalid_ordinal else 1,
                kind=StructureUnitKind.HEADING,
                text="Course Overview",
                heading_level=1,
                table_index=None,
                row_index=None,
                column_index=None,
            ),
            ExtractedStructureUnit(
                ordinal=2,
                kind=StructureUnitKind.PARAGRAPH,
                text="Structured body",
                heading_level=None,
                table_index=None,
                row_index=None,
                column_index=None,
            ),
            ExtractedStructureUnit(
                ordinal=3,
                kind=StructureUnitKind.TABLE_CELL,
                text="A1",
                heading_level=None,
                table_index=0,
                row_index=0,
                column_index=0,
            ),
        )
    )


def test_persist_structural_success_creates_structure_provenance(
    tmp_path: Path,
) -> None:
    with repository_database(tmp_path) as (repository, sessions):
        (remote_file_id,) = seed_remote_files(
            sessions,
            ("moodle:fixture", "resource", True),
        )
        selection = selected_remote_file(
            repository,
            source_system="moodle:fixture",
            remote_file_id=remote_file_id,
        )

        result = repository.persist_structural_success(
            selection=selection,
            blob=installed_docx_blob(),
            source_filename="lecture.docx",
            parsed_docx=parsed_docx(),
            attempted_at=NOW,
        )

        assert result.version_number == 1
        assert result.content_blob_created is True

        with sessions() as session:
            document_row = session.scalar(
                select(DocumentORM).where(DocumentORM.remote_file_id == remote_file_id)
            )
            assert document_row is not None
            assert document_row.ingestion_status == IngestionStatus.READY.value

            version_row = session.scalar(
                select(DocumentVersionORM).where(DocumentVersionORM.document_id == document_row.id)
            )
            assert version_row is not None
            assert version_row.source_filename == "lecture.docx"
            assert version_row.extraction_status == ExtractionStatus.SUCCESS.value
            assert version_row.provenance_mode == "STRUCTURE"
            assert version_row.page_count is None

            blob_row = session.get(ContentBlobORM, result.content_blob_id)
            assert blob_row is not None
            assert blob_row.media_type == _DOCX_MIME
            assert blob_row.storage_key == installed_docx_blob().storage_key

            structure_rows = session.scalars(
                select(DocumentStructureUnitORM)
                .where(DocumentStructureUnitORM.document_version_id == version_row.id)
                .order_by(DocumentStructureUnitORM.ordinal)
            ).all()

            assert [
                (
                    row.ordinal,
                    row.kind,
                    row.text,
                    row.heading_level,
                    row.table_index,
                    row.row_index,
                    row.column_index,
                )
                for row in structure_rows
            ] == [
                (1, "HEADING", "Course Overview", 1, None, None, None),
                (2, "PARAGRAPH", "Structured body", None, None, None, None),
                (3, "TABLE_CELL", "A1", None, 0, 0, 0),
            ]

            assert (
                session.scalar(
                    select(func.count())
                    .select_from(DocumentPageORM)
                    .where(DocumentPageORM.document_version_id == version_row.id)
                )
                == 0
            )


def test_structural_same_document_sha_is_unchanged_without_duplicate_units(
    tmp_path: Path,
) -> None:
    with repository_database(tmp_path) as (repository, sessions):
        (remote_file_id,) = seed_remote_files(
            sessions,
            ("moodle:fixture", "resource", True),
        )
        selection = selected_remote_file(
            repository,
            source_system="moodle:fixture",
            remote_file_id=remote_file_id,
        )

        first = repository.persist_structural_success(
            selection=selection,
            blob=installed_docx_blob(),
            source_filename="first.docx",
            parsed_docx=parsed_docx(),
            attempted_at=NOW,
        )
        second = repository.persist_structural_success(
            selection=selection,
            blob=installed_docx_blob(created=False),
            source_filename="renamed.docx",
            parsed_docx=parsed_docx(),
            attempted_at=LATER,
        )

        assert second.document_id == first.document_id
        assert second.version_number == first.version_number
        assert second.content_blob_id == first.content_blob_id
        assert second.content_blob_created is False

        with sessions() as session:
            assert session.scalar(select(func.count()).select_from(DocumentVersionORM)) == 1
            assert session.scalar(select(func.count()).select_from(DocumentStructureUnitORM)) == 3


def test_structural_blob_deduplicates_globally_without_merging_documents(
    tmp_path: Path,
) -> None:
    with repository_database(tmp_path) as (repository, sessions):
        first_id, second_id = seed_remote_files(
            sessions,
            ("moodle:fixture", "first", True),
            ("moodle:fixture", "second", True),
        )
        first_selection = selected_remote_file(
            repository,
            source_system="moodle:fixture",
            remote_file_id=first_id,
        )
        second_selection = selected_remote_file(
            repository,
            source_system="moodle:fixture",
            remote_file_id=second_id,
        )

        first = repository.persist_structural_success(
            selection=first_selection,
            blob=installed_docx_blob("e"),
            source_filename="first.docx",
            parsed_docx=parsed_docx(),
            attempted_at=NOW,
        )
        second = repository.persist_structural_success(
            selection=second_selection,
            blob=installed_docx_blob("e", created=False),
            source_filename="second.docx",
            parsed_docx=parsed_docx(),
            attempted_at=NOW,
        )

        assert first.document_id != second.document_id
        assert first.content_blob_id == second.content_blob_id
        assert first.content_blob_created is True
        assert second.content_blob_created is False

        with sessions() as session:
            assert session.scalar(select(func.count()).select_from(ContentBlobORM)) == 1
            assert session.scalar(select(func.count()).select_from(DocumentVersionORM)) == 2
            assert session.scalar(select(func.count()).select_from(DocumentStructureUnitORM)) == 6


def test_structural_persistence_rolls_back_invalid_units_and_document_state(
    tmp_path: Path,
) -> None:
    with repository_database(tmp_path) as (repository, sessions):
        (remote_file_id,) = seed_remote_files(
            sessions,
            ("moodle:fixture", "resource", True),
        )
        selection = selected_remote_file(
            repository,
            source_system="moodle:fixture",
            remote_file_id=remote_file_id,
        )

        with pytest.raises(DocumentIngestionFailure) as caught:
            repository.persist_structural_success(
                selection=selection,
                blob=installed_docx_blob("f"),
                source_filename="invalid.docx",
                parsed_docx=parsed_docx(invalid_ordinal=True),
                attempted_at=NOW,
            )

        assert caught.value.code is IngestionErrorCode.PERSISTENCE_FAILED
        assert str(caught.value) == "document ingestion failed"

        with sessions() as session:
            assert session.scalar(select(func.count()).select_from(DocumentORM)) == 0
            assert session.scalar(select(func.count()).select_from(ContentBlobORM)) == 0
            assert session.scalar(select(func.count()).select_from(DocumentVersionORM)) == 0
            assert session.scalar(select(func.count()).select_from(DocumentPageORM)) == 0
            assert session.scalar(select(func.count()).select_from(DocumentStructureUnitORM)) == 0


def test_cross_media_sha_reuse_fails_closed(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions):
        first_id, second_id = seed_remote_files(
            sessions,
            ("moodle:fixture", "pdf", True),
            ("moodle:fixture", "docx", True),
        )
        pdf_selection = selected_remote_file(
            repository,
            source_system="moodle:fixture",
            remote_file_id=first_id,
        )
        docx_selection = selected_remote_file(
            repository,
            source_system="moodle:fixture",
            remote_file_id=second_id,
        )

        repository.persist_success(
            selection=pdf_selection,
            blob=installed_blob("a"),
            source_filename="source.pdf",
            parsed_pdf=parsed_pdf(),
            attempted_at=NOW,
        )

        with pytest.raises(DocumentIngestionFailure) as caught:
            repository.persist_structural_success(
                selection=docx_selection,
                blob=installed_docx_blob("a", created=False),
                source_filename="source.docx",
                parsed_docx=parsed_docx(),
                attempted_at=LATER,
            )

        assert caught.value.code is IngestionErrorCode.PERSISTENCE_FAILED

        with sessions() as session:
            assert session.scalar(select(func.count()).select_from(ContentBlobORM)) == 1
            assert session.scalar(select(func.count()).select_from(DocumentVersionORM)) == 1
            assert session.scalar(select(func.count()).select_from(DocumentStructureUnitORM)) == 0


def test_existing_sha_lookup_is_provenance_aware(tmp_path: Path) -> None:
    with repository_database(tmp_path) as (repository, sessions):
        pdf_id, docx_id = seed_remote_files(
            sessions,
            ("moodle:fixture", "pdf-resource", True),
            ("moodle:fixture", "docx-resource", True),
        )

        pdf_selection = selected_remote_file(
            repository,
            source_system="moodle:fixture",
            remote_file_id=pdf_id,
        )
        docx_selection = selected_remote_file(
            repository,
            source_system="moodle:fixture",
            remote_file_id=docx_id,
        )

        pdf_blob = installed_blob("9")
        docx_blob = installed_docx_blob("8")

        repository.persist_success(
            selection=pdf_selection,
            blob=pdf_blob,
            source_filename="source.pdf",
            parsed_pdf=parsed_pdf(),
            attempted_at=NOW,
        )
        repository.persist_structural_success(
            selection=docx_selection,
            blob=docx_blob,
            source_filename="source.docx",
            parsed_docx=parsed_docx(),
            attempted_at=NOW,
        )

        assert (
            repository.existing_version_for_sha256(
                remote_file_id=pdf_id,
                sha256=pdf_blob.sha256,
            )
            == 1
        )
        assert (
            repository.existing_structural_version_for_sha256(
                remote_file_id=pdf_id,
                sha256=pdf_blob.sha256,
            )
            is None
        )

        assert (
            repository.existing_structural_version_for_sha256(
                remote_file_id=docx_id,
                sha256=docx_blob.sha256,
            )
            == 1
        )
        assert (
            repository.existing_version_for_sha256(
                remote_file_id=docx_id,
                sha256=docx_blob.sha256,
            )
            is None
        )
