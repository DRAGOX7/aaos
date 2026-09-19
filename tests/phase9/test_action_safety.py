from __future__ import annotations

from tests.phase9.action_safety_harness import (
    ActionScenario,
    FrozenClock,
    run_action_scenario,
)


def test_fake_lifecycle_preserves_approval_capability_and_execution_boundaries() -> None:
    result = run_action_scenario(ActionScenario.SUCCESS, clock=FrozenClock())
    assert result.action_state == "CONFIRMED_SUCCESS"
    assert result.operation_state == "CONFIRMED_SUCCESS"
    assert result.execution_attempt_count == 1
    assert result.persisted_outcome == "CONFIRMED_SUCCESS"
    assert result.real_external_writes == 0
    assert "DISPATCH_STARTED" in result.event_kinds


def test_unknown_outcome_requires_reconciliation_and_never_retries() -> None:
    result = run_action_scenario(ActionScenario.UNKNOWN_OUTCOME, clock=FrozenClock())
    assert result.action_state == "CONFIRMED_FAILURE"
    assert result.operation_state == "CONFIRMED_FAILURE"
    assert result.reconciliation_count == 1
    assert result.execution_attempt_count == 1
    assert result.automatic_retry_count == 0


def test_stale_precondition_fails_closed_without_external_side_effect() -> None:
    result = run_action_scenario(ActionScenario.STALE_PRECONDITION, clock=FrozenClock())
    assert result.action_state == "INVALIDATED"
    assert result.operation_state == "NOT_DISPATCHED"
    assert result.persisted_outcome is None
    assert result.real_external_writes == 0


def test_rejection_revocation_expiry_and_capability_drift_are_trusted_boundaries() -> None:
    for scenario, expected in (
        (ActionScenario.REJECTED, "REJECTED"),
        (ActionScenario.REVOKED, "REVOKED"),
        (ActionScenario.EXPIRED, "EXPIRED"),
        (ActionScenario.CAPABILITY_REVOKED, "INVALIDATED"),
    ):
        result = run_action_scenario(scenario, clock=FrozenClock())
        assert result.action_state == expected
        assert result.execution_attempt_count == 0
        assert result.real_external_writes == 0
