"""Confined content-addressed storage for Phase 3 PDF bytes."""

from __future__ import annotations

import errno
import hashlib
import os
import stat
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from aaos.documents.contracts import (
    MAX_PDF_BYTES,
    BlobInstall,
    DocumentIngestionFailure,
    IngestionErrorCode,
)

_MAX_FILENAME_BYTES = 255
_TEMPORARY_DIRECTORY = ".temporary"
_TEMPORARY_PREFIX = "aaos-"
_TEMPORARY_SUFFIX = ".tmp"
_SUPPORTED_STORAGE_SUFFIXES = frozenset({".pdf", ".docx"})
_WINDOWS_RESERVED_CHARACTERS = frozenset('<>:"/\\|?*')
_WINDOWS_RESERVED_BASENAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)


def _storage_failure() -> DocumentIngestionFailure:
    return DocumentIngestionFailure(IngestionErrorCode.STORAGE_FAILED)


def _truncate_utf8(value: str, maximum_bytes: int) -> str:
    result: list[str] = []
    size = 0
    for character in value:
        encoded_size = len(character.encode("utf-8"))
        if size + encoded_size > maximum_bytes:
            break
        result.append(character)
        size += encoded_size
    return "".join(result)


def _clean_display_component(value: str) -> str:
    basename = value.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    characters: list[str] = []
    for character in basename:
        if unicodedata.category(character).startswith("C"):
            continue
        characters.append("_" if character in _WINDOWS_RESERVED_CHARACTERS else character)
    cleaned = "".join(characters).strip().rstrip(" .")
    if cleaned in {"", ".", ".."}:
        return ""
    device_basename = cleaned.split(".", maxsplit=1)[0].upper()
    if device_basename in _WINDOWS_RESERVED_BASENAMES:
        cleaned = f"_{cleaned}"
    return _truncate_utf8(cleaned, _MAX_FILENAME_BYTES).rstrip(" .")


def sanitize_source_filename(
    value: str | None,
    *,
    fallback: str,
    suffix: str = ".pdf",
) -> str:
    """Return bounded display metadata with no filesystem-path semantics."""
    if suffix not in _SUPPORTED_STORAGE_SUFFIXES:
        raise _storage_failure()

    cleaned = _clean_display_component(value or "")
    if cleaned:
        return cleaned

    fallback_name = _clean_display_component(fallback)
    if fallback_name.lower().endswith(suffix):
        fallback_name = fallback_name[: -len(suffix)].rstrip(" .")
    if not fallback_name:
        fallback_name = "document"

    maximum_base_bytes = _MAX_FILENAME_BYTES - len(suffix.encode("utf-8"))
    fallback_name = _truncate_utf8(
        fallback_name,
        maximum_base_bytes,
    ).rstrip(" .")
    if not fallback_name:
        fallback_name = "document"

    return f"{fallback_name}{suffix}"


@dataclass(frozen=True, slots=True)
class StagedBlob:
    sha256: str
    size_bytes: int
    temporary_path: Path = field(repr=False)


class ContentBlobStore:
    """Stage and atomically install immutable blobs beneath one owned root."""

    def __init__(self, root: Path) -> None:
        candidate = Path(root)
        try:
            if not candidate.is_absolute() or candidate.resolve(strict=False) != candidate:
                raise _storage_failure()
            _reject_reparse_ancestors(candidate)
        except DocumentIngestionFailure:
            raise
        except (OSError, RuntimeError, ValueError):
            raise _storage_failure() from None
        self._root = candidate
        self._temporary_root = candidate / _TEMPORARY_DIRECTORY

    def stage(self, body: bytes) -> StagedBlob:
        if not body or len(body) > MAX_PDF_BYTES:
            raise _storage_failure()

        temporary_path: Path | None = None
        descriptor: int | None = None
        try:
            self._ensure_directory(self._root, create=True)
            self._ensure_directory(self._temporary_root, create=True)
            descriptor, path_value = tempfile.mkstemp(
                prefix=_TEMPORARY_PREFIX,
                suffix=_TEMPORARY_SUFFIX,
                dir=self._temporary_root,
            )
            temporary_path = Path(path_value)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = None
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            self._ensure_owned_temporary(temporary_path, require_exists=True)
            return StagedBlob(
                sha256=hashlib.sha256(body).hexdigest(),
                size_bytes=len(body),
                temporary_path=temporary_path,
            )
        except DocumentIngestionFailure:
            self._best_effort_remove_created_temporary(temporary_path)
            raise
        except (OSError, RuntimeError, ValueError):
            self._best_effort_remove_created_temporary(temporary_path)
            raise _storage_failure() from None
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def install(
        self,
        staged: StagedBlob,
        *,
        suffix: str = ".pdf",
    ) -> BlobInstall:
        try:
            self._validate_staged_blob(staged)
            storage_key = _storage_key(
                staged.sha256,
                suffix=suffix,
            )
            relative_path = Path(*storage_key.split("/"))
            target = self._root / relative_path
            hash_root = self._root / "sha256"
            prefix_root = hash_root / staged.sha256[:2]
            self._ensure_directory(self._root, create=True)
            self._ensure_directory(hash_root, create=True)
            self._ensure_directory(prefix_root, create=True)
            self._ensure_confined(target)

            if _path_exists(target):
                self._verify_regular_blob(
                    target,
                    expected_size=staged.size_bytes,
                    expected_sha256=staged.sha256,
                )
                self.discard_temporary(staged)
                return BlobInstall(
                    sha256=staged.sha256,
                    size_bytes=staged.size_bytes,
                    storage_key=storage_key,
                    created=False,
                )

            os.replace(staged.temporary_path, target)
            self._verify_regular_blob(
                target,
                expected_size=staged.size_bytes,
                expected_sha256=staged.sha256,
            )
            return BlobInstall(
                sha256=staged.sha256,
                size_bytes=staged.size_bytes,
                storage_key=storage_key,
                created=True,
            )
        except DocumentIngestionFailure:
            raise
        except (OSError, RuntimeError, ValueError):
            raise _storage_failure() from None

    def discard_temporary(self, staged: StagedBlob) -> None:
        try:
            exists = self._ensure_owned_temporary(staged.temporary_path, require_exists=False)
            if not exists:
                return
            staged.temporary_path.unlink()
        except DocumentIngestionFailure:
            raise
        except (OSError, RuntimeError, ValueError):
            raise _storage_failure() from None

    def compensate_new_blob(
        self,
        install: BlobInstall,
        *,
        referenced: bool,
    ) -> None:
        if not install.created or referenced:
            return
        try:
            if Path(install.storage_key).is_absolute():
                raise _storage_failure()

            expected_keys = {
                _storage_key(install.sha256, suffix=suffix)
                for suffix in _SUPPORTED_STORAGE_SUFFIXES
            }
            if install.storage_key not in expected_keys:
                raise _storage_failure()
            target = self._root / Path(*install.storage_key.split("/"))
            self._ensure_confined(target)
            if not _path_exists(target):
                return
            self._verify_regular_blob(
                target,
                expected_size=install.size_bytes,
                expected_sha256=install.sha256,
            )
            target.unlink()
            self._remove_empty_owned_prefix(target.parent)
        except DocumentIngestionFailure:
            raise
        except (OSError, RuntimeError, ValueError):
            raise _storage_failure() from None

    def _validate_staged_blob(self, staged: StagedBlob) -> None:
        if (
            not _is_sha256(staged.sha256)
            or staged.size_bytes < 1
            or staged.size_bytes > MAX_PDF_BYTES
        ):
            raise _storage_failure()
        self._ensure_owned_temporary(staged.temporary_path, require_exists=True)
        self._verify_regular_blob(
            staged.temporary_path,
            expected_size=staged.size_bytes,
            expected_sha256=staged.sha256,
        )

    def _ensure_owned_temporary(self, path: Path, *, require_exists: bool) -> bool:
        candidate = Path(path)
        self._ensure_confined(candidate)
        if (
            candidate.parent != self._temporary_root
            or not candidate.name.startswith(_TEMPORARY_PREFIX)
            or not candidate.name.endswith(_TEMPORARY_SUFFIX)
        ):
            raise _storage_failure()
        temporary_root_exists = self._ensure_directory(
            self._temporary_root,
            create=False,
        )
        if not temporary_root_exists:
            if require_exists:
                raise _storage_failure()
            return False
        if not _path_exists(candidate):
            if require_exists:
                raise _storage_failure()
            return False
        _ensure_regular_file(candidate)
        return True

    def _ensure_directory(self, path: Path, *, create: bool) -> bool:
        self._ensure_confined(path)
        if _path_exists(path):
            _ensure_directory_path(path)
            return True
        if not create:
            return False
        path.mkdir(parents=True, exist_ok=True)
        self._ensure_confined(path)
        _ensure_directory_path(path)
        return True

    def _ensure_confined(self, path: Path) -> None:
        candidate = Path(path)
        if not candidate.is_absolute() or not (
            candidate == self._root or candidate.is_relative_to(self._root)
        ):
            raise _storage_failure()
        _reject_reparse_ancestors(candidate)
        resolved_root = self._root.resolve(strict=False)
        resolved_candidate = candidate.resolve(strict=False)
        if resolved_root != self._root or not (
            resolved_candidate == resolved_root or resolved_candidate.is_relative_to(resolved_root)
        ):
            raise _storage_failure()

    def _verify_regular_blob(
        self,
        path: Path,
        *,
        expected_size: int,
        expected_sha256: str,
    ) -> None:
        self._ensure_confined(path)
        _ensure_regular_file(path)
        if path.stat().st_size != expected_size or _hash_file(path) != expected_sha256:
            raise _storage_failure()

    def _remove_empty_owned_prefix(self, prefix_root: Path) -> None:
        expected_parent = self._root / "sha256"
        self._ensure_confined(prefix_root)
        if prefix_root.parent != expected_parent:
            raise _storage_failure()
        if not _path_exists(prefix_root):
            return
        _ensure_directory_path(prefix_root)
        try:
            prefix_root.rmdir()
        except OSError as error:
            if error.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
                raise

    def _best_effort_remove_created_temporary(self, path: Path | None) -> None:
        if path is None:
            return
        try:
            if path.parent == self._temporary_root and _path_exists(path):
                _ensure_regular_file(path)
                path.unlink()
        except OSError:
            pass


def _storage_key(
    sha256: str,
    *,
    suffix: str = ".pdf",
) -> str:
    if not _is_sha256(sha256) or suffix not in _SUPPORTED_STORAGE_SUFFIXES:
        raise _storage_failure()
    return f"sha256/{sha256[:2]}/{sha256}{suffix}"


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _ensure_regular_file(path: Path) -> None:
    metadata = path.lstat()
    attributes = getattr(metadata, "st_file_attributes", 0)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
        or not stat.S_ISREG(metadata.st_mode)
    ):
        raise _storage_failure()


def _ensure_directory_path(path: Path) -> None:
    metadata = path.lstat()
    attributes = getattr(metadata, "st_file_attributes", 0)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
        or not stat.S_ISDIR(metadata.st_mode)
    ):
        raise _storage_failure()


def _reject_reparse_ancestors(path: Path) -> None:
    current = path
    while True:
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            pass
        else:
            attributes = getattr(metadata, "st_file_attributes", 0)
            if stat.S_ISLNK(metadata.st_mode) or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise _storage_failure()
        if current.parent == current:
            return
        current = current.parent
