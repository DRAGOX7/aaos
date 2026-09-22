from __future__ import annotations

import hashlib
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import pytest

from aaos.documents.contracts import (
    MAX_TRANSIENT_RETRIES,
    BlobInstall,
    DocumentIngestionFailure,
    DocumentResultCode,
    ExtractedPage,
    ExtractionStatus,
    FetchedPdf,
    IngestionErrorCode,
    IngestionMode,
    ParsedPdf,
    RemoteFileSelection,
    TextSource,
)
from aaos.documents.lock import IngestionLockUnavailable
from aaos.documents.repository import PersistedVersion, SelectionPlan
from aaos.documents.service import Phase3IngestionService
from aaos.documents.storage import StagedBlob

NOW = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
SOURCE = "moodle:example:phase3"


def selected(remote_file_id: int) -> RemoteFileSelection:
    return RemoteFileSelection(
        remote_file_id=remote_file_id,
        course_id=100 + remote_file_id,
        source_system=SOURCE,
        source_id=f"resource-cm:{200 + remote_file_id}",
        title=f"Resource {remote_file_id}",
    )


def fetched(body: bytes) -> FetchedPdf:
    return FetchedPdf(body=body, source_filename="unsafe/lecture.pdf", advisory_media_type=None)


def parsed() -> ParsedPdf:
    return ParsedPdf(
        page_count=1,
        extraction_status=ExtractionStatus.SUCCESS,
        pages=(
            ExtractedPage(
                page_number=1,
                text="safe text",
                text_source=TextSource.EMBEDDED_PDF,
                width_points=612.0,
                height_points=792.0,
            ),
        ),
    )


@dataclass
class FakeRepository:
    plan: SelectionPlan
    events: list[str]
    histories: dict[int, dict[str, int]] = field(default_factory=dict)
    blob_ids: dict[str, int] = field(default_factory=dict)
    persist_failures: set[int] = field(default_factory=set)
    record_failure_fails: set[int] = field(default_factory=set)
    reference_result: bool | DocumentIngestionFailure | None = None
    recorded_failures: list[tuple[int, IngestionErrorCode, datetime]] = field(default_factory=list)

    def plan_selection(
        self, *, mode: IngestionMode, source_system: str, remote_file_id: int | None
    ) -> SelectionPlan:
        self.events.append(f"select:{mode.value}:{source_system}:{remote_file_id}")
        return self.plan

    def existing_version_for_sha256(self, *, remote_file_id: int, sha256: str) -> int | None:
        self.events.append(f"existing:{remote_file_id}")
        return self.histories.get(remote_file_id, {}).get(sha256)

    def persist_success(
        self,
        *,
        selection: RemoteFileSelection,
        blob: BlobInstall,
        source_filename: str,
        parsed_pdf: ParsedPdf,
        attempted_at: datetime,
    ) -> PersistedVersion:
        self.events.append(f"persist:{selection.remote_file_id}")
        assert source_filename == "lecture.pdf"
        assert parsed_pdf.page_count == 1
        assert attempted_at == NOW
        if selection.remote_file_id in self.persist_failures:
            raise DocumentIngestionFailure(IngestionErrorCode.PERSISTENCE_FAILED)
        history = self.histories.setdefault(selection.remote_file_id, {})
        existing = history.get(blob.sha256)
        if existing is not None:
            return PersistedVersion(1000 + selection.remote_file_id, existing, 1, False)
        blob_created = blob.sha256 not in self.blob_ids
        blob_id = self.blob_ids.setdefault(blob.sha256, len(self.blob_ids) + 1)
        version = len(history) + 1
        history[blob.sha256] = version
        return PersistedVersion(
            1000 + selection.remote_file_id,
            version,
            blob_id,
            blob_created,
        )

    def record_failure(
        self,
        *,
        selection: RemoteFileSelection,
        code: IngestionErrorCode,
        attempted_at: datetime,
    ) -> None:
        self.events.append(f"record_failure:{selection.remote_file_id}:{code.value}")
        if selection.remote_file_id in self.record_failure_fails:
            raise DocumentIngestionFailure(IngestionErrorCode.PERSISTENCE_FAILED)
        self.recorded_failures.append((selection.remote_file_id, code, attempted_at))

    def blob_is_referenced(self, *, sha256: str) -> bool:
        self.events.append("reference_check")
        if isinstance(self.reference_result, DocumentIngestionFailure):
            raise self.reference_result
        if self.reference_result is not None:
            return self.reference_result
        return any(sha256 in history for history in self.histories.values())


@dataclass
class FakeBlobStore:
    events: list[str]
    installed: set[str] = field(default_factory=set)
    active_temporary: set[str] = field(default_factory=set)
    stage_failure: DocumentIngestionFailure | None = None
    install_failure: DocumentIngestionFailure | None = None
    discard_failure: DocumentIngestionFailure | None = None
    compensations: list[tuple[str, bool]] = field(default_factory=list)

    def stage(self, body: bytes) -> StagedBlob:
        self.events.append("stage")
        if self.stage_failure is not None:
            raise self.stage_failure
        digest = hashlib.sha256(body).hexdigest()
        self.active_temporary.add(digest)
        return StagedBlob(digest, len(body), Path(f"C:/tmp/{digest}.tmp"))

    def install(self, staged: StagedBlob) -> BlobInstall:
        self.events.append("install")
        if self.install_failure is not None:
            raise self.install_failure
        created = staged.sha256 not in self.installed
        self.installed.add(staged.sha256)
        self.active_temporary.discard(staged.sha256)
        return BlobInstall(
            staged.sha256,
            staged.size_bytes,
            f"sha256/{staged.sha256[:2]}/{staged.sha256}.pdf",
            created,
        )

    def discard_temporary(self, staged: StagedBlob) -> None:
        self.events.append("cleanup")
        if self.discard_failure is not None:
            raise self.discard_failure
        self.active_temporary.discard(staged.sha256)

    def compensate_new_blob(self, install: BlobInstall, *, referenced: bool) -> None:
        self.events.append(f"compensate:{referenced}")
        self.compensations.append((install.sha256, referenced))
        if install.created and not referenced:
            self.installed.discard(install.sha256)


@dataclass
class FakePdfProcessor:
    events: list[str]
    invalid_bodies: set[bytes] = field(default_factory=set)

    def process(self, body: bytes) -> ParsedPdf:
        self.events.append("pdf")
        if body in self.invalid_bodies:
            raise DocumentIngestionFailure(IngestionErrorCode.INVALID_PDF)
        return parsed()


@dataclass
class FakeFetcher:
    events: list[str]
    outcomes: dict[int, list[FetchedPdf | DocumentIngestionFailure]]
    calls: list[int] = field(default_factory=list)
    unsupported: set[int] = field(default_factory=set)

    def supports(self, selection: RemoteFileSelection) -> bool:
        return selection.remote_file_id not in self.unsupported

    def fetch(self, selection: RemoteFileSelection) -> FetchedPdf:
        self.events.append(f"fetch:{selection.remote_file_id}")
        self.calls.append(selection.remote_file_id)
        outcome = self.outcomes[selection.remote_file_id].pop(0)
        if isinstance(outcome, DocumentIngestionFailure):
            raise outcome
        return outcome


class FakeFetcherSession(AbstractContextManager[FakeFetcher]):
    def __init__(self, fetcher: FakeFetcher, events: list[str]) -> None:
        self.fetcher = fetcher
        self.events = events

    def __enter__(self) -> FakeFetcher:
        self.events.append("session_open")
        return self.fetcher

    def __exit__(self, *_args: object) -> None:
        self.events.append("session_close")


@dataclass
class FakeFetcherFactory:
    fetcher: FakeFetcher
    events: list[str]
    opens: int = 0

    def __call__(self) -> FakeFetcherSession:
        self.opens += 1
        return FakeFetcherSession(self.fetcher, self.events)


class FakeLease(AbstractContextManager[None]):
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def __enter__(self) -> None:
        self.events.append("lock_enter")

    def __exit__(self, *_args: object) -> None:
        self.events.append("lock_release")


@dataclass
class FakeLock:
    events: list[str]
    unavailable: bool = False

    def acquire(self) -> FakeLease:
        self.events.append("lock_acquire")
        if self.unavailable:
            raise IngestionLockUnavailable()
        return FakeLease(self.events)


@dataclass
class Harness:
    service: Phase3IngestionService
    repository: FakeRepository
    store: FakeBlobStore
    processor: FakePdfProcessor
    fetcher: FakeFetcher
    factory: FakeFetcherFactory
    lock: FakeLock
    events: list[str]


def harness(
    plan: SelectionPlan,
    outcomes: dict[int, list[FetchedPdf | DocumentIngestionFailure]],
    *,
    clock: Any = lambda: NOW,
) -> Harness:
    events: list[str] = []
    repository = FakeRepository(plan, events)
    store = FakeBlobStore(events)
    processor = FakePdfProcessor(events)
    fetcher = FakeFetcher(events, outcomes)
    factory = FakeFetcherFactory(fetcher, events)
    lock = FakeLock(events)
    service = Phase3IngestionService(
        repository=cast(Any, repository),
        blob_store=cast(Any, store),
        pdf_processor=cast(Any, processor),
        ingestion_lock=cast(Any, lock),
        fetcher_factory=cast(Any, factory),
        clock=clock,
    )
    return Harness(service, repository, store, processor, fetcher, factory, lock, events)


def one_fetch_plan(item: RemoteFileSelection | None = None) -> SelectionPlan:
    item = item or selected(1)
    return SelectionPlan((item,), (item,), ())


def test_successful_pipeline_order_is_lock_scoped_and_sequential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aaos.documents.service as module

    item = selected(1)
    test = harness(one_fetch_plan(item), {1: [fetched(b"pdf-one")]})
    original_sanitize = module.sanitize_source_filename

    def sanitize(value: str | None, *, fallback: str) -> str:
        test.events.append("sanitize")
        return original_sanitize(value, fallback=fallback)

    monkeypatch.setattr(module, "sanitize_source_filename", sanitize)
    result = test.service.run(mode=IngestionMode.INGEST_NEW, source_system=SOURCE)

    assert test.events == [
        "lock_acquire",
        "lock_enter",
        f"select:INGEST_NEW:{SOURCE}:None",
        "session_open",
        "fetch:1",
        "sanitize",
        "stage",
        "pdf",
        "existing:1",
        "install",
        "persist:1",
        "cleanup",
        "session_close",
        "lock_release",
    ]
    assert result.ingested == 1
    assert result.new_versions == 1
    assert result.exit_code == 0


def test_ingest_new_all_skipped_never_opens_a_fetch_session() -> None:
    items = (selected(1), selected(2))
    test = harness(SelectionPlan(items, (), items), {})
    result = test.service.run(mode=IngestionMode.INGEST_NEW, source_system=SOURCE)
    assert test.factory.opens == 0
    assert result.remote_files_selected == 2
    assert result.skipped == 2
    assert tuple(document.code for document in result.documents) == (
        DocumentResultCode.SKIPPED,
        DocumentResultCode.SKIPPED,
    )


def test_transient_timeout_retries_only_fetch_then_succeeds() -> None:
    test = harness(
        one_fetch_plan(),
        {
            1: [
                DocumentIngestionFailure(IngestionErrorCode.NETWORK_TIMEOUT),
                fetched(b"pdf-one"),
            ]
        },
    )
    result = test.service.run(mode=IngestionMode.REFRESH, source_system=SOURCE)
    assert test.fetcher.calls == [1, 1]
    assert result.ingested == 1
    assert len(test.repository.histories[1]) == 1


def test_transient_network_failure_uses_exact_maximum_attempts_then_records_failure() -> None:
    failures: list[FetchedPdf | DocumentIngestionFailure] = [
        DocumentIngestionFailure(IngestionErrorCode.NETWORK_FAILURE)
        for _ in range(MAX_TRANSIENT_RETRIES + 1)
    ]
    test = harness(one_fetch_plan(), {1: failures})
    result = test.service.run(mode=IngestionMode.REFRESH, source_system=SOURCE)
    assert test.fetcher.calls == [1, 1, 1]
    assert result.failed == 1
    assert result.documents[0].error_code is IngestionErrorCode.NETWORK_FAILURE
    assert test.repository.recorded_failures[0][1] is IngestionErrorCode.NETWORK_FAILURE


@pytest.mark.parametrize(
    "code",
    [
        IngestionErrorCode.UNAUTHORIZED_REDIRECT,
        IngestionErrorCode.UNAUTHORIZED_ROUTE,
        IngestionErrorCode.DOWNLOAD_TOO_LARGE,
        IngestionErrorCode.INVALID_PDF,
    ],
)
def test_non_transient_fetch_failures_are_never_retried(code: IngestionErrorCode) -> None:
    test = harness(one_fetch_plan(), {1: [DocumentIngestionFailure(code)]})
    result = test.service.run(mode=IngestionMode.REFRESH, source_system=SOURCE)
    assert test.fetcher.calls == [1]
    assert result.documents[0].error_code is code


def test_pdf_failure_is_not_retried_and_owned_temporary_is_cleaned() -> None:
    body = b"invalid"
    test = harness(one_fetch_plan(), {1: [fetched(body)]})
    test.processor.invalid_bodies.add(body)
    result = test.service.run(mode=IngestionMode.REFRESH, source_system=SOURCE)
    assert test.fetcher.calls == [1]
    assert result.documents[0].error_code is IngestionErrorCode.INVALID_PDF
    assert test.store.active_temporary == set()
    assert test.events.index("cleanup") < test.events.index("record_failure:1:INVALID_PDF")


@pytest.mark.parametrize("failure_boundary", ["stage", "install", "persist"])
def test_storage_and_persistence_failures_never_retry_fetch(failure_boundary: str) -> None:
    test = harness(one_fetch_plan(), {1: [fetched(b"one-attempt")]})
    if failure_boundary == "stage":
        test.store.stage_failure = DocumentIngestionFailure(IngestionErrorCode.STORAGE_FAILED)
    elif failure_boundary == "install":
        test.store.install_failure = DocumentIngestionFailure(IngestionErrorCode.STORAGE_FAILED)
    else:
        test.repository.persist_failures.add(1)

    result = test.service.run(mode=IngestionMode.REFRESH, source_system=SOURCE)

    assert test.fetcher.calls == [1]
    assert result.failed == 1
    assert result.documents[0].error_code in {
        IngestionErrorCode.STORAGE_FAILED,
        IngestionErrorCode.PERSISTENCE_FAILED,
    }


def test_refresh_same_or_previously_seen_sha_is_unchanged_without_install() -> None:
    body = b"same-pdf"
    digest = hashlib.sha256(body).hexdigest()
    test = harness(one_fetch_plan(), {1: [fetched(body)]})
    test.repository.histories[1] = {digest: 1}
    result = test.service.run(mode=IngestionMode.REFRESH, source_system=SOURCE)
    assert result.unchanged == 1
    assert result.documents[0].version_number == 1
    assert "install" not in test.events
    assert "persist:1" not in test.events
    assert test.store.active_temporary == set()


def test_refresh_changed_content_creates_the_next_version() -> None:
    old_digest = hashlib.sha256(b"old").hexdigest()
    test = harness(one_fetch_plan(), {1: [fetched(b"new")]})
    test.repository.histories[1] = {old_digest: 1}
    result = test.service.run(mode=IngestionMode.REFRESH, source_system=SOURCE)
    assert result.documents[0].version_number == 2
    assert result.new_versions == 1


def test_two_documents_with_the_same_sha_create_two_versions_and_one_global_blob() -> None:
    items = (selected(1), selected(2))
    test = harness(
        SelectionPlan(items, items, ()),
        {1: [fetched(b"shared")], 2: [fetched(b"shared")]},
    )
    result = test.service.run(mode=IngestionMode.REFRESH, source_system=SOURCE)
    assert result.ingested == 2
    assert result.new_versions == 2
    assert result.deduplicated_blobs == 1
    assert result.documents[0].deduplicated is False
    assert result.documents[1].deduplicated is True
    assert len(test.repository.histories) == 2
    assert len(test.repository.blob_ids) == 1
    assert test.factory.opens == 1


def test_one_invalid_document_does_not_rollback_or_stop_its_neighbors() -> None:
    items = (selected(1), selected(2), selected(3))
    test = harness(
        SelectionPlan(items, items, ()),
        {1: [fetched(b"a")], 2: [fetched(b"bad")], 3: [fetched(b"c")]},
    )
    test.processor.invalid_bodies.add(b"bad")
    result = test.service.run(mode=IngestionMode.REFRESH, source_system=SOURCE)
    assert tuple(document.code for document in result.documents) == (
        DocumentResultCode.INGESTED,
        DocumentResultCode.FAILED,
        DocumentResultCode.INGESTED,
    )
    assert result.ingested == 2
    assert result.failed == 1
    assert result.exit_code == 1
    assert set(test.repository.histories) == {1, 3}


def test_failure_recording_failure_is_not_recursive_and_reports_persistence_failure() -> None:
    test = harness(
        one_fetch_plan(),
        {1: [DocumentIngestionFailure(IngestionErrorCode.INVALID_PDF)]},
    )
    test.repository.record_failure_fails.add(1)
    result = test.service.run(mode=IngestionMode.REFRESH, source_system=SOURCE)
    assert test.events.count("record_failure:1:INVALID_PDF") == 1
    assert result.documents[0].error_code is IngestionErrorCode.PERSISTENCE_FAILED


@pytest.mark.parametrize(
    ("install_created", "referenced", "expect_compensation"),
    [
        (True, False, True),
        (True, True, False),
        (False, False, False),
    ],
)
def test_persistence_failure_compensates_only_a_proven_unreferenced_new_blob(
    install_created: bool,
    referenced: bool,
    expect_compensation: bool,
) -> None:
    body = b"compensation"
    digest = hashlib.sha256(body).hexdigest()
    test = harness(one_fetch_plan(), {1: [fetched(body)]})
    if not install_created:
        test.store.installed.add(digest)
    test.repository.persist_failures.add(1)
    test.repository.reference_result = referenced
    result = test.service.run(mode=IngestionMode.REFRESH, source_system=SOURCE)
    assert result.documents[0].error_code is IngestionErrorCode.PERSISTENCE_FAILED
    assert bool(test.store.compensations) is expect_compensation
    assert (digest in test.store.installed) is not expect_compensation
    assert test.store.active_temporary == set()


def test_reference_check_uncertainty_preserves_new_blob_and_reports_persistence_failure() -> None:
    body = b"uncertain"
    digest = hashlib.sha256(body).hexdigest()
    test = harness(one_fetch_plan(), {1: [fetched(body)]})
    test.repository.persist_failures.add(1)
    test.repository.reference_result = DocumentIngestionFailure(
        IngestionErrorCode.PERSISTENCE_FAILED
    )
    result = test.service.run(mode=IngestionMode.REFRESH, source_system=SOURCE)
    assert result.documents[0].error_code is IngestionErrorCode.PERSISTENCE_FAILED
    assert test.store.compensations == []
    assert digest in test.store.installed
    assert test.store.active_temporary == set()


def test_lock_contention_prevents_selection_fetch_and_store_work() -> None:
    test = harness(one_fetch_plan(), {1: [fetched(b"never")]})
    test.lock.unavailable = True
    with pytest.raises(IngestionLockUnavailable):
        test.service.run(mode=IngestionMode.REFRESH, source_system=SOURCE)
    assert test.events == ["lock_acquire"]


def test_empty_active_selection_performs_no_fetch_or_delete_work() -> None:
    test = harness(SelectionPlan((), (), ()), {})
    result = test.service.run(mode=IngestionMode.REFRESH, source_system=SOURCE)
    assert result.remote_files_selected == 0
    assert result.documents == ()
    assert test.factory.opens == 0
    assert test.events == [
        "lock_acquire",
        "lock_enter",
        f"select:REFRESH:{SOURCE}:None",
        "lock_release",
    ]


def test_cleanup_failure_is_fixed_safe_failure_and_lock_still_releases() -> None:
    test = harness(one_fetch_plan(), {1: [fetched(b"cleanup-failure")]})
    test.store.discard_failure = DocumentIngestionFailure(IngestionErrorCode.STORAGE_FAILED)
    result = test.service.run(mode=IngestionMode.REFRESH, source_system=SOURCE)
    assert result.documents[0].error_code is IngestionErrorCode.STORAGE_FAILED
    assert test.events[-1] == "lock_release"


def test_cleanup_failure_does_not_hide_the_primary_failure_code() -> None:
    body = b"primary-pdf-failure"
    test = harness(one_fetch_plan(), {1: [fetched(body)]})
    test.processor.invalid_bodies.add(body)
    test.store.discard_failure = DocumentIngestionFailure(IngestionErrorCode.STORAGE_FAILED)
    result = test.service.run(mode=IngestionMode.REFRESH, source_system=SOURCE)
    assert result.documents[0].error_code is IngestionErrorCode.INVALID_PDF
    assert "record_failure:1:INVALID_PDF" in test.events


def test_batch_preserves_selected_order_and_exact_accounting() -> None:
    one, two, three = selected(1), selected(2), selected(3)
    plan = SelectionPlan((one, two, three), (one, three), (two,))
    test = harness(plan, {1: [fetched(b"one")], 3: [fetched(b"three")]})
    result = test.service.run(
        mode=IngestionMode.INGEST_NEW,
        source_system=SOURCE,
        remote_file_id=3,
    )
    assert tuple(document.remote_file_id for document in result.documents) == (1, 2, 3)
    assert result.remote_files_selected == 3
    assert (result.ingested, result.unchanged, result.failed, result.skipped) == (2, 0, 0, 1)
    assert test.events[2] == f"select:INGEST_NEW:{SOURCE}:3"


def test_naive_clock_fails_before_fetch_without_persisting_unsafe_time() -> None:
    test = harness(one_fetch_plan(), {1: [fetched(b"never")]}, clock=lambda: datetime(2026, 8, 27))
    result = test.service.run(mode=IngestionMode.REFRESH, source_system=SOURCE)
    assert test.fetcher.calls == []
    assert test.repository.recorded_failures == []
    assert result.documents[0].error_code is IngestionErrorCode.PERSISTENCE_FAILED


def test_unsupported_fetcher_capability_is_skipped_without_fetching() -> None:
    item = selected(1)
    test = harness(
        one_fetch_plan(item),
        {1: [fetched(b"must-not-be-fetched")]},
    )
    test.fetcher.unsupported.add(1)

    result = test.service.run(
        mode=IngestionMode.INGEST_NEW,
        source_system=SOURCE,
    )

    assert test.fetcher.calls == []
    assert test.repository.recorded_failures == []
    assert result.remote_files_selected == 1
    assert result.ingested == 0
    assert result.unchanged == 0
    assert result.failed == 0
    assert result.skipped == 1
    assert result.documents[0].code is DocumentResultCode.SKIPPED


def test_supports_unauthorized_route_is_isolated_as_failed_without_fetch() -> None:
    item = selected(1)

    test = harness(
        one_fetch_plan(item),
        {1: [fetched(b"must-not-be-fetched")]},
    )

    def reject_selection(
        selection: RemoteFileSelection,
    ) -> bool:
        del selection
        raise DocumentIngestionFailure(IngestionErrorCode.UNAUTHORIZED_ROUTE)

    test.fetcher.supports = reject_selection  # type: ignore[method-assign]

    result = test.service.run(
        mode=IngestionMode.INGEST_NEW,
        source_system=SOURCE,
    )

    assert test.fetcher.calls == []
    assert result.remote_files_selected == 1
    assert result.ingested == 0
    assert result.unchanged == 0
    assert result.failed == 1
    assert result.skipped == 0

    assert result.documents[0].code is DocumentResultCode.FAILED
    assert result.documents[0].error_code is IngestionErrorCode.UNAUTHORIZED_ROUTE

    assert len(test.repository.recorded_failures) == 1
    assert test.repository.recorded_failures[0][1] is IngestionErrorCode.UNAUTHORIZED_ROUTE
