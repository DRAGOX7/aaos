from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from aaos.documents.contracts import IngestionStatus
from aaos.main import create_app
from aaos.persistence.academic_models import Base, CourseORM, RemoteFileORM
from aaos.persistence.database import create_database_engine, create_session_factory
from aaos.persistence.document_models import (
    ContentBlobORM,
    DocumentORM,
    DocumentVersionORM,
)

NOW = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    db_engine = create_database_engine(tmp_path / "phase7-documents-api.db")
    Base.metadata.create_all(db_engine)
    try:
        yield db_engine
    finally:
        db_engine.dispose()


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(engine)


def _client(sessions: sessionmaker[Session]) -> TestClient:
    return TestClient(create_app(session_factory=sessions), raise_server_exceptions=False)


def _seed_document(
    sessions: sessionmaker[Session],
    *,
    document_id: int,
    course_id: int = 1,
    course_active: bool = True,
    remote_active: bool = True,
    title: str | None = None,
    versions: tuple[tuple[int, str, int], ...] = (),
) -> None:
    with sessions() as session, session.begin():
        course = session.get(CourseORM, course_id)
        if course is None:
            session.add(
                CourseORM(
                    id=course_id,
                    source_system="test",
                    source_id=f"course-{course_id}",
                    title=f"Course {course_id}",
                    is_active=course_active,
                )
            )
            session.flush()
        else:
            course.is_active = course_active

        remote_id = 10_000 + document_id
        session.add(
            RemoteFileORM(
                id=remote_id,
                course_id=course_id,
                source_system="test",
                source_id=f"remote-{document_id}",
                title=title or f"Document {document_id}",
                is_active=remote_active,
            )
        )
        session.flush()
        session.add(
            DocumentORM(
                id=document_id,
                remote_file_id=remote_id,
                ingestion_status=IngestionStatus.READY.value,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()

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
                    source_filename="private-source.pdf",
                    ingested_at=NOW,
                    extraction_status=extraction_status,
                    page_count=page_count,
                )
            )


def test_documents_endpoint_returns_safe_metadata_only(
    sessions: sessionmaker[Session],
) -> None:
    _seed_document(
        sessions,
        document_id=1,
        title="Lecture Notes",
        versions=((1, "SUCCESS", 4),),
    )

    response = _client(sessions).get("/api/v1/documents")

    assert response.status_code == 200
    assert response.json() == {
        "documents": [
            {
                "id": 1,
                "course_id": 1,
                "title": "Lecture Notes",
                "ingestion_status": "READY",
                "extraction_status": "SUCCESS",
                "page_count": 4,
                "index_readiness": None,
                "index_coverage": None,
                "updated_at": "2026-09-17T12:00:00Z",
            }
        ]
    }
    for forbidden in ("private-source.pdf", "storage_key", "source_id", "remote_url"):
        assert forbidden not in response.text


def test_document_detail_missing_and_inactive_parent_are_not_found(
    sessions: sessionmaker[Session],
) -> None:
    _seed_document(
        sessions,
        document_id=2,
        course_id=2,
        course_active=False,
    )

    inactive = _client(sessions).get("/api/v1/documents/2")
    missing = _client(sessions).get("/api/v1/documents/404")

    assert inactive.status_code == 404
    assert missing.status_code == 404
    assert inactive.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"
    assert missing.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"
    assert inactive.json()["error"]["message"] == missing.json()["error"]["message"]


def test_documents_endpoint_filters_inactive_files_and_courses(
    sessions: sessionmaker[Session],
) -> None:
    _seed_document(sessions, document_id=10, course_id=10)
    _seed_document(sessions, document_id=11, course_id=11, course_active=False)
    _seed_document(sessions, document_id=12, remote_active=False)

    response = _client(sessions).get("/api/v1/documents")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["documents"]] == [10]

    filtered = _client(sessions).get(
        "/api/v1/documents",
        params={"course_id": 10},
    )
    assert filtered.status_code == 200
    assert [item["id"] for item in filtered.json()["documents"]] == [10]


@pytest.mark.parametrize("course_id", [11, 404])
def test_documents_missing_or_inactive_course_filter_is_non_leaking(
    sessions: sessionmaker[Session],
    course_id: int,
) -> None:
    _seed_document(sessions, document_id=20, course_id=11, course_active=False)

    response = _client(sessions).get(
        "/api/v1/documents",
        params={"course_id": course_id},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "COURSE_NOT_FOUND"


@pytest.mark.parametrize("value", ["0", "-1", "not-an-id"])
def test_documents_rejects_malformed_or_non_positive_course_filter(
    sessions: sessionmaker[Session],
    value: str,
) -> None:
    response = _client(sessions).get(
        "/api/v1/documents",
        params={"course_id": value},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


@pytest.mark.parametrize("path", ["0", "-1", "not-an-id"])
def test_document_detail_rejects_malformed_or_non_positive_ids(
    sessions: sessionmaker[Session],
    path: str,
) -> None:
    response = _client(sessions).get(f"/api/v1/documents/{path}")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_document_routes_have_no_write_or_runtime_authority() -> None:
    route_source = Path("src/aaos/web/routes.py").read_text(encoding="utf-8")

    for token in (
        "INSERT",
        "UPDATE",
        "DELETE",
        "DocumentRepository",
        "RagService",
        "Embedding",
        "Chroma",
        "FTS",
        "ToolRegistry",
        "network",
    ):
        assert token.casefold() not in route_source.casefold()
