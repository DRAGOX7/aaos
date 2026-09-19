from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aaos.agent.events import (
    Phase8ActivityEvent,
    Phase8ActivityKind,
    Phase8ActivityStatus,
)
from aaos.agent.observations import project_phase8_activity


def _event(**overrides: object) -> Phase8ActivityEvent:
    values: dict[str, object] = {
        "event_kind": Phase8ActivityKind.COMPUTATION_COMPLETED,
        "resource_id": "request-001",
        "status": Phase8ActivityStatus.SUCCEEDED,
        "sequence_number": 1,
        "timestamp_utc": datetime(2026, 9, 18, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return Phase8ActivityEvent(**values)


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
    "field,value",
    [
        ("resource_id", "C:\\Users\\secret\\workspace"),
        ("resource_id", "token=super-secret"),
        ("resource_id", "traceback ValueError at /private/path"),
    ],
)
def test_phase8_activity_rejects_paths_secrets_and_traceback_text(field: str, value: str) -> None:
    with pytest.raises(ValueError):
        _event(**{field: value})


def test_phase8_activity_has_no_arbitrary_details_field() -> None:
    with pytest.raises(TypeError):
        _event(details={"code": "print(secret)"})


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
