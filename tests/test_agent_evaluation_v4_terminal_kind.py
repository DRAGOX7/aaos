from __future__ import annotations

import pytest

from aaos.agent.contracts import TerminalResultKind
from aaos.agent.evaluation.contracts import (
    BenchmarkCategory,
    EvaluationLanguage,
    EvaluationSplit,
)
from aaos.agent.evaluation.v2 import V2ExpectedAction
from aaos.agent.evaluation.v3 import V3BenchmarkCase, V3EvaluationOwner
from aaos.agent.evaluation.v4 import (
    V4EvaluationManifest,
    V4EvaluationTrace,
    score_v4_evaluation,
)


def _synthetic_routing_manifest() -> V4EvaluationManifest:
    cases: list[V3BenchmarkCase] = []
    for language in EvaluationLanguage:
        for action in V2ExpectedAction:
            category = (
                BenchmarkCategory.RAG_QUERY
                if action is V2ExpectedAction.RETRIEVE
                else BenchmarkCategory.CONTROL_TERMINAL
            )
            for index in range(16):
                case_id = f"synthetic-{language.value}-{action.value}-{index:02d}"
                cases.append(
                    V3BenchmarkCase(
                        case_id=case_id,
                        split=EvaluationSplit.CALIBRATION,
                        category=category,
                        language=language,
                        request_text=f"synthetic contract test instance {case_id}",
                        owner=V3EvaluationOwner.MODEL_SEMANTIC_ROUTING,
                        expected_action=action,
                        expected_terminal_kind=(
                            TerminalResultKind.CONTROL_OR_CONVERSATIONAL_TERMINAL
                            if action is V2ExpectedAction.TERMINAL_TEXT
                            else None
                        ),
                    )
                )
    return V4EvaluationManifest(
        benchmark_version="phase5-agent-benchmark-v4",
        cases=tuple(cases),
        split=EvaluationSplit.CALIBRATION,
    )


@pytest.mark.parametrize(
    ("terminal_kind_mismatches", "expected_pass"),
    [(3, True), (4, False)],
)
def test_applicable_terminal_kind_is_part_of_global_routing_accuracy(
    terminal_kind_mismatches: int,
    expected_pass: bool,
) -> None:
    manifest = _synthetic_routing_manifest()
    wrong_terminal_kind_ids = {
        case.case_id
        for case in manifest.routing_cases
        if case.expected_action is V2ExpectedAction.TERMINAL_TEXT
    }
    selected_wrong_ids = set(sorted(wrong_terminal_kind_ids)[:terminal_kind_mismatches])

    traces = tuple(
        V4EvaluationTrace(
            case_id=case.case_id,
            observed_action=case.expected_action,
            observed_terminal_kind=(
                TerminalResultKind.GROUNDED_RAG_RESPONSE
                if case.case_id in selected_wrong_ids
                else case.expected_terminal_kind
            ),
        )
        for case in manifest.routing_cases
    )

    metrics = score_v4_evaluation(manifest, traces).model_routing

    assert metrics.correct == 64 - terminal_kind_mismatches
    assert metrics.terminal_correct == 32 - terminal_kind_mismatches
    assert metrics.accuracy_pass is expected_pass
    assert sum(count for _, count in metrics.stratum_correct) == metrics.correct
