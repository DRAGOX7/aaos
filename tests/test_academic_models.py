from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aaos.academic.models import CanonicalCourse, CanonicalSnapshot


def test_canonical_records_are_frozen_and_snapshot_uses_tuples() -> None:
    course = CanonicalCourse(source_system="mock:primary", source_id="c1", title="Math")
    snapshot = CanonicalSnapshot(source_system="mock:primary", courses=(course,))

    with pytest.raises((AttributeError, TypeError)):
        course.title = "Changed"  # type: ignore[misc]
    assert isinstance(snapshot.courses, tuple)


def test_canonical_course_accepts_aware_utc_timestamp() -> None:
    course = CanonicalCourse(
        source_system="mock:primary",
        source_id="c1",
        title="Math",
        source_updated_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
    )
    assert course.source_updated_at is not None
