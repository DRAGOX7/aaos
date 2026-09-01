"""Acceptance-only local corpus bridge into the frozen Phase 3 pipeline."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pdf_fixtures import valid_one_page_pdf

from aaos.documents.contracts import (
    DocumentIngestionFailure,
    FetchedPdf,
    IngestionErrorCode,
    RemoteFileSelection,
)
from aaos.rag.evaluation.local_phase3 import (
    ACCEPTANCE_SOURCE_SYSTEM,
    LocalAcceptanceCorpusError,
    LocalAcceptancePdfFetcher,
    LocalCorpusFile,
    build_local_acceptance_snapshot,
    copy_local_corpus,
    ingest_local_corpus,
)


def _entry(root: Path, body: bytes) -> LocalCorpusFile:
    relative = Path("Course") / "lecture.pdf"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()
    return LocalCorpusFile(
        source_id=f"local-pdf:{'0' * 16}:{digest}",
        course_source_id="local-course:course",
        title="lecture.pdf",
        relative_path="Course/lecture.pdf",
        sha256=digest,
        size_bytes=len(body),
    )


def _selection(entry: LocalCorpusFile) -> RemoteFileSelection:
    return RemoteFileSelection(
        remote_file_id=1,
        course_id=7,
        source_system="local:phase4:acceptance",
        source_id=entry.source_id,
        title="lecture.pdf",
    )


def test_local_fetcher_returns_the_existing_fetched_pdf_contract(tmp_path: Path) -> None:
    body = valid_one_page_pdf()
    entry = _entry(tmp_path, body)
    fetcher = LocalAcceptancePdfFetcher(
        corpus_root=tmp_path,
        entries={entry.source_id: entry},
        course_ids={entry.course_source_id: 7},
        source_system="local:phase4:acceptance",
    )

    fetched = fetcher.fetch(_selection(entry))

    assert isinstance(fetched, FetchedPdf)
    assert fetched.body == body
    assert fetched.source_filename == "lecture.pdf"
    assert fetched.advisory_media_type == "application/pdf"


def test_local_fetcher_rejects_unknown_selection(tmp_path: Path) -> None:
    body = valid_one_page_pdf()
    entry = _entry(tmp_path, body)
    unknown = RemoteFileSelection(
        remote_file_id=1,
        course_id=7,
        source_system="local:phase4:acceptance",
        source_id="local-pdf:" + "f" * 16 + ":" + "f" * 64,
        title=entry.title,
    )
    fetcher = LocalAcceptancePdfFetcher(
        corpus_root=tmp_path,
        entries={entry.source_id: entry},
        course_ids={entry.course_source_id: 7},
        source_system="local:phase4:acceptance",
    )

    with pytest.raises(DocumentIngestionFailure) as raised:
        fetcher.fetch(unknown)

    assert raised.value.code is IngestionErrorCode.UNAUTHORIZED_ROUTE


def test_local_fetcher_rejects_path_traversal_manifest(tmp_path: Path) -> None:
    entry = LocalCorpusFile(
        source_id=f"local-pdf:{'0' * 16}:{'0' * 64}",
        course_source_id="local-course:course",
        title="lecture.pdf",
        relative_path="../outside.pdf",
        sha256="0" * 64,
        size_bytes=1,
    )

    with pytest.raises(DocumentIngestionFailure) as raised:
        LocalAcceptancePdfFetcher(
            corpus_root=tmp_path,
            entries={entry.source_id: entry},
            course_ids={entry.course_source_id: 7},
            source_system="local:phase4:acceptance",
        )

    assert raised.value.code is IngestionErrorCode.UNAUTHORIZED_ROUTE


def test_local_fetcher_rejects_copy_hash_mismatch(tmp_path: Path) -> None:
    body = valid_one_page_pdf()
    entry = _entry(tmp_path, body)
    (tmp_path / entry.relative_path).write_bytes(b"changed")
    fetcher = LocalAcceptancePdfFetcher(
        corpus_root=tmp_path,
        entries={entry.source_id: entry},
        course_ids={entry.course_source_id: 7},
        source_system="local:phase4:acceptance",
    )

    with pytest.raises(DocumentIngestionFailure) as raised:
        fetcher.fetch(_selection(entry))

    assert raised.value.code is IngestionErrorCode.STORAGE_FAILED


def test_local_snapshot_uses_deterministic_course_and_source_identity(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "Course").mkdir(parents=True)
    (source / "Course" / "lecture.pdf").write_bytes(valid_one_page_pdf())

    first = build_local_acceptance_snapshot(source)
    second = build_local_acceptance_snapshot(source)

    assert first == second
    assert first.source_system == "local:phase4:acceptance"
    assert len(first.courses) == 1
    assert len(first.remote_files) == 1
    assert first.remote_files[0].course_source_id == first.courses[0].source_id
    assert first.courses[0].title == "Course"


def test_copy_local_corpus_preserves_relative_path_and_hash(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "copy"
    (source / "Course").mkdir(parents=True)
    body = valid_one_page_pdf()
    (source / "Course" / "lecture.pdf").write_bytes(body)

    manifest = copy_local_corpus(source, destination)

    copied = destination / "Course" / "lecture.pdf"
    assert copied.read_bytes() == body
    assert manifest.entries[0].relative_path == "Course/lecture.pdf"
    assert manifest.entries[0].sha256 == hashlib.sha256(body).hexdigest()


def test_copy_rejects_unexpected_preexisting_file(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "copy"
    (source / "Course").mkdir(parents=True)
    (source / "Course" / "lecture.pdf").write_bytes(valid_one_page_pdf())
    destination.mkdir()
    (destination / "unexpected.txt").write_text("not part of the manifest")

    with pytest.raises(LocalAcceptanceCorpusError):
        copy_local_corpus(source, destination)


def test_copy_rejects_source_non_pdf_and_preserves_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "copy"
    (source / "Course").mkdir(parents=True)
    original = b"source marker"
    (source / "Course" / "notes.txt").write_bytes(original)

    with pytest.raises(LocalAcceptanceCorpusError):
        copy_local_corpus(source, destination)
    assert (source / "Course" / "notes.txt").read_bytes() == original


def test_acceptance_root_cannot_be_normal_data_path(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "Course").mkdir(parents=True)
    (source / "Course" / "lecture.pdf").write_bytes(valid_one_page_pdf())
    normal_data = Path(__file__).resolve().parents[1] / "data"

    with pytest.raises(LocalAcceptanceCorpusError):
        ingest_local_corpus(source_root=source, acceptance_root=normal_data / "acceptance")


def test_local_fetcher_rejects_wrong_namespace_and_course_binding(tmp_path: Path) -> None:
    body = valid_one_page_pdf()
    entry = _entry(tmp_path, body)
    fetcher = LocalAcceptancePdfFetcher(
        corpus_root=tmp_path,
        entries={entry.source_id: entry},
        course_ids={entry.course_source_id: 7},
    )

    wrong_namespace = RemoteFileSelection(
        remote_file_id=1,
        course_id=7,
        source_system="other:namespace",
        source_id=entry.source_id,
        title=entry.title,
    )
    wrong_course = RemoteFileSelection(
        remote_file_id=1,
        course_id=8,
        source_system=ACCEPTANCE_SOURCE_SYSTEM,
        source_id=entry.source_id,
        title=entry.title,
    )
    for selection in (wrong_namespace, wrong_course):
        with pytest.raises(DocumentIngestionFailure) as raised:
            fetcher.fetch(selection)
        assert raised.value.code is IngestionErrorCode.UNAUTHORIZED_ROUTE


def test_local_fetcher_rejects_oversize_manifest_before_read(tmp_path: Path) -> None:
    body = valid_one_page_pdf()
    entry = _entry(tmp_path, body)
    oversized = LocalCorpusFile(
        source_id=entry.source_id,
        course_source_id=entry.course_source_id,
        title=entry.title,
        relative_path=entry.relative_path,
        sha256=entry.sha256,
        size_bytes=52_428_801,
    )

    with pytest.raises(LocalAcceptanceCorpusError):
        LocalAcceptancePdfFetcher(
            corpus_root=tmp_path,
            entries={oversized.source_id: oversized},
            course_ids={entry.course_source_id: 7},
        )


def test_local_snapshot_preserves_course_isolation(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "Course A").mkdir(parents=True)
    (source / "Course B").mkdir(parents=True)
    body = valid_one_page_pdf()
    (source / "Course A" / "a.pdf").write_bytes(body)
    (source / "Course B" / "b.pdf").write_bytes(body + b"different")

    snapshot = build_local_acceptance_snapshot(source)

    assert {course.title for course in snapshot.courses} == {"Course A", "Course B"}
    assert {remote.course_source_id for remote in snapshot.remote_files} == {
        course.source_id for course in snapshot.courses
    }
    assert len(snapshot.remote_files) == 2


def test_invalid_pdf_stays_owned_by_phase3_validation(tmp_path: Path) -> None:
    source = tmp_path / "source"
    acceptance = tmp_path / "acceptance"
    (source / "Course").mkdir(parents=True)
    (source / "Course" / "invalid.pdf").write_bytes(b"not a PDF")

    result = ingest_local_corpus(source_root=source, acceptance_root=acceptance)

    assert result.course_count == 1
    assert result.document_count == 1
    assert result.document_version_count == 0
    assert result.page_count == 0
    assert result.success_count == 0
    assert result.failed_count == 1


def test_ingest_local_corpus_uses_phase3_validation_and_disposable_paths(tmp_path: Path) -> None:
    source = tmp_path / "source"
    acceptance = tmp_path / "acceptance"
    (source / "Course").mkdir(parents=True)
    (source / "Course" / "lecture.pdf").write_bytes(valid_one_page_pdf())

    result = ingest_local_corpus(source_root=source, acceptance_root=acceptance)

    assert result.course_count == 1
    assert result.document_count == 1
    assert result.document_version_count == 1
    assert result.page_count == 1
    assert result.success_count == 1
    assert result.failed_count == 0
    assert result.database_path == acceptance / "database" / "acceptance.sqlite3"
    assert result.document_store_root == acceptance / "documents"
