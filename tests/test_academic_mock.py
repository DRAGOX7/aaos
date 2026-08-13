from __future__ import annotations

from pathlib import Path

import pytest

from aaos.academic.mock import MockAcademicSource, MockSourceError
from aaos.academic.source import CompleteSourceSnapshot, RawAssignment, RawCourse


def test_named_mock_source_returns_configured_fixture() -> None:
    snapshot = CompleteSourceSnapshot(
        source_system="mock:primary",
        courses=(RawCourse(source_id="c1", title="Math"),),
        assignments=(RawAssignment(source_id="a1", course_source_id="c1", title="Task"),),
    )
    source = MockAcademicSource({"initial": snapshot})
    assert source.collect("initial") == snapshot
    assert source.collect("initial") is snapshot


def test_unknown_mock_fixture_is_typed_failure() -> None:
    source = MockAcademicSource({})
    with pytest.raises(MockSourceError, match="unknown"):
        source.collect("missing")


def test_mock_source_does_not_read_files_or_mutate_fixtures(tmp_path: Path) -> None:
    snapshot = CompleteSourceSnapshot(source_system="mock:primary")
    source = MockAcademicSource({"empty": snapshot})
    result = source.collect("empty")
    assert isinstance(result, CompleteSourceSnapshot)
    assert result.courses == ()
    assert list(tmp_path.iterdir()) == []
