from __future__ import annotations

from phase9.action_safety_harness import FrozenClock, run_concurrent_claims


def test_duplicate_claim_has_one_winner_and_one_operation() -> None:
    first, second = run_concurrent_claims(clock=FrozenClock())
    assert sorted((first.claimed, second.claimed)) == [False, True]
    assert first.operation_id is not None or second.operation_id is not None
    assert first.execution_attempt_count + second.execution_attempt_count == 1


def test_dispatch_start_is_durable_before_fake_adapter_call() -> None:
    result = run_concurrent_claims(clock=FrozenClock(), dispatch=True)
    assert result[0].adapter_invoked + result[1].adapter_invoked == 1
    assert result[0].dispatch_started or result[1].dispatch_started
