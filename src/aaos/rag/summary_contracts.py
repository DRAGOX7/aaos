"""Typed, bounded values for verified document summaries (Phase I3).

A stored summary keeps only verified point text and the chunk keys each point
cites. It is never rendered directly: on read, the chunks are re-materialized
under a fresh snapshot and the points re-validated by the citation validator.
"""

from __future__ import annotations

from dataclasses import dataclass

SUMMARY_LANGUAGES = frozenset({"en", "ar"})
MAX_SUMMARY_POINTS = 12
MAX_SUMMARY_POINT_CHARACTERS = 2048
MAX_SUMMARY_CHUNK_KEYS_PER_POINT = 8
_MAX_IDENTIFIER_CHARACTERS = 128


def _positive_int(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _bounded_text(value: object, name: str, limit: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must be non-blank bounded text")


@dataclass(frozen=True, slots=True)
class SummaryPoint:
    """One verified summary point and the chunk keys it cites."""

    text: str
    chunk_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        _bounded_text(self.text, "summary point text", MAX_SUMMARY_POINT_CHARACTERS)
        if (
            not isinstance(self.chunk_keys, tuple)
            or not self.chunk_keys
            or len(self.chunk_keys) > MAX_SUMMARY_CHUNK_KEYS_PER_POINT
            or len(set(self.chunk_keys)) != len(self.chunk_keys)
        ):
            raise ValueError("summary point must cite unique, bounded chunk keys")
        for key in self.chunk_keys:
            _bounded_text(key, "chunk key", _MAX_IDENTIFIER_CHARACTERS)


@dataclass(frozen=True, slots=True)
class SummaryCacheKey:
    """Every identity a cached summary depends on; any change is a cache miss."""

    document_version_id: int
    generation_id: int
    content_revision: int
    policy_version: str
    model_identifier: str
    language: str

    def __post_init__(self) -> None:
        _positive_int(self.document_version_id, "document_version_id")
        _positive_int(self.generation_id, "generation_id")
        if (
            isinstance(self.content_revision, bool)
            or not isinstance(self.content_revision, int)
            or self.content_revision < 0
        ):
            raise ValueError("content_revision must be a non-negative integer")
        _bounded_text(self.policy_version, "policy_version", 64)
        _bounded_text(self.model_identifier, "model_identifier", _MAX_IDENTIFIER_CHARACTERS)
        if self.language not in SUMMARY_LANGUAGES:
            raise ValueError("language must be 'en' or 'ar'")


@dataclass(frozen=True, slots=True)
class StoredDocumentSummary:
    """A complete, verified document summary as persisted in the cache."""

    key: SummaryCacheKey
    points: tuple[SummaryPoint, ...]
    sections_total: int
    dropped_points: int

    def __post_init__(self) -> None:
        if not isinstance(self.key, SummaryCacheKey):
            raise ValueError("key must be a SummaryCacheKey")
        if (
            not isinstance(self.points, tuple)
            or not 1 <= len(self.points) <= MAX_SUMMARY_POINTS
            or any(not isinstance(point, SummaryPoint) for point in self.points)
        ):
            raise ValueError(f"a summary needs 1 to {MAX_SUMMARY_POINTS} points")
        _positive_int(self.sections_total, "sections_total")
        if (
            isinstance(self.dropped_points, bool)
            or not isinstance(self.dropped_points, int)
            or self.dropped_points < 0
        ):
            raise ValueError("dropped_points must be a non-negative integer")
