from __future__ import annotations

from datetime import datetime, timezone

import pytest

import aaos.agent.events as events
from aaos.agent.events import (
    Phase8ActivityEvent,
    Phase8ActivityKind,
    Phase8ActivityStatus,
)
from aaos.agent.observations import project_phase8_activity


def _event(
    *,
    resource_id: str = "request-001",
    event_kind: Phase8ActivityKind = Phase8ActivityKind.COMPUTATION_COMPLETED,
    status: Phase8ActivityStatus = Phase8ActivityStatus.SUCCEEDED,
) -> Phase8ActivityEvent:
    return Phase8ActivityEvent(
        event_kind=event_kind,
        resource_id=resource_id,
        status=status,
        sequence_number=1,
        timestamp_utc=datetime(2026, 9, 18, tzinfo=timezone.utc),
    )


def test_phase8_activity_event_has_closed_safe_projection() -> None:
    event = _event()

    assert event.to_dict() == {
        "event_kind": "COMPUTATION_COMPLETED",
        "resource_id": "request-001",
        "status": "SUCCEEDED",
        "sequence_number": 1,
        "timestamp_utc": "2026-09-18T00:00:00+00:00",
    }
    assert project_phase8_activity(event).to_dict() == event.to_dict()


@pytest.mark.parametrize(
    "resource_id",
    [
        "C:\\Users\\secret\\workspace",
        "token=super-secret",
        "traceback ValueError at /private/path",
    ],
)
def test_phase8_activity_rejects_paths_secrets_and_traceback_text(resource_id: str) -> None:
    with pytest.raises(ValueError):
        _event(resource_id=resource_id)


def test_phase8_activity_has_no_arbitrary_details_field() -> None:
    event_type = getattr(events, "Phase8ActivityEvent")
    with pytest.raises(TypeError):
        event_type(
            event_kind=Phase8ActivityKind.COMPUTATION_COMPLETED,
            resource_id="request-001",
            status=Phase8ActivityStatus.SUCCEEDED,
            sequence_number=1,
            timestamp_utc=datetime(2026, 9, 18, tzinfo=timezone.utc),
            details={"code": "print(secret)"},
        )


def test_phase8_activity_unknown_outcome_is_distinct() -> None:
    event = _event(
        event_kind=Phase8ActivityKind.COMPUTATION_UNKNOWN_OUTCOME,
        status=Phase8ActivityStatus.UNKNOWN_OUTCOME,
    )

    assert project_phase8_activity(event).status is Phase8ActivityStatus.UNKNOWN_OUTCOME


def test_phase8_activity_vision_states_are_closed() -> None:
    for kind, status in (
        (Phase8ActivityKind.VISION_COMPLETED, Phase8ActivityStatus.SUCCEEDED),
        (Phase8ActivityKind.VISION_PARTIAL, Phase8ActivityStatus.PARTIAL),
        (Phase8ActivityKind.VISION_UNAVAILABLE, Phase8ActivityStatus.UNAVAILABLE),
        (Phase8ActivityKind.VISION_FAILED, Phase8ActivityStatus.FAILED),
    ):
        assert _event(event_kind=kind, status=status).event_kind is kind
