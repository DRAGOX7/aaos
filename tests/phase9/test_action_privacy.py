from __future__ import annotations

from phase9.action_safety_harness import FrozenClock, run_action_scenario


def test_action_results_are_bounded_and_fake_only() -> None:
    result = run_action_scenario("SUCCESS", clock=FrozenClock())
    assert result.real_external_writes == 0
    assert result.model_calls == 0
    assert result.rag_calls == 0
    assert result.provider_payload is None
    assert result.secret_exposed is False
