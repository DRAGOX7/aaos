from __future__ import annotations

from phase9.security_corpus import (
    PHASE9_SECURITY_CORPUS,
    SecurityFixtureResult,
    evaluate_untrusted_content,
)


def test_untrusted_content_is_data_and_cannot_gain_authority() -> None:
    results = tuple(evaluate_untrusted_content(item) for item in PHASE9_SECURITY_CORPUS)
    assert all(isinstance(result, SecurityFixtureResult) for result in results)
    assert all(result.status == "DATA_ONLY" for result in results)
    assert all(result.secret_sentinel_exposed is False for result in results)
    assert all(result.authority_escalated is False for result in results)
    assert all(result.real_provider_calls == 0 for result in results)
    assert all(result.real_external_writes == 0 for result in results)


def test_evaluation_never_executes_fixture_payload() -> None:
    malicious = next(item for item in PHASE9_SECURITY_CORPUS if item.channel == "COMPUTATION")
    result = evaluate_untrusted_content(malicious)
    assert result.executed is False
    assert result.tool_calls == 0
