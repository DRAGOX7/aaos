"""Viewing a document's original file, and listing local study tasks over the API."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from aaos.persistence.academic_models import Base, CourseORM, RemoteFileORM
from aaos.persistence.database import create_database_engine, create_session_factory
from aaos.persistence.document_models import ContentBlobORM, DocumentORM, DocumentVersionORM
from aaos.study_tasks.contracts import StudyTask, StudyTaskPriority, StudyTaskStatus
from aaos.web.document_files import DocumentFile, DocumentFileService
from aaos.web.errors import Phase7ApiError, install_error_boundary
from aaos.web.routes import create_api_v1_router

NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
PDF_BYTES = b"%PDF-1.4 KNIME notes"


@pytest.fixture
def sessions(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = create_database_engine(tmp_path / "files.db")
    Base.metadata.create_all(engine)
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()


def _seed(
    sessions: sessionmaker[Session],
    root: Path,
    *,
    body: bytes = PDF_BYTES,
    stored: bytes | None = None,
    write_file: bool = True,
    remote_active: bool = True,
) -> None:
    digest = hashlib.sha256(body).hexdigest()
    key = f"sha256/{digest[:2]}/{digest}.pdf"
    target = root / key
    if write_file:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body if stored is None else stored)
    with sessions() as session, session.begin():
        session.add(
            CourseORM(id=1, source_system="t", source_id="c1", title="AI380", is_active=True)
        )
        session.flush()
        session.add(
            RemoteFileORM(
                id=11,
                course_id=1,
                source_system="t",
                source_id="r1",
                title="Intro to KNIME.pdf",
                is_active=remote_active,
            )
        )
        session.flush()
        session.add(
            DocumentORM(
                id=5, remote_file_id=11, ingestion_status="READY", created_at=NOW, updated_at=NOW
            )
        )
        session.add(
            ContentBlobORM(
                id=21,
                sha256=digest,
                size_bytes=len(body),
                media_type="application/pdf",
                storage_key=key,
                created_at=NOW,
            )
        )
        session.flush()
        session.add(
            DocumentVersionORM(
                id=31,
                document_id=5,
                version_number=1,
                content_blob_id=21,
                source_filename="intro.pdf",
                ingested_at=NOW,
                extraction_status="SUCCESS",
                page_count=1,
            )
        )


def test_the_stored_file_of_a_visible_document_is_returned(
    sessions: sessionmaker[Session], tmp_path: Path
) -> None:
    _seed(sessions, tmp_path)

    document = DocumentFileService(sessions, tmp_path).open_document_file(5)

    assert document == DocumentFile(PDF_BYTES, "application/pdf", "Intro to KNIME.pdf")


@pytest.mark.parametrize(
    "seed",
    [
        {"stored": b"tampered bytes"},  # hash no longer matches
        {"write_file": False},  # not on disk
        {"remote_active": False},  # removed from Moodle
    ],
)
def test_a_file_that_is_missing_altered_outside_or_hidden_is_not_served(
    sessions: sessionmaker[Session], tmp_path: Path, seed: dict[str, object]
) -> None:
    root = tmp_path / "documents"
    _seed(sessions, root, **seed)  # type: ignore[arg-type]

    with pytest.raises(Phase7ApiError) as error:
        DocumentFileService(sessions, root).open_document_file(5)

    assert error.value.status_code == 404


class _Courses:
    def list_courses(self) -> tuple[()]:
        return ()


class _Files:
    def __init__(self, media_type: str) -> None:
        self.media_type = media_type

    def open_document_file(self, document_id: int) -> DocumentFile:
        name = "Notes.pdf" if self.media_type == "application/pdf" else "Notes.docx"
        return DocumentFile(b"content", self.media_type, name)


class _Tasks:
    def list(self) -> tuple[StudyTask, ...]:
        def task(task_id: int, status: StudyTaskStatus, course: str | None) -> StudyTask:
            return StudyTask(
                id=task_id,
                title=f"Task {task_id}",
                status=status,
                priority=StudyTaskPriority.NORMAL,
                course_id=9 if course else None,
                course_title=course,
                assignment_id=None,
                assignment_title=None,
                due_at=None,
                created_at=NOW,
                updated_at=NOW,
            )

        return (
            task(1, StudyTaskStatus.OPEN, None),
            task(2, StudyTaskStatus.COMPLETED, None),
            task(3, StudyTaskStatus.OPEN, "AI445 · Natural Language Processing"),
        )


def _client(**readers: object) -> TestClient:
    app = FastAPI()
    install_error_boundary(app)
    app.include_router(create_api_v1_router(_Courses(), **readers))  # type: ignore[arg-type]
    return TestClient(app)


def test_a_pdf_opens_in_the_browser_and_a_word_file_downloads() -> None:
    pdf = _client(document_file_reader=_Files("application/pdf")).get("/api/v1/documents/5/file")
    word_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    word = _client(document_file_reader=_Files(word_type)).get("/api/v1/documents/5/file")

    assert pdf.status_code == 200
    assert pdf.content == b"content"
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.headers["content-disposition"].startswith('inline; filename="Notes.pdf"')
    assert pdf.headers["x-content-type-options"] == "nosniff"
    assert word.headers["content-disposition"].startswith('attachment; filename="Notes.docx"')


def test_study_tasks_are_listed_open_first_newest_first() -> None:
    response = _client(study_task_reader=_Tasks()).get("/api/v1/study-tasks")

    assert response.status_code == 200
    tasks = response.json()["study_tasks"]
    assert [task["id"] for task in tasks] == [3, 1, 2]
    assert tasks[0]["course_title"] == "AI445 · Natural Language Processing"


def test_citation_evidence_is_original_version_text_and_private(
    sessions: sessionmaker[Session], tmp_path: Path
) -> None:
    from aaos.persistence.document_models import DocumentPageORM

    _seed(sessions, tmp_path)
    with sessions() as session, session.begin():
        session.add(
            DocumentPageORM(
                document_version_id=31,
                page_number=1,
                text="Original source passage.",
                text_source="EMBEDDED_PDF",
                width_points=600,
                height_points=800,
                created_at=NOW,
            )
        )
    reader = DocumentFileService(sessions, tmp_path)
    with _client(evidence_reader=reader) as client:
        response = client.get("/api/v1/document-versions/31/evidence?start=1&end=1")
        file = client.get("/api/v1/document-versions/31/file")
        missing = client.get("/api/v1/document-versions/999/evidence?start=1&end=1")
        invalid = client.get("/api/v1/document-versions/31/evidence?start=2&end=1")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "document_version_id": 31,
        "document_title": "Intro to KNIME.pdf",
        "course_title": "AI380",
        "location_kind": "PAGE",
        "start": 1,
        "end": 1,
        "excerpts": ["Original source passage."],
        "truncated": False,
    }
    assert file.content == PDF_BYTES
    assert missing.status_code == 404
    assert invalid.status_code == 422
    assert "storage_key" not in response.text


@pytest.mark.parametrize("hidden_course", [False, True])
def test_citation_evidence_rechecks_visibility(
    sessions: sessionmaker[Session], tmp_path: Path, hidden_course: bool
) -> None:
    _seed(sessions, tmp_path, remote_active=hidden_course)
    if hidden_course:
        with sessions() as session, session.begin():
            course = session.get(CourseORM, 1)
            assert course is not None
            course.is_active = False
    reader = DocumentFileService(sessions, tmp_path)
    with _client(evidence_reader=reader) as client:
        assert client.get("/api/v1/document-versions/31/evidence?start=1&end=1").status_code == 404
        assert client.get("/api/v1/document-versions/31/file").status_code == 404


def test_evidence_handles_missing_text_and_bounds_original_context(
    sessions: sessionmaker[Session], tmp_path: Path
) -> None:
    from aaos.persistence.document_models import DocumentPageORM

    _seed(sessions, tmp_path)
    reader = DocumentFileService(sessions, tmp_path)
    assert reader.read_evidence(31, 1, 1).excerpts == ()
    with sessions() as session, session.begin():
        session.add(
            DocumentPageORM(
                document_version_id=31,
                page_number=1,
                text="x" * 17000,
                text_source="EMBEDDED_PDF",
                width_points=600,
                height_points=800,
                created_at=NOW,
            )
        )
    evidence = reader.read_evidence(31, 1, 1)
    assert evidence.truncated
    assert evidence.excerpts == ("x" * 16000,)
    wide_range = reader.read_evidence(31, 1, 100)
    assert wide_range.end == 100
    assert wide_range.truncated
    assert wide_range.excerpts == ("x" * 16000,)


def test_cited_file_stays_on_its_version_after_an_update(
    sessions: sessionmaker[Session], tmp_path: Path
) -> None:
    _seed(sessions, tmp_path)
    updated = b"%PDF-1.4 replacement notes"
    digest = hashlib.sha256(updated).hexdigest()
    key = f"sha256/{digest[:2]}/{digest}.pdf"
    target = tmp_path / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(updated)
    with sessions() as session, session.begin():
        session.add(
            ContentBlobORM(
                id=22,
                sha256=digest,
                size_bytes=len(updated),
                media_type="application/pdf",
                storage_key=key,
                created_at=NOW,
            )
        )
        session.flush()
        session.add(
            DocumentVersionORM(
                id=32,
                document_id=5,
                version_number=2,
                content_blob_id=22,
                source_filename="updated.pdf",
                ingested_at=NOW,
                extraction_status="SUCCESS",
                page_count=1,
            )
        )
    reader = DocumentFileService(sessions, tmp_path)
    assert reader.open_document_file(5).body == updated
    assert reader.open_document_version_file(31).body == PDF_BYTES


def test_structural_evidence_reads_units_without_inventing_pages(
    sessions: sessionmaker[Session], tmp_path: Path
) -> None:
    from aaos.persistence.document_models import DocumentStructureUnitORM

    _seed(sessions, tmp_path)
    with sessions() as session, session.begin():
        version = session.get(DocumentVersionORM, 31)
        assert version is not None
        version.provenance_mode = "STRUCTURE"
        version.page_count = None
        session.add(
            DocumentStructureUnitORM(
                document_version_id=31,
                ordinal=3,
                kind="PARAGRAPH",
                text="Original paragraph.",
                created_at=NOW,
            )
        )
    result = DocumentFileService(sessions, tmp_path).read_evidence(31, 3, 3)
    assert result.location_kind == "STRUCTURE"
    assert result.excerpts == ("Original paragraph.",)


def test_wide_citation_range_marks_omitted_locations_even_with_short_text(
    sessions: sessionmaker[Session], tmp_path: Path
) -> None:
    from aaos.persistence.document_models import DocumentPageORM

    _seed(sessions, tmp_path)
    with sessions() as session, session.begin():
        session.add(
            DocumentPageORM(
                document_version_id=31,
                page_number=1,
                text="Short text.",
                text_source="EMBEDDED_PDF",
                width_points=600,
                height_points=800,
                created_at=NOW,
            )
        )
    reader = DocumentFileService(sessions, tmp_path)
    assert not reader.read_evidence(31, 1, 1).truncated
    wide = reader.read_evidence(31, 1, 21)
    assert wide.excerpts == ("Short text.",)
    assert wide.end == 21
    assert wide.truncated
