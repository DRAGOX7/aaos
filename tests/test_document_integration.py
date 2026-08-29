"""Disposable end-to-end verification for Phase 3 document ingestion."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from pdf_fixtures import html_as_pdf, valid_multipage_pdf, valid_one_page_pdf
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

import aaos.persistence.database as database_module
from aaos.academic.normalization import normalize_collection
from aaos.academic.source import CompleteSourceSnapshot, RawCourse, RawRemoteFile
from aaos.academic.sync import AcademicSyncService
from aaos.documents.contracts import (
    BlobInstall,
    DocumentIngestionFailure,
    DocumentResultCode,
    ExtractionStatus,
    FetchedPdf,
    IngestionErrorCode,
    IngestionMode,
    IngestionStatus,
    ParsedPdf,
    RemoteFileSelection,
)
from aaos.documents.lock import IngestionLock
from aaos.documents.pdf import PdfProcessor
from aaos.documents.repository import DocumentRepository, PersistedVersion
from aaos.documents.service import Phase3IngestionService
from aaos.documents.storage import ContentBlobStore
from aaos.moodle.file_download import BrowserAuthenticatedFetcher
from aaos.persistence.academic_models import CourseORM, RemoteFileORM
from aaos.persistence.database import create_database_engine, create_session_factory
from aaos.persistence.document_models import (
    ContentBlobORM,
    DocumentORM,
    DocumentPageORM,
    DocumentVersionORM,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NORMAL_DATABASE = (PROJECT_ROOT / "data/database/aaos.db").resolve()
NORMAL_DOCUMENTS = (PROJECT_ROOT / "data/documents").resolve()
SOURCE_SYSTEM = "mock:phase3:integration"
NOW = datetime(2026, 8, 28, 9, tzinfo=timezone.utc)
SessionFactory = Callable[[], Session]


def _alembic_config(database_path: Path) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("aaos.database_path", str(database_path))
    return config


@pytest.fixture(autouse=True)
def guard_normal_paths(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Fail immediately if disposable integration resolves either normal path."""
    assert not NORMAL_DATABASE.exists()
    assert not NORMAL_DOCUMENTS.exists()
    original_resolve = database_module.resolve_database_path
    original_store_init = ContentBlobStore.__init__

    def guarded_database_path(database_path: str | Path = NORMAL_DATABASE) -> Path:
        resolved = original_resolve(database_path)
        if resolved == NORMAL_DATABASE:
            raise AssertionError("normal database path accessed")
        return resolved

    def guarded_store_init(store: ContentBlobStore, root: Path) -> None:
        if Path(root).resolve() == NORMAL_DOCUMENTS:
            raise AssertionError("normal document store accessed")
        original_store_init(store, root)

    monkeypatch.setattr(database_module, "resolve_database_path", guarded_database_path)
    monkeypatch.setattr(ContentBlobStore, "__init__", guarded_store_init)
    yield
    assert not NORMAL_DATABASE.exists()
    assert not NORMAL_DOCUMENTS.exists()


@dataclass
class FakeAuthorizedFetcher(BrowserAuthenticatedFetcher):
    outcomes: dict[int, list[FetchedPdf]] = field(default_factory=dict)
    calls: list[int] = field(default_factory=list)

    def fetch(self, selection: RemoteFileSelection) -> FetchedPdf:
        self.calls.append(selection.remote_file_id)
        return self.outcomes[selection.remote_file_id].pop(0)


class FakeFetcherSession(AbstractContextManager[BrowserAuthenticatedFetcher]):
    def __init__(self, fetcher: FakeAuthorizedFetcher) -> None:
        self._fetcher = fetcher

    def __enter__(self) -> BrowserAuthenticatedFetcher:
        return self._fetcher

    def __exit__(self, *_args: object) -> None:
        return None


@dataclass
class IntegrationHarness:
    database_path: Path
    content_root: Path
    engine: Engine
    sessions: SessionFactory
    repository: DocumentRepository
    store: ContentBlobStore
    fetcher: FakeAuthorizedFetcher
    service: Phase3IngestionService


@contextmanager
def integration_harness(tmp_path: Path) -> Iterator[IntegrationHarness]:
    database_path = (tmp_path / "database" / "phase3.db").resolve()
    content_root = (tmp_path / "content-store").resolve()
    lock_path = (tmp_path / "locks" / "phase3.lock").resolve()
    command.upgrade(_alembic_config(database_path), "0003_phase3_document_ingestion")
    engine = create_database_engine(database_path)
    with engine.connect() as connection:
        assert (
            MigrationContext.configure(connection).get_current_revision()
            == "0003_phase3_document_ingestion"
        )
    sessions = create_session_factory(engine)
    repository = DocumentRepository(sessions)
    store = ContentBlobStore(content_root)
    fetcher = FakeAuthorizedFetcher()

    def fetcher_factory() -> AbstractContextManager[BrowserAuthenticatedFetcher]:
        return FakeFetcherSession(fetcher)

    service = Phase3IngestionService(
        repository=repository,
        blob_store=store,
        pdf_processor=PdfProcessor(),
        ingestion_lock=IngestionLock(lock_path),
        fetcher_factory=fetcher_factory,
        clock=lambda: NOW,
    )
    try:
        yield IntegrationHarness(
            database_path=database_path,
            content_root=content_root,
            engine=engine,
            sessions=sessions,
            repository=repository,
            store=store,
            fetcher=fetcher,
            service=service,
        )
    finally:
        engine.dispose()


def sync_remote_files(
    sessions: SessionFactory,
    source_ids: tuple[int, ...],
) -> dict[int, int]:
    collection = CompleteSourceSnapshot(
        source_system=SOURCE_SYSTEM,
        courses=(RawCourse(source_id="course-1", title="Course"),),
        remote_files=tuple(
            RawRemoteFile(
                source_id=f"resource-cm:{source_id}",
                course_source_id="course-1",
                title=f"Document {source_id}",
            )
            for source_id in source_ids
        ),
    )
    AcademicSyncService(sessions).sync(normalize_collection(collection))
    with sessions() as session:
        rows = session.scalars(
            select(RemoteFileORM).where(RemoteFileORM.source_system == SOURCE_SYSTEM)
        )
        return {int(row.source_id.rsplit(":", maxsplit=1)[1]): row.id for row in rows}


def fetched(body: bytes, filename: str = "unsafe/lecture.pdf") -> FetchedPdf:
    return FetchedPdf(
        body=body,
        source_filename=filename,
        advisory_media_type="application/pdf",
    )


def assert_no_temporary_artifacts(content_root: Path) -> None:
    temporary_root = content_root / ".temporary"
    assert not temporary_root.exists() or tuple(temporary_root.iterdir()) == ()


def test_ingest_refresh_and_versioning_preserve_provenance(tmp_path: Path) -> None:
    first_pdf = valid_multipage_pdf()
    changed_pdf = valid_one_page_pdf()
    with integration_harness(tmp_path) as test:
        remote_file_id = sync_remote_files(test.sessions, (101,))[101]
        test.fetcher.outcomes[remote_file_id] = [
            fetched(first_pdf),
            fetched(first_pdf),
            fetched(changed_pdf, "revised.pdf"),
        ]

        first = test.service.run(mode=IngestionMode.INGEST_NEW, source_system=SOURCE_SYSTEM)
        assert (
            first.remote_files_selected,
            first.ingested,
            first.unchanged,
            first.failed,
            first.new_versions,
            first.deduplicated_blobs,
        ) == (1, 1, 0, 0, 1, 0)
        assert first.documents[0].code is DocumentResultCode.INGESTED
        assert first.documents[0].version_number == 1
        assert first.documents[0].page_count == 3
        assert first.documents[0].extraction_status is ExtractionStatus.SUCCESS

        first_digest = hashlib.sha256(first_pdf).hexdigest()
        expected_key = f"sha256/{first_digest[:2]}/{first_digest}.pdf"
        with test.sessions() as session:
            document = session.scalars(select(DocumentORM)).one()
            blob = session.scalars(select(ContentBlobORM)).one()
            version = session.scalars(select(DocumentVersionORM)).one()
            pages = tuple(session.scalars(select(DocumentPageORM).order_by(DocumentPageORM.id)))
            provenance = tuple(
                session.execute(
                    select(
                        DocumentPageORM.id,
                        DocumentVersionORM.id,
                        DocumentORM.id,
                        RemoteFileORM.id,
                        CourseORM.id,
                    )
                    .join(
                        DocumentVersionORM,
                        DocumentVersionORM.id == DocumentPageORM.document_version_id,
                    )
                    .join(DocumentORM, DocumentORM.id == DocumentVersionORM.document_id)
                    .join(RemoteFileORM, RemoteFileORM.id == DocumentORM.remote_file_id)
                    .join(CourseORM, CourseORM.id == RemoteFileORM.course_id)
                )
            )
            assert document.remote_file_id == remote_file_id
            assert document.ingestion_status == IngestionStatus.READY.value
            assert blob.sha256 == first_digest
            assert blob.storage_key == expected_key
            assert not Path(blob.storage_key).is_absolute()
            assert version.source_filename == "lecture.pdf"
            assert version.version_number == 1
            assert version.page_count == 3
            assert len(pages) == 3
            assert len(provenance) == 3
            assert all(
                row[2:] == (document.id, remote_file_id, provenance[0][4]) for row in provenance
            )
        assert (test.content_root / Path(*expected_key.split("/"))).read_bytes() == first_pdf

        unchanged = test.service.run(
            mode=IngestionMode.REFRESH,
            source_system=SOURCE_SYSTEM,
            remote_file_id=remote_file_id,
        )
        assert unchanged.documents[0].code is DocumentResultCode.UNCHANGED
        assert unchanged.documents[0].version_number == 1
        assert (unchanged.unchanged, unchanged.new_versions) == (1, 0)
        with test.sessions() as session:
            assert session.scalar(select(func.count()).select_from(DocumentVersionORM)) == 1
            assert session.scalar(select(func.count()).select_from(ContentBlobORM)) == 1
            assert session.scalar(select(func.count()).select_from(DocumentPageORM)) == 3

        changed = test.service.run(
            mode=IngestionMode.REFRESH,
            source_system=SOURCE_SYSTEM,
            remote_file_id=remote_file_id,
        )
        assert changed.documents[0].code is DocumentResultCode.INGESTED
        assert changed.documents[0].version_number == 2
        assert (changed.ingested, changed.new_versions) == (1, 1)
        changed_digest = hashlib.sha256(changed_pdf).hexdigest()
        with test.sessions() as session:
            versions = tuple(
                session.execute(
                    select(DocumentVersionORM, ContentBlobORM)
                    .join(
                        ContentBlobORM,
                        ContentBlobORM.id == DocumentVersionORM.content_blob_id,
                    )
                    .order_by(DocumentVersionORM.version_number)
                )
            )
            assert tuple(version.version_number for version, _blob in versions) == (1, 2)
            assert tuple(version.page_count for version, _blob in versions) == (3, 1)
            assert tuple(version.source_filename for version, _blob in versions) == (
                "lecture.pdf",
                "revised.pdf",
            )
            assert tuple(blob.sha256 for _version, blob in versions) == (
                first_digest,
                changed_digest,
            )
            assert session.scalar(select(func.count()).select_from(DocumentORM)) == 1
            assert session.scalar(select(func.count()).select_from(ContentBlobORM)) == 2
            assert session.scalar(select(func.count()).select_from(DocumentPageORM)) == 4
        assert_no_temporary_artifacts(test.content_root)


def test_identical_bytes_deduplicate_globally_without_merging_documents(tmp_path: Path) -> None:
    shared_pdf = valid_one_page_pdf()
    with integration_harness(tmp_path) as test:
        remote_ids = sync_remote_files(test.sessions, (201, 202))
        for remote_file_id in remote_ids.values():
            test.fetcher.outcomes[remote_file_id] = [fetched(shared_pdf)]

        result = test.service.run(mode=IngestionMode.INGEST_NEW, source_system=SOURCE_SYSTEM)
        assert (result.ingested, result.new_versions, result.deduplicated_blobs) == (2, 2, 1)
        assert tuple(item.code for item in result.documents) == (
            DocumentResultCode.INGESTED,
            DocumentResultCode.INGESTED,
        )
        assert tuple(item.deduplicated for item in result.documents) == (False, True)
        with test.sessions() as session:
            documents = tuple(session.scalars(select(DocumentORM).order_by(DocumentORM.id)))
            versions = tuple(
                session.scalars(select(DocumentVersionORM).order_by(DocumentVersionORM.id))
            )
            assert len(documents) == 2
            assert {document.remote_file_id for document in documents} == set(remote_ids.values())
            assert len(versions) == 2
            assert versions[0].content_blob_id == versions[1].content_blob_id
            assert session.scalar(select(func.count()).select_from(ContentBlobORM)) == 1


def test_inactive_remote_file_preserves_history_and_is_not_selected(tmp_path: Path) -> None:
    with integration_harness(tmp_path) as test:
        remote_file_id = sync_remote_files(test.sessions, (301,))[301]
        test.fetcher.outcomes[remote_file_id] = [fetched(valid_one_page_pdf())]
        first = test.service.run(mode=IngestionMode.INGEST_NEW, source_system=SOURCE_SYSTEM)
        assert first.ingested == 1

        sync_remote_files(test.sessions, ())
        calls_before = tuple(test.fetcher.calls)
        inactive = test.service.run(mode=IngestionMode.INGEST_NEW, source_system=SOURCE_SYSTEM)
        assert inactive.remote_files_selected == 0
        assert tuple(test.fetcher.calls) == calls_before
        with test.sessions() as session:
            remote_file = session.get(RemoteFileORM, remote_file_id)
            assert remote_file is not None
            assert remote_file.is_active is False
            assert session.scalar(select(func.count()).select_from(DocumentORM)) == 1
            assert session.scalar(select(func.count()).select_from(DocumentVersionORM)) == 1
            assert session.scalar(select(func.count()).select_from(ContentBlobORM)) == 1
            assert session.scalar(select(func.count()).select_from(DocumentPageORM)) == 1


def test_three_item_batch_isolates_invalid_pdf_and_cleans_temporary_state(
    tmp_path: Path,
) -> None:
    with integration_harness(tmp_path) as test:
        remote_ids = sync_remote_files(test.sessions, (401, 402, 403))
        bodies = {
            401: valid_one_page_pdf(),
            402: html_as_pdf(),
            403: valid_multipage_pdf(),
        }
        for source_id, remote_file_id in remote_ids.items():
            test.fetcher.outcomes[remote_file_id] = [fetched(bodies[source_id])]

        result = test.service.run(mode=IngestionMode.INGEST_NEW, source_system=SOURCE_SYSTEM)
        assert tuple(item.code for item in result.documents) == (
            DocumentResultCode.INGESTED,
            DocumentResultCode.FAILED,
            DocumentResultCode.INGESTED,
        )
        assert result.documents[1].error_code is IngestionErrorCode.INVALID_PDF
        assert (result.ingested, result.failed, result.new_versions, result.exit_code) == (
            2,
            1,
            2,
            1,
        )
        with test.sessions() as session:
            documents = {row.remote_file_id: row for row in session.scalars(select(DocumentORM))}
            assert documents[remote_ids[401]].ingestion_status == IngestionStatus.READY.value
            assert documents[remote_ids[402]].ingestion_status == IngestionStatus.FAILED.value
            assert documents[remote_ids[403]].ingestion_status == IngestionStatus.READY.value
            failed_document_id = documents[remote_ids[402]].id
            failed_versions = session.scalar(
                select(func.count())
                .select_from(DocumentVersionORM)
                .where(DocumentVersionORM.document_id == failed_document_id)
            )
            assert failed_versions == 0
            assert session.scalar(select(func.count()).select_from(DocumentVersionORM)) == 2
            assert session.scalar(select(func.count()).select_from(ContentBlobORM)) == 2
        invalid_digest = hashlib.sha256(bodies[402]).hexdigest()
        invalid_path = test.content_root / "sha256" / invalid_digest[:2] / f"{invalid_digest}.pdf"
        assert not invalid_path.exists()
        assert_no_temporary_artifacts(test.content_root)


def test_persistence_failure_compensates_new_blob_but_preserves_shared_blob(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unique_pdf = valid_multipage_pdf()
    shared_pdf = valid_one_page_pdf()
    with integration_harness(tmp_path) as test:
        remote_ids = sync_remote_files(test.sessions, (501, 502, 503))
        test.fetcher.outcomes[remote_ids[501]] = [fetched(unique_pdf)]
        test.fetcher.outcomes[remote_ids[502]] = [fetched(shared_pdf)]
        test.fetcher.outcomes[remote_ids[503]] = [fetched(shared_pdf)]
        failing_ids = {remote_ids[501]}
        original_persist = test.repository.persist_success

        def fail_selected_persistence(
            *,
            selection: RemoteFileSelection,
            blob: BlobInstall,
            source_filename: str,
            parsed_pdf: ParsedPdf,
            attempted_at: datetime,
        ) -> PersistedVersion:
            if selection.remote_file_id in failing_ids:
                raise DocumentIngestionFailure(IngestionErrorCode.PERSISTENCE_FAILED)
            return original_persist(
                selection=selection,
                blob=blob,
                source_filename=source_filename,
                parsed_pdf=parsed_pdf,
                attempted_at=attempted_at,
            )

        monkeypatch.setattr(test.repository, "persist_success", fail_selected_persistence)

        new_failure = test.service.run(
            mode=IngestionMode.REFRESH,
            source_system=SOURCE_SYSTEM,
            remote_file_id=remote_ids[501],
        )
        assert new_failure.documents[0].error_code is IngestionErrorCode.PERSISTENCE_FAILED
        unique_digest = hashlib.sha256(unique_pdf).hexdigest()
        unique_path = test.content_root / "sha256" / unique_digest[:2] / f"{unique_digest}.pdf"
        assert not unique_path.exists()

        failing_ids.clear()
        shared_success = test.service.run(
            mode=IngestionMode.REFRESH,
            source_system=SOURCE_SYSTEM,
            remote_file_id=remote_ids[502],
        )
        assert shared_success.documents[0].code is DocumentResultCode.INGESTED
        shared_digest = hashlib.sha256(shared_pdf).hexdigest()
        shared_path = test.content_root / "sha256" / shared_digest[:2] / f"{shared_digest}.pdf"
        assert shared_path.read_bytes() == shared_pdf

        failing_ids.add(remote_ids[503])
        shared_failure = test.service.run(
            mode=IngestionMode.REFRESH,
            source_system=SOURCE_SYSTEM,
            remote_file_id=remote_ids[503],
        )
        assert shared_failure.documents[0].error_code is IngestionErrorCode.PERSISTENCE_FAILED
        assert shared_path.read_bytes() == shared_pdf
        with test.sessions() as session:
            assert session.scalar(select(func.count()).select_from(ContentBlobORM)) == 1
            assert session.scalar(select(func.count()).select_from(DocumentVersionORM)) == 1
        assert_no_temporary_artifacts(test.content_root)
