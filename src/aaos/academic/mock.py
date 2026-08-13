"""Deterministic in-memory academic source fixtures."""

from __future__ import annotations

from collections.abc import Mapping

from aaos.academic.source import SourceCollection


class MockSourceError(KeyError):
    """Raised when a named mock fixture does not exist."""


class MockAcademicSource:
    """Return named immutable source collection fixtures without I/O."""

    def __init__(self, snapshots: Mapping[str, SourceCollection]) -> None:
        self._snapshots = dict(snapshots)

    def collect(self, name: str) -> SourceCollection:
        try:
            return self._snapshots[name]
        except KeyError as exc:
            raise MockSourceError(f"unknown mock snapshot: {name}") from exc
