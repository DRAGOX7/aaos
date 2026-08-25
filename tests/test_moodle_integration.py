from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from aaos.academic.source import SourceCollectionFailure
from aaos.moodle.integration import MoodleSyncPreconditionError, sync_moodle_snapshot


def test_source_failure_is_rejected_before_session_factory_is_called() -> None:
    session_opened = False

    def forbidden_session_factory() -> Session:
        nonlocal session_opened
        session_opened = True
        raise AssertionError("session factory must not be called for source failure")

    failure = SourceCollectionFailure(
        source_system="moodle:fixture:failed",
        code="browser_navigation",
    )

    with pytest.raises(MoodleSyncPreconditionError):
        sync_moodle_snapshot(
            failure,
            session_factory=forbidden_session_factory,
        )

    assert session_opened is False
