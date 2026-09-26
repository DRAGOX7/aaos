from __future__ import annotations

from phase9.failure_scenarios import (
    PHASE9_FAILURE_MATRIX,
    FailureFakes,
    FailureResult,
    run_failure_scenario,
)


def test_failure_matrix_is_complete_and_bounded() -> None:
    assert len(PHASE9_FAILURE_MATRIX) >= 24
    names = [scenario.name for scenario in PHASE9_FAILURE_MATRIX]
    assert len(names) == len(set(names))
    assert {scenario.retry_policy for scenario in PHASE9_FAILURE_MATRIX} <= {
        "NONE",
        "TRUSTED_RETRY",
        "RECONCILIATION",
    }
    assert all(len(scenario.recovery) <= 256 for scenario in PHASE9_FAILURE_MATRIX)


def test_each_failure_has_explicit_safe_recovery_and_no_leaky_result() -> None:
    results = tuple(
        run_failure_scenario(scenario, fakes=FailureFakes()) for scenario in PHASE9_FAILURE_MATRIX
    )
    assert all(isinstance(result, FailureResult) for result in results)
    assert all(result.external_provider_calls == 0 for result in results)
    assert all(result.external_writes == 0 for result in results)
    assert all(result.secret_exposed is False for result in results)
    assert all(result.raw_details is None for result in results)
    ambiguous = [
        result
        for result in results
        if result.internal_state in {"UNKNOWN_OUTCOME", "RECONCILIATION_PENDING"}
    ]
    assert ambiguous
    assert all(result.automatic_retry is False for result in ambiguous)


def test_calendar_ambiguity_requires_reconciliation_without_retry() -> None:
    scenario = next(item for item in PHASE9_FAILURE_MATRIX if item.name == "calendar_ambiguous")
    result = run_failure_scenario(scenario, fakes=FailureFakes())
    assert result.internal_state == "UNKNOWN_OUTCOME"
    assert result.user_state == "RECONCILIATION_REQUIRED"
    assert result.automatic_retry is False
    assert result.reconciliation_required is True
