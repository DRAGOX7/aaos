"""Acceptance-only local corpus bridge for the frozen Phase 3 pipeline.

This module is deliberately outside the production Moodle/document commands.
It maps an explicit disposable corpus manifest to the existing ``FetchedPdf``
contract, then lets ``Phase3IngestionService`` retain ownership of validation,
hashing, extraction, storage, and persistence.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import stat
import unicodedata
from collections import Counter
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterator, NoReturn

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from aaos.academic.normalization import normalize_collection
from aaos.academic.source import CompleteSourceSnapshot, RawCourse, RawRemoteFile
from aaos.academic.sync import AcademicSyncService
from aaos.documents.contracts import (
    MAX_PDF_BYTES,
    DocumentIngestionFailure,
    ExtractionStatus,
    FetchedPdf,
    IngestionErrorCode,
    IngestionMode,
    RemoteFileSelection,
)
from aaos.documents.lock import IngestionLock
from aaos.documents.pdf import PdfProcessor
from aaos.documents.repository import DocumentRepository
from aaos.documents.service import Phase3IngestionService
from aaos.documents.storage import ContentBlobStore
from aaos.moodle.file_download import BrowserAuthenticatedFetcher
from aaos.persistence.academic_models import CourseORM, RemoteFileORM
from aaos.persistence.database import create_database_engine, create_session_factory
from aaos.persistence.document_models import DocumentORM, DocumentPageORM, DocumentVersionORM

ACCEPTANCE_SOURCE_SYSTEM = "local:phase4:acceptance"
_PDF_ID = re.compile(r"local-pdf:[0-9a-f]{16}:[0-9a-f]{64}\Z", re.ASCII)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_SAFE_COURSE_ID = re.compile(r"local-course:[a-z0-9][a-z0-9-]{0,99}\Z", re.ASCII)
_HEAD_REVISION = "0003_phase3_document_ingestion"
_MAX_MANIFEST_FILES = 10_000


class LocalAcceptanceCorpusError(ValueError):
    """Raised for an invalid acceptance-only corpus or manifest."""


@dataclass(frozen=True, slots=True)
class LocalCorpusFile:
    """One allowlisted PDF and its non-secret integrity/provenance metadata."""

    source_id: str
    course_source_id: str
    title: str
    relative_path: str
    sha256: str
    size_bytes: int
    course_display_name: str = ""


@dataclass(frozen=True, slots=True)
class LocalCorpusManifest:
    """Immutable allowlist for one disposable corpus copy."""

    corpus_root: Path = field(repr=False)
    entries: tuple[LocalCorpusFile, ...]


@dataclass(frozen=True, slots=True)
class LocalPhase3IngestionResult:
    """Aggregate, privacy-safe result of one disposable Phase 3 ingestion."""

    database_path: Path = field(repr=False)
    document_store_root: Path = field(repr=False)
    course_count: int
    document_count: int
    document_version_count: int
    page_count: int
    success_count: int
    partial_count: int
    no_text_count: int
    failed_count: int
    english_dominant_page_count: int
    arabic_dominant_page_count: int
    mixed_page_count: int
    empty_page_count: int
    provenance_ok: bool
    source_hashes_match: bool


def build_local_acceptance_snapshot(corpus_root: Path) -> CompleteSourceSnapshot:
    """Build Phase-1 source metadata from a validated local corpus tree."""
    entries = _scan_corpus(corpus_root)
    return _snapshot_from_entries(entries)


def copy_local_corpus(source_root: Path, destination_root: Path) -> LocalCorpusManifest:
    """Copy only course-grouped PDFs into a disposable allowlisted tree."""
    source = _validated_directory(source_root, label="source corpus")
    destination = _validated_absolute(destination_root, label="corpus copy")
    if destination == source or destination.is_relative_to(source):
        raise LocalAcceptanceCorpusError("corpus copy must not overlap source corpus")
    if destination.exists() and not destination.is_dir():
        raise LocalAcceptanceCorpusError("corpus copy must be a directory")
    destination.mkdir(parents=True, exist_ok=True)
    _reject_reparse(destination)

    source_entries = _scan_corpus(source)
    expected_paths = {entry.relative_path for entry in source_entries}
    for existing in destination.rglob("*"):
        _reject_reparse(existing)
        if (
            existing.is_file()
            and existing.relative_to(destination).as_posix() not in expected_paths
        ):
            raise LocalAcceptanceCorpusError("corpus copy contains an unexpected file")
    copied: list[LocalCorpusFile] = []
    for entry in source_entries:
        source_path = _confined_file(source, entry.relative_path)
        destination_path = _confined_file(destination, entry.relative_path, require_exists=False)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        _reject_reparse(destination_path.parent)
        shutil.copyfile(source_path, destination_path)
        copied_bytes = destination_path.read_bytes()
        copied_hash = hashlib.sha256(copied_bytes).hexdigest()
        if len(copied_bytes) != entry.size_bytes or copied_hash != entry.sha256:
            raise LocalAcceptanceCorpusError("corpus copy integrity check failed")
        copied.append(entry)
    return LocalCorpusManifest(corpus_root=destination, entries=tuple(copied))


class LocalAcceptancePdfFetcher(BrowserAuthenticatedFetcher):
    """Read one exact allowlisted PDF while remaining Phase-3 compatible.

    The parent class is intentionally not initialized: this acceptance-only
    adapter never has a browser context or Moodle authorizer.  The structural
    subtype is confined to this module and returns the frozen ``FetchedPdf``
    value consumed by ``Phase3IngestionService``.
    """

    def __init__(
        self,
        *,
        corpus_root: Path,
        entries: Mapping[str, LocalCorpusFile],
        course_ids: Mapping[str, int],
        source_system: str = ACCEPTANCE_SOURCE_SYSTEM,
    ) -> None:
        self._corpus_root = _validated_directory(corpus_root, label="corpus copy")
        self._source_system = _validated_source_system(source_system)
        if not entries:
            raise LocalAcceptanceCorpusError("acceptance manifest must not be empty")
        self._entries = dict(entries)
        self._course_ids = dict(course_ids)
        self._validate_entries()

    def fetch(self, selection: RemoteFileSelection) -> FetchedPdf:
        """Return only the bytes bound to the exact normalized selection."""
        if selection.source_system != self._source_system:
            _fail(IngestionErrorCode.UNAUTHORIZED_ROUTE)
        entry = self._entries.get(selection.source_id)
        if entry is None:
            _fail(IngestionErrorCode.UNAUTHORIZED_ROUTE)
        expected_course_id = self._course_ids.get(entry.course_source_id)
        if expected_course_id != selection.course_id or selection.title != entry.title:
            _fail(IngestionErrorCode.UNAUTHORIZED_ROUTE)
        path = _confined_file(self._corpus_root, entry.relative_path)
        try:
            body = path.read_bytes()
        except (OSError, RuntimeError, ValueError):
            _fail(IngestionErrorCode.STORAGE_FAILED)
        if len(body) != entry.size_bytes or hashlib.sha256(body).hexdigest() != entry.sha256:
            _fail(IngestionErrorCode.STORAGE_FAILED)
        return FetchedPdf(
            body=body,
            source_filename=Path(entry.relative_path).name,
            advisory_media_type="application/pdf",
        )

    def _validate_entries(self) -> None:
        for source_id, entry in self._entries.items():
            if not isinstance(entry, LocalCorpusFile):
                raise LocalAcceptanceCorpusError("manifest entry type is invalid")
            if (
                not isinstance(source_id, str)
                or source_id != entry.source_id
                or _PDF_ID.fullmatch(source_id) is None
            ):
                raise LocalAcceptanceCorpusError("manifest source identity is invalid")
            if not _SAFE_COURSE_ID.fullmatch(entry.course_source_id):
                raise LocalAcceptanceCorpusError("manifest course identity is invalid")
            if entry.course_source_id not in self._course_ids:
                raise LocalAcceptanceCorpusError("manifest course is not bound")
            if type(self._course_ids[entry.course_source_id]) is not int or (
                self._course_ids[entry.course_source_id] <= 0
            ):
                raise LocalAcceptanceCorpusError("manifest course database identity is invalid")
            if ".." in PurePosixPath(entry.relative_path).parts:
                _fail(IngestionErrorCode.UNAUTHORIZED_ROUTE)
            if (
                not entry.title.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in entry.title)
                or len(entry.title) > 500
                or Path(entry.relative_path).name != entry.title
                or entry.title in {".", ".."}
            ):
                raise LocalAcceptanceCorpusError("manifest title is invalid")
            if entry.course_display_name and (
                not entry.course_display_name.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in entry.course_display_name)
                or len(entry.course_display_name) > 500
            ):
                raise LocalAcceptanceCorpusError("manifest course display name is invalid")
            _validate_relative_path(entry.relative_path)
            if _SHA256.fullmatch(entry.sha256) is None:
                raise LocalAcceptanceCorpusError("manifest hash is invalid")
            if not isinstance(entry.size_bytes, int) or not 0 < entry.size_bytes <= MAX_PDF_BYTES:
                raise LocalAcceptanceCorpusError("manifest size is invalid")
            _confined_file(self._corpus_root, entry.relative_path)


@contextmanager
def _fetcher_session(
    fetcher: LocalAcceptancePdfFetcher,
) -> Iterator[BrowserAuthenticatedFetcher]:
    yield fetcher


def ingest_local_corpus(
    *,
    source_root: Path,
    acceptance_root: Path,
) -> LocalPhase3IngestionResult:
    """Ingest a copied corpus through Phase 3 into disposable paths only."""
    acceptance = _validated_acceptance_root(acceptance_root)
    for existing_name in ("corpus-source", "database", "documents", "rag", "reports"):
        if (acceptance / existing_name).exists():
            raise LocalAcceptanceCorpusError("acceptance root contains prior run state")
    manifest = copy_local_corpus(source_root, acceptance / "corpus-source")
    snapshot = normalize_collection(_snapshot_from_entries(manifest.entries))
    database_path = acceptance / "database" / "acceptance.sqlite3"
    document_store_root = acceptance / "documents"
    lock_path = acceptance / "database" / ".phase3-ingestion.lock"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    database_config = Config(str(_repository_root() / "alembic.ini"))
    database_config.set_main_option("aaos.database_path", str(database_path))
    command.upgrade(database_config, _HEAD_REVISION)

    engine = create_database_engine(database_path)
    try:
        sessions = create_session_factory(engine)
        AcademicSyncService(sessions).sync(snapshot)
        course_ids = _course_ids(sessions)
        fetcher = LocalAcceptancePdfFetcher(
            corpus_root=manifest.corpus_root,
            entries={entry.source_id: entry for entry in manifest.entries},
            course_ids=course_ids,
        )
        service = Phase3IngestionService(
            repository=DocumentRepository(sessions),
            blob_store=ContentBlobStore(document_store_root),
            pdf_processor=PdfProcessor(),
            ingestion_lock=IngestionLock(lock_path),
            fetcher_factory=lambda: _fetcher_session(fetcher),
            clock=lambda: datetime.now(timezone.utc),
        )
        batch = service.run(mode=IngestionMode.INGEST_NEW, source_system=ACCEPTANCE_SOURCE_SYSTEM)
        return _result_from_database(
            sessions,
            database_path=database_path,
            document_store_root=document_store_root,
            manifest=manifest,
            batch_failed=batch.failed,
        )
    finally:
        engine.dispose()


def _scan_corpus(root: Path) -> tuple[LocalCorpusFile, ...]:
    validated = _validated_directory(root, label="corpus")
    course_dirs = tuple(
        sorted((item for item in validated.iterdir() if item.is_dir()), key=lambda p: p.name)
    )
    if not course_dirs:
        raise LocalAcceptanceCorpusError("corpus must contain course folders")
    for course_dir in course_dirs:
        _reject_reparse(course_dir)
    files = tuple(
        sorted(
            (item for item in validated.rglob("*") if item.is_file()), key=lambda p: p.as_posix()
        )
    )
    if len(files) > _MAX_MANIFEST_FILES:
        raise LocalAcceptanceCorpusError("corpus is too large")
    if any(item.suffix.lower() != ".pdf" for item in files):
        raise LocalAcceptanceCorpusError("corpus contains a non-PDF file")
    entries: list[LocalCorpusFile] = []
    course_names: dict[str, str] = {}
    for path in files:
        _reject_reparse(path)
    for path in files:
        relative = path.relative_to(validated).as_posix()
        parts = PurePosixPath(relative).parts
        if len(parts) < 2 or parts[0] not in {item.name for item in course_dirs}:
            raise LocalAcceptanceCorpusError("every PDF must belong to one course folder")
        course_source_id = _course_source_id(parts[0])
        previous_name = course_names.setdefault(course_source_id, parts[0])
        if previous_name != parts[0]:
            raise LocalAcceptanceCorpusError("course identity collision")
        _validate_display_name(parts[0], label="course folder")
        _validate_display_name(path.name, label="PDF filename")
        body = path.read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        path_digest = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:16]
        entries.append(
            LocalCorpusFile(
                source_id=f"local-pdf:{path_digest}:{digest}",
                course_source_id=course_source_id,
                title=path.name,
                relative_path=relative,
                sha256=digest,
                size_bytes=len(body),
                course_display_name=parts[0],
            )
        )
    if not entries:
        raise LocalAcceptanceCorpusError("corpus must contain at least one PDF")
    return tuple(entries)


def _snapshot_from_entries(entries: tuple[LocalCorpusFile, ...]) -> CompleteSourceSnapshot:
    courses_by_id: dict[str, RawCourse] = {}
    remotes: list[RawRemoteFile] = []
    for entry in entries:
        courses_by_id.setdefault(
            entry.course_source_id,
            RawCourse(
                source_id=entry.course_source_id,
                title=entry.course_display_name or entry.course_source_id,
            ),
        )
        remotes.append(
            RawRemoteFile(
                source_id=entry.source_id,
                course_source_id=entry.course_source_id,
                title=entry.title,
            )
        )
    return CompleteSourceSnapshot(
        source_system=ACCEPTANCE_SOURCE_SYSTEM,
        courses=tuple(courses_by_id.values()),
        remote_files=tuple(remotes),
    )


def _course_ids(sessions: sessionmaker[Session]) -> dict[str, int]:
    with sessions() as session:
        rows = tuple(
            session.scalars(
                select(CourseORM).where(CourseORM.source_system == ACCEPTANCE_SOURCE_SYSTEM)
            )
        )
    return {row.source_id: row.id for row in rows}


def _result_from_database(
    sessions: sessionmaker[Session],
    *,
    database_path: Path,
    document_store_root: Path,
    manifest: LocalCorpusManifest,
    batch_failed: int,
) -> LocalPhase3IngestionResult:
    with sessions() as session:
        course_count = int(
            session.scalar(
                select(func.count(CourseORM.id)).where(
                    CourseORM.source_system == ACCEPTANCE_SOURCE_SYSTEM,
                    CourseORM.is_active.is_(True),
                )
            )
            or 0
        )
        acceptance_document_ids = (
            select(DocumentORM.id)
            .join(RemoteFileORM, RemoteFileORM.id == DocumentORM.remote_file_id)
            .where(RemoteFileORM.source_system == ACCEPTANCE_SOURCE_SYSTEM)
        )
        document_count = int(
            session.scalar(
                select(func.count(DocumentORM.id)).where(
                    DocumentORM.id.in_(acceptance_document_ids)
                )
            )
            or 0
        )
        version_query = (
            select(DocumentVersionORM)
            .join(DocumentORM, DocumentORM.id == DocumentVersionORM.document_id)
            .join(RemoteFileORM, RemoteFileORM.id == DocumentORM.remote_file_id)
            .where(RemoteFileORM.source_system == ACCEPTANCE_SOURCE_SYSTEM)
        )
        version_count = int(
            session.scalar(select(func.count()).select_from(version_query.subquery())) or 0
        )
        page_rows = tuple(
            session.scalars(
                select(DocumentPageORM)
                .join(
                    DocumentVersionORM, DocumentVersionORM.id == DocumentPageORM.document_version_id
                )
                .where(DocumentVersionORM.document_id.in_(acceptance_document_ids))
                .order_by(DocumentPageORM.id)
            )
        )
        versions = tuple(session.scalars(version_query))
        remote_rows = tuple(
            session.scalars(
                select(RemoteFileORM).where(RemoteFileORM.source_system == ACCEPTANCE_SOURCE_SYSTEM)
            )
        )
        provenance_ok = _provenance_ok(session, remote_rows)
    status_counts = Counter(version.extraction_status for version in versions)
    page_counts = Counter(_page_language(page.text) for page in page_rows)
    source_hashes_match = all(
        hashlib.sha256((manifest.corpus_root / entry.relative_path).read_bytes()).hexdigest()
        == entry.sha256
        for entry in manifest.entries
    )
    return LocalPhase3IngestionResult(
        database_path=database_path,
        document_store_root=document_store_root,
        course_count=course_count,
        document_count=document_count,
        document_version_count=version_count,
        page_count=len(page_rows),
        success_count=status_counts[ExtractionStatus.SUCCESS.value],
        partial_count=status_counts[ExtractionStatus.PARTIAL.value],
        no_text_count=status_counts[ExtractionStatus.NO_TEXT.value],
        failed_count=batch_failed,
        english_dominant_page_count=page_counts["ENGLISH"],
        arabic_dominant_page_count=page_counts["ARABIC"],
        mixed_page_count=page_counts["MIXED"],
        empty_page_count=page_counts["EMPTY"],
        provenance_ok=provenance_ok,
        source_hashes_match=source_hashes_match,
    )


def _provenance_ok(session: Session, remote_rows: tuple[RemoteFileORM, ...]) -> bool:
    if not remote_rows:
        return False
    for remote in remote_rows:
        course = session.get(CourseORM, remote.course_id)
        document = session.scalar(
            select(DocumentORM).where(DocumentORM.remote_file_id == remote.id)
        )
        if (
            course is None
            or course.source_system != ACCEPTANCE_SOURCE_SYSTEM
            or document is None
            or not remote.source_id.startswith("local-pdf:")
        ):
            return False
    return True


def _page_language(text: str) -> str:
    arabic = latin = 0
    for char in text:
        codepoint = ord(char)
        if (
            0x0600 <= codepoint <= 0x06FF
            or 0x0750 <= codepoint <= 0x077F
            or 0x08A0 <= codepoint <= 0x08FF
            or 0xFB50 <= codepoint <= 0xFDFF
            or 0xFE70 <= codepoint <= 0xFEFF
        ):
            arabic += 1
        elif char.isalpha() and "LATIN" in unicodedata.name(char, ""):
            latin += 1
    if arabic and latin:
        return "MIXED"
    if arabic:
        return "ARABIC"
    if latin:
        return "ENGLISH"
    return "EMPTY"


def _validated_source_system(value: object) -> str:
    if value != ACCEPTANCE_SOURCE_SYSTEM:
        raise LocalAcceptanceCorpusError("acceptance source namespace is fixed")
    return ACCEPTANCE_SOURCE_SYSTEM


def _course_source_id(folder: str) -> str:
    normalized = unicodedata.normalize("NFKC", folder).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")[:90]
    if not slug:
        raise LocalAcceptanceCorpusError("course folder cannot produce a safe identity")
    result = f"local-course:{slug}"
    if _SAFE_COURSE_ID.fullmatch(result) is None:
        raise LocalAcceptanceCorpusError("course identity is invalid")
    return result


def _validated_acceptance_root(value: Path) -> Path:
    root = _validated_absolute(value, label="acceptance root")
    normal_root = (_repository_root() / "data").resolve()
    if root == normal_root or root.is_relative_to(normal_root):
        raise LocalAcceptanceCorpusError("normal AAOS data paths are forbidden")
    root.mkdir(parents=True, exist_ok=True)
    _reject_reparse(root)
    return root


def _validated_directory(value: Path, *, label: str) -> Path:
    root = _validated_absolute(value, label=label)
    if not root.is_dir():
        raise LocalAcceptanceCorpusError(f"{label} must be a directory")
    _reject_reparse(root)
    return root


def _validated_absolute(value: Path, *, label: str) -> Path:
    candidate = Path(value)
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise LocalAcceptanceCorpusError(f"{label} path is invalid") from exc
    if not candidate.is_absolute() or resolved != candidate:
        raise LocalAcceptanceCorpusError(f"{label} path must be absolute and normalized")
    return candidate


def _confined_file(root: Path, relative_path: str, *, require_exists: bool = True) -> Path:
    _validate_relative_path(relative_path)
    candidate = root / Path(*PurePosixPath(relative_path).parts)
    try:
        resolved = candidate.resolve(strict=require_exists)
    except (OSError, RuntimeError, ValueError):
        raise LocalAcceptanceCorpusError("corpus path is unavailable") from None
    if not resolved.is_relative_to(root) or (require_exists and not resolved.is_file()):
        raise LocalAcceptanceCorpusError("corpus path escapes the allowlist")
    _reject_reparse(candidate.parent)
    if require_exists:
        _reject_reparse(candidate)
    return candidate


def _validate_relative_path(value: object) -> None:
    if not isinstance(value, str) or not value or "\\" in value:
        raise LocalAcceptanceCorpusError("manifest path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise LocalAcceptanceCorpusError("manifest path is invalid")


def _validate_display_name(value: str, *, label: str) -> None:
    if (
        not value.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or len(value) > 500
    ):
        raise LocalAcceptanceCorpusError(f"{label} is invalid")


def _reject_reparse(path: Path) -> None:
    current = path
    while True:
        try:
            info = current.lstat()
        except FileNotFoundError:
            current = current.parent
            if current == path.parent:
                break
            continue
        except (OSError, RuntimeError, ValueError) as exc:
            raise LocalAcceptanceCorpusError("corpus ownership cannot be verified") from exc
        attributes = getattr(info, "st_file_attributes", 0)
        if stat.S_ISLNK(info.st_mode) or attributes & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
        ):
            raise LocalAcceptanceCorpusError("reparse paths are not permitted")
        if current.parent == current:
            break
        current = current.parent


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _fail(code: IngestionErrorCode) -> NoReturn:
    raise DocumentIngestionFailure(code)
