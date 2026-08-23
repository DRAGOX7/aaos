"""Closed non-secret contracts for the restricted Moodle browser source."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

from aaos.moodle.models import AuthPaths

BrowserRoute: TypeAlias = Literal[
    "COURSE_INDEX",
    "COURSE_DETAIL",
    "ASSIGNMENT_LIST",
    "ANNOUNCEMENT_LIST",
    "RESOURCE_METADATA",
]
BrowserEvent: TypeAlias = Literal[
    "BROWSER_AUTH_REQUIRED",
    "BROWSER_AUTHENTICATED",
    "COURSE_COLLECTION_STARTED",
    "COURSE_COLLECTION_COMPLETE",
    "ASSIGNMENT_COLLECTION_COMPLETE",
    "COLLECTION_FAILED",
    "SESSION_EXPIRED",
]


class BrowserPolicyError(RuntimeError):
    """Raised with a fixed message when browser policy validation fails."""

    def __init__(self) -> None:
        super().__init__("browser collection unavailable")


class BrowserProfileBusyError(RuntimeError):
    """Raised with a fixed message when the dedicated profile is locked."""

    def __init__(self) -> None:
        super().__init__("browser profile unavailable")


@dataclass(frozen=True, slots=True)
class BrowserProfilePaths:
    """The exact local paths the browser connector may own."""

    profile_dir: Path
    lock_path: Path
    binding_path: Path


@dataclass(frozen=True, slots=True)
class PaginationProof:
    """Non-content evidence that a paginated route was exhausted."""

    route: BrowserRoute
    visited_page_numbers: tuple[int, ...]
    exhausted: bool
    expected_record_count: int | None
    observed_record_count: int


class BrowserProfileLease:
    """Exclusive ownership of one application-owned profile lock."""

    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._lock_path.unlink()
        except FileNotFoundError:
            return
        except OSError:
            return

    def __enter__(self) -> BrowserProfileLease:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()


def resolve_browser_profile_paths(paths: AuthPaths, expected_origin: str) -> BrowserProfilePaths:
    """Build the sole source-bound persistent profile layout without I/O."""
    if not expected_origin.startswith("https://") or expected_origin.rstrip("/") != expected_origin:
        raise BrowserPolicyError()
    root = paths.browser_state_dir.resolve(strict=False)
    profile_dir = root / "playwright-edge-profile"
    lock_path = root / ".playwright-edge-profile.lock"
    binding_path = root / "playwright-edge-profile.binding.json"
    for candidate in (profile_dir, lock_path, binding_path):
        if candidate.resolve(strict=False).parent != root:
            raise BrowserPolicyError()
    return BrowserProfilePaths(
        profile_dir=profile_dir, lock_path=lock_path, binding_path=binding_path
    )


def acquire_browser_profile_lock(paths: BrowserProfilePaths) -> BrowserProfileLease:
    """Atomically acquire the one lock owned by the dedicated browser profile."""
    _reject_reparse_ancestors(paths.lock_path.parent)
    try:
        paths.lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(paths.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise BrowserProfileBusyError() from None
    except OSError:
        raise BrowserPolicyError() from None
    try:
        os.close(descriptor)
    except OSError:
        try:
            paths.lock_path.unlink()
        except OSError:
            pass
        raise BrowserPolicyError() from None
    return BrowserProfileLease(paths.lock_path)


def validate_browser_profile_binding(
    paths: BrowserProfilePaths, *, expected_origin: str, expected_alias: str
) -> None:
    """Accept only the AAOS-owned non-secret binding marker for this exact profile."""
    try:
        _reject_reparse_ancestors(paths.binding_path.parent)
        payload = paths.binding_path.read_bytes()
        if len(payload) > 1024:
            raise BrowserPolicyError()
        document = json.loads(payload.decode("utf-8"))
        if document != {
            "version": 1,
            "site": expected_origin + "/current",
            "alias": expected_alias,
        }:
            raise BrowserPolicyError()
    except (OSError, UnicodeError, TypeError, ValueError):
        raise BrowserPolicyError() from None


def validate_pagination_proof(proof: PaginationProof) -> None:
    """Reject pagination evidence that cannot prove bounded exhaustion."""
    pages = proof.visited_page_numbers
    if (
        not proof.exhausted
        or not pages
        or pages != tuple(range(len(pages)))
        or proof.observed_record_count < 0
        or proof.expected_record_count is not None
        and proof.expected_record_count != proof.observed_record_count
    ):
        raise BrowserPolicyError()


def _reject_reparse_ancestors(path: Path) -> None:
    """Reject existing symbolic links and Windows reparse points without traversal."""
    current = path
    while True:
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            pass
        except OSError:
            raise BrowserPolicyError() from None
        else:
            attributes = getattr(metadata, "st_file_attributes", 0)
            if stat.S_ISLNK(metadata.st_mode) or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise BrowserPolicyError()
        if current.parent == current:
            return
        current = current.parent
