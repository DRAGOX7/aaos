"""Database-enforced invariants for Phase 3 document mappings."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from aaos.persistence.academic_models import CourseORM, RemoteFileORM
from aaos.persistence.database import create_database_engine, create_session_factory
from aaos.persistence.document_models import (
    ContentBlobORM,
    DocumentORM,
    DocumentPageORM,
    DocumentVersionORM,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)


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
