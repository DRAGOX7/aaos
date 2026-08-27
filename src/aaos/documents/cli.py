"""Exact, side-effect-free command composition for Phase 3 ingestion."""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from aaos.documents.config import DocumentRuntimePaths, resolve_document_runtime_paths
from aaos.documents.contracts import (
    BatchIngestionResult,
    DocumentIngestionFailure,
    DocumentIngestionResult,
    DocumentResultCode,
    ExtractionStatus,
    IngestionErrorCode,
    IngestionMode,
)
from aaos.documents.lock import IngestionLock, IngestionLockUnavailable
from aaos.documents.pdf import PdfProcessor
from aaos.documents.repository import DocumentRepository
from aaos.documents.service import Phase3IngestionService
from aaos.documents.storage import ContentBlobStore
from aaos.moodle.bootstrap import MoodleBootstrapConfig
from aaos.moodle.file_download import BrowserAuthenticatedFetcher, open_phase3_fetcher_session
from aaos.moodle.models import AuthPaths, MoodleSettings
from aaos.moodle.receiver import (
    ReceiverError,
    RuntimeMoodleEnvironment,
    load_runtime_moodle_environment,
)
from aaos.persistence.database import create_database_engine, create_session_factory

_USAGE = "usage: aaos documents <ingest-new|refresh> [--remote-file-id <positive-int>]"
_HEAD_REVISION = "0003_phase3_document_ingestion"
_POSITIVE_DECIMAL = re.compile(r"[1-9][0-9]*\Z", re.ASCII)

__all__ = [
    "RuntimeMoodleEnvironment",
    "build_phase3_service",
    "load_runtime_moodle_environment",
    "render_batch_result",
    "run_documents_command",
]


class DocumentCommandError(RuntimeError):
    """Fixed, safe failure for command configuration/composition."""


def _configuration_failure() -> DocumentCommandError:
    return DocumentCommandError("document ingestion configuration failed")


def _database_is_migrated(database_path: Path) -> None:
    try:
        if not database_path.is_file() or database_path.resolve(strict=True) != database_path:
            raise _configuration_failure()
        engine = create_database_engine(database_path)
        try:
            with engine.connect() as connection:
                revision = connection.exec_driver_sql(
                    "SELECT version_num FROM alembic_version"
                ).scalar_one()
        finally:
            engine.dispose()
    except DocumentCommandError:
        raise
    except (OSError, RuntimeError, SQLAlchemyError, TypeError, ValueError):
        raise _configuration_failure() from None
    if revision != _HEAD_REVISION:
        raise _configuration_failure()


def _validate_content_store_parent(path: Path) -> None:
    try:
        parent = path.parent
        if not parent.is_dir() or parent.resolve(strict=True) != parent:
            raise _configuration_failure()
    except DocumentCommandError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise _configuration_failure() from None


def build_phase3_service(
    *,
    bootstrap_config: MoodleBootstrapConfig,
    settings: MoodleSettings,
    auth_paths: AuthPaths,
    paths: DocumentRuntimePaths,
) -> Phase3IngestionService:
    """Compose Phase 3 from explicit validated runtime dependencies."""
    _database_is_migrated(paths.database_path)
    _validate_content_store_parent(paths.content_store_root)
    try:
        engine = create_database_engine(paths.database_path)
        repository = DocumentRepository(create_session_factory(engine))
        blob_store = ContentBlobStore(paths.content_store_root)
        ingestion_lock = IngestionLock(paths.ingestion_lock_path)

        def fetcher_factory() -> AbstractContextManager[BrowserAuthenticatedFetcher]:
            return open_phase3_fetcher_session(
                canonical_wwwroot=bootstrap_config.canonical_wwwroot,
                expected_source_system=settings.source_system,
                settings=settings,
                auth_paths=auth_paths,
            )

        return Phase3IngestionService(
            repository=repository,
            blob_store=blob_store,
            pdf_processor=PdfProcessor(),
            ingestion_lock=ingestion_lock,
            fetcher_factory=fetcher_factory,
            clock=_utc_now,
        )
    except DocumentCommandError:
        raise
    except (OSError, RuntimeError, SQLAlchemyError, TypeError, ValueError):
        raise _configuration_failure() from None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_arguments(argv: Sequence[str]) -> tuple[IngestionMode, int | None] | None:
    arguments = tuple(argv)
    if len(arguments) not in {1, 3} or arguments[0] not in {"ingest-new", "refresh"}:
        return None
    mode = IngestionMode.INGEST_NEW if arguments[0] == "ingest-new" else IngestionMode.REFRESH
    if len(arguments) == 1:
        return mode, None
    if arguments[1] != "--remote-file-id" or _POSITIVE_DECIMAL.fullmatch(arguments[2]) is None:
        return None
    try:
        remote_file_id = int(arguments[2])
    except (TypeError, ValueError, OverflowError):
        return None
    return mode, remote_file_id if remote_file_id > 0 else None


def run_documents_command(argv: Sequence[str]) -> int:
    """Parse and execute one exact document-ingestion command."""
    parsed = _parse_arguments(argv)
    if parsed is None:
        print(_USAGE, file=sys.stderr)
        return 2
    mode, remote_file_id = parsed
    try:
        environment = load_runtime_moodle_environment()
        paths = resolve_document_runtime_paths(environment.data_root)
        service = build_phase3_service(
            bootstrap_config=environment.bootstrap_config,
            settings=environment.settings,
            auth_paths=environment.auth_paths,
            paths=paths,
        )
        result = service.run(
            mode=mode,
            source_system=environment.settings.source_system,
            remote_file_id=remote_file_id,
        )
        lines = render_batch_result(result)
        exit_code = _batch_exit_code(result)
    except (
        DocumentCommandError,
        DocumentIngestionFailure,
        IngestionLockUnavailable,
        ReceiverError,
        OSError,
        SQLAlchemyError,
        TypeError,
        ValueError,
    ):
        print("DOCUMENT_INGESTION_FAILED", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    return exit_code


def render_batch_result(result: BatchIngestionResult) -> tuple[str, ...]:
    """Render only fixed status and numeric ingestion metadata."""
    _validate_batch_result(result)
    lines = [
        (
            "DOCUMENT "
            f"REMOTE_FILE_ID={item.remote_file_id} "
            f"CODE={item.code.value} "
            f"VERSION_NUMBER={_value(item.version_number)} "
            f"SIZE_BYTES={_value(item.size_bytes)} "
            f"PAGE_COUNT={_value(item.page_count)} "
            f"EXTRACTION_STATUS={_optional_enum_value(item.extraction_status, ExtractionStatus)} "
            f"DEDUPLICATED={str(item.deduplicated).upper()} "
            f"ERROR_CODE={_optional_enum_value(item.error_code, IngestionErrorCode)}"
        )
        for item in result.documents
    ]
    lines.extend(
        (
            f"REMOTE_FILES_SELECTED={result.remote_files_selected}",
            f"INGESTED={result.ingested}",
            f"UNCHANGED={result.unchanged}",
            f"FAILED={result.failed}",
            f"SKIPPED={result.skipped}",
            f"NEW_VERSIONS={result.new_versions}",
            f"DEDUPLICATED_BLOBS={result.deduplicated_blobs}",
        )
    )
    return tuple(lines)


def _validate_batch_result(result: BatchIngestionResult) -> None:
    if type(result) is not BatchIngestionResult or type(result.documents) is not tuple:
        raise _configuration_failure()
    for field in (
        result.remote_files_selected,
        result.ingested,
        result.unchanged,
        result.failed,
        result.skipped,
        result.new_versions,
        result.deduplicated_blobs,
    ):
        _require_nonnegative_int(field)
    for item in result.documents:
        _validate_document_result(item)


def _validate_document_result(item: DocumentIngestionResult) -> None:
    if type(item) is not DocumentIngestionResult:
        raise _configuration_failure()
    if not _is_positive_int(item.remote_file_id):
        raise _configuration_failure()
    if type(item.code) is not DocumentResultCode:
        raise _configuration_failure()
    for value in (item.version_number, item.size_bytes, item.page_count):
        if value is not None:
            _require_nonnegative_int(value)
    if item.extraction_status is not None and type(item.extraction_status) is not ExtractionStatus:
        raise _configuration_failure()
    if type(item.deduplicated) is not bool:
        raise _configuration_failure()
    if item.error_code is not None and type(item.error_code) is not IngestionErrorCode:
        raise _configuration_failure()


def _require_nonnegative_int(value: object) -> None:
    if type(value) is not int or value < 0:
        raise _configuration_failure()


def _is_positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _batch_exit_code(result: BatchIngestionResult) -> int:
    _require_nonnegative_int(result.failed)
    return 1 if result.failed > 0 else 0


def _value(value: int | None) -> str:
    return "NONE" if value is None else str(value)


def _optional_enum_value(value: object, enum_type: type[object]) -> str:
    if value is None:
        return "NONE"
    if type(value) is not enum_type:
        raise _configuration_failure()
    enum_value = getattr(value, "value", None)
    if type(enum_value) is not str:
        raise _configuration_failure()
    return enum_value
