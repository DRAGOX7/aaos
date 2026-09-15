from __future__ import annotations

import builtins
from collections.abc import Mapping

import pytest
from phase6b_evaluation.harness import (
    CaseResult,
    FakeOnlyExecutor,
    mapped_case_ids,
    run_case,
    run_evaluation,
)
from phase6b_evaluation.manifest import (
    FROZEN_MANIFEST,
    FROZEN_MANIFEST_SHA256,
    PHASE6B_CASE_IDS,
    PHASE6B_EVALUATION_SCHEMA_VERSION,
    EvaluationCase,
    EvaluationOwner,
    build_cases,
    canonical_manifest_bytes,
    freeze_manifest,
    owner_counts,
)

_EXPECTED_OWNER_COUNTS = {
    EvaluationOwner.VALIDATION_CANONICALIZATION.value: 6,
    EvaluationOwner.TRUSTED_CONFIGURATION_AUTHORITY.value: 5,
    EvaluationOwner.PRECONDITION_DUPLICATE_PREVENTION.value: 5,
    EvaluationOwner.PROVIDER_ERROR_TAXONOMY.value: 5,
    EvaluationOwner.UNKNOWN_RECONCILIATION.value: 5,
    EvaluationOwner.PRIVACY_IMPORT_SECURITY.value: 4,
}

# Filled from the deterministic manifest implementation before any case execution.
_EXPECTED_MANIFEST_SHA256 = "1ee1682c54051cbece048a9e11c70a8dc7f60849062ea7b5898d4d4b50a34550"


class RecordingFakeExecutor(FakeOnlyExecutor):
    def __init__(self) -> None:
        self.case_ids: list[str] = []

    def execute(self, case: EvaluationCase) -> CaseResult:
        self.case_ids.append(case.case_id)
        return CaseResult(case.case_id, True, "FAKE_ONLY")


def test_exact_case_ids_and_owners() -> None:
    cases = build_cases()
    assert PHASE6B_EVALUATION_SCHEMA_VERSION == "phase6b-google-calendar-v1"
    assert tuple(case.case_id for case in cases) == tuple(
        f"phase6b-cal-{index:03d}" for index in range(1, 31)
    )
    assert PHASE6B_CASE_IDS == tuple(case.case_id for case in cases)
    assert dict(owner_counts(cases)) == _EXPECTED_OWNER_COUNTS


def test_manifest_fingerprint_is_stable() -> None:
    manifest = freeze_manifest()
    assert manifest.manifest_sha256 == _EXPECTED_MANIFEST_SHA256
    assert FROZEN_MANIFEST_SHA256 == _EXPECTED_MANIFEST_SHA256
    assert canonical_manifest_bytes(manifest.cases) == canonical_manifest_bytes(
        FROZEN_MANIFEST.cases
    )


def test_no_duplicate_case_ids() -> None:
    case_ids = tuple(case.case_id for case in build_cases())
    assert len(case_ids) == 30
    assert len(set(case_ids)) == 30


def test_all_30_cases_are_mapped() -> None:
    assert mapped_case_ids() == PHASE6B_CASE_IDS


def test_evaluation_uses_fake_only_executor() -> None:
    case = build_cases()[0]
    executor = RecordingFakeExecutor()

    result = run_case(case, executor)

    assert result.passed is True
    assert result.evidence_kind == "FAKE_ONLY"
    assert executor.case_ids == [case.case_id]


def test_normal_pytest_has_zero_model_rag_and_live_google_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__
    counters = {"model": 0, "rag": 0, "google_live": 0}

    blocked_prefixes = (
        "torch",
        "transformers",
        "sentence_transformers",
        "FlagEmbedding",
        "llama_cpp",
        "aaos.rag",
    )

    def guarded_import(
        name: str,
        globals: Mapping[str, object] | None = None,
        locals: Mapping[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name.startswith(blocked_prefixes):
            if name.startswith("aaos.rag"):
                counters["rag"] += 1
            else:
                counters["model"] += 1
            raise AssertionError(f"model/RAG import attempted by Phase 6B evaluator: {name}")
        if name.startswith(("google.oauth2", "googleapiclient", "google.auth.transport")):
            counters["google_live"] += 1
            raise AssertionError(f"live Google SDK import attempted by evaluator: {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", guarded_import)

    report = run_evaluation(FROZEN_MANIFEST)

    assert report.MODEL_CALL_COUNT == 0
    assert report.RAG_EXECUTION_COUNT == 0
    assert counters == {"model": 0, "rag": 0, "google_live": 0}
    assert report.UNAUTHORIZED_WRITE_COUNT == 0
    assert report.WRONG_CALENDAR_WRITE_COUNT == 0
    assert report.CREDENTIAL_EXPOSURE_COUNT == 0
    assert report.RAW_PROVIDER_RESPONSE_EXPOSURE_COUNT == 0
    assert report.WRITE_RETRY_COUNT == 0


_EXPECTED_REPORT_SHA256 = "a3c75a6b563ff1b9db147eb4ba525cdeab397cc347f172a0fd2f6760aed3ae11"


def test_frozen_evaluation_report_is_stable() -> None:
    report = run_evaluation(FROZEN_MANIFEST)

    assert report.manifest_sha256 == _EXPECTED_MANIFEST_SHA256
    assert report.report_sha256 == _EXPECTED_REPORT_SHA256
    assert len(report.results) == 30
    assert tuple(result.case_id for result in report.results) == PHASE6B_CASE_IDS
    assert all(result.passed for result in report.results)

    assert report.UNAUTHORIZED_WRITE_COUNT == 0
    assert report.WRONG_CALENDAR_WRITE_COUNT == 0
    assert report.CREDENTIAL_EXPOSURE_COUNT == 0
    assert report.RAW_PROVIDER_RESPONSE_EXPOSURE_COUNT == 0
    assert report.WRITE_RETRY_COUNT == 0
    assert report.MODEL_CALL_COUNT == 0
    assert report.RAG_EXECUTION_COUNT == 0


def test_evaluator_crosses_real_fake_only_surfaces() -> None:
    from unittest.mock import patch

    import aaos.approval.service as approval_service
    import aaos.integrations.google_calendar.canonicalization as calendar_canonicalization
    from aaos.integrations.google_calendar.adapter import GoogleCalendarAdapter
    from aaos.integrations.google_calendar.client import FakeGoogleCalendarClient

    original_get = FakeGoogleCalendarClient.get_event
    original_insert = FakeGoogleCalendarClient.insert_event
    original_execute = GoogleCalendarAdapter.execute
    original_reconcile = GoogleCalendarAdapter.reconcile

    with (
        patch.object(
            calendar_canonicalization,
            "canonicalize_calendar_request",
            wraps=calendar_canonicalization.canonicalize_calendar_request,
        ) as canonicalize_spy,
        patch.object(
            FakeGoogleCalendarClient,
            "get_event",
            autospec=True,
            side_effect=original_get,
        ) as get_spy,
        patch.object(
            FakeGoogleCalendarClient,
            "insert_event",
            autospec=True,
            side_effect=original_insert,
        ) as insert_spy,
        patch.object(
            GoogleCalendarAdapter,
            "execute",
            autospec=True,
            side_effect=original_execute,
        ) as execute_spy,
        patch.object(
            GoogleCalendarAdapter,
            "reconcile",
            autospec=True,
            side_effect=original_reconcile,
        ) as reconcile_spy,
        patch.object(
            approval_service,
            "dispatch_claimed_operation",
            wraps=approval_service.dispatch_claimed_operation,
        ) as dispatch_spy,
    ):
        report = run_evaluation(FROZEN_MANIFEST)

    assert len(report.results) == 30

    # The evaluator must actually cross the proven Task 1-5 seams rather than
    # treating frozen membership as sufficient evidence of success.
    assert canonicalize_spy.call_count > 0
    assert get_spy.call_count > 0
    assert insert_spy.call_count > 0
    assert execute_spy.call_count > 0
    assert reconcile_spy.call_count > 0
    assert dispatch_spy.call_count > 0

    evidence_kinds = {result.evidence_kind for result in report.results}
    assert "FAKE_ONLY" not in evidence_kinds
    assert len(evidence_kinds) >= 6


def test_hard_gate_counters_are_aggregated_not_literal_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import phase6b_evaluation.harness as evaluation_harness

    class CounterProbeResult(CaseResult):
        @property
        def UNAUTHORIZED_WRITE_COUNT(self) -> int:
            return int(self.case_id == "phase6b-cal-001")

        @property
        def WRONG_CALENDAR_WRITE_COUNT(self) -> int:
            return int(self.case_id == "phase6b-cal-002")

        @property
        def CREDENTIAL_EXPOSURE_COUNT(self) -> int:
            return int(self.case_id == "phase6b-cal-003")

        @property
        def RAW_PROVIDER_RESPONSE_EXPOSURE_COUNT(self) -> int:
            return int(self.case_id == "phase6b-cal-004")

        @property
        def WRITE_RETRY_COUNT(self) -> int:
            return int(self.case_id == "phase6b-cal-005")

        @property
        def MODEL_CALL_COUNT(self) -> int:
            return int(self.case_id == "phase6b-cal-006")

        @property
        def RAG_EXECUTION_COUNT(self) -> int:
            return int(self.case_id == "phase6b-cal-007")

    class CounterProbeExecutor(FakeOnlyExecutor):
        def execute(self, case: EvaluationCase) -> CaseResult:
            return CounterProbeResult(
                case_id=case.case_id,
                passed=False,
                evidence_kind="COUNTER_PROBE",
            )

    monkeypatch.setattr(
        evaluation_harness,
        "_DeterministicFakeExecutor",
        CounterProbeExecutor,
    )

    report = run_evaluation(FROZEN_MANIFEST)

    assert report.UNAUTHORIZED_WRITE_COUNT == 1
    assert report.WRONG_CALENDAR_WRITE_COUNT == 1
    assert report.CREDENTIAL_EXPOSURE_COUNT == 1
    assert report.RAW_PROVIDER_RESPONSE_EXPOSURE_COUNT == 1
    assert report.WRITE_RETRY_COUNT == 1
    assert report.MODEL_CALL_COUNT == 1
    assert report.RAG_EXECUTION_COUNT == 1


def test_evaluation_rejects_frozen_benchmark_mutation() -> None:
    from dataclasses import replace

    original = FROZEN_MANIFEST.cases[0]

    mutated = replace(
        original,
        expected=replace(
            original.expected,
            value="MUTATED_EXPECTED_INVARIANT",
        ),
    )

    forged = replace(
        FROZEN_MANIFEST,
        cases=(mutated, *FROZEN_MANIFEST.cases[1:]),
    )

    with pytest.raises(ValueError, match="frozen"):
        run_evaluation(forged)
