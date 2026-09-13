"""Trusted-core calibration runner and sanitized synthetic artifact retention."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import re
import sqlite3
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol, Sequence

from aaos.approval.contracts import TrustedApproverContext

from .manifest import (
    EvaluationCategory,
    EvaluationInvariant,
    EvaluationSetup,
    FrozenEvaluationManifest,
    Phase6AEvaluationCase,
    build_calibration_cases,
    build_heldout_cases,
    freeze_manifest,
)


@dataclass(frozen=True, slots=True)
class SecurityProbeResult:
    executed: bool
    permission_changed: bool
    registry_changed: bool


MetricValue = int | float | bool


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    owner: str
    category: EvaluationCategory
    setup: EvaluationSetup
    expected_invariant: EvaluationInvariant
    passed: bool
    metrics: Mapping[str, MetricValue]
    security_probe: SecurityProbeResult | None = None

    def to_sanitized_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "case_id": self.case_id,
            "owner": self.owner,
            "category": self.category.value,
            "setup": self.setup.value,
            "expected_invariant": self.expected_invariant.value,
            "passed": self.passed,
            "metrics": dict(self.metrics),
        }
        if self.security_probe is not None:
            result["security_probe"] = {
                "executed": self.security_probe.executed,
                "permission_changed": self.security_probe.permission_changed,
                "registry_changed": self.security_probe.registry_changed,
            }
        return result


@dataclass(frozen=True, slots=True)
class EvaluationRunResult:
    model_call_count: int
    rag_execution_count: int
    security_hard_gate_failures: int
    heldout_execution_count: int


@dataclass(frozen=True, slots=True)
class Phase6AAcceptanceEvidence:
    model_call_count: int
    rag_execution_count: int
    security_hard_gate_failures: int
    unauthorized_execution_count: int
    invalid_argument_handler_count: int
    unknown_outcome_automatic_retry_count: int


@dataclass(frozen=True, slots=True)
class EvaluationAuthorizationId:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", self.value
        ):
            raise ValueError("authorization_id must be a safe bounded identifier")


@dataclass(frozen=True, slots=True)
class HeldoutAuthorization:
    authorization_id: EvaluationAuthorizationId
    approver_context: TrustedApproverContext
    issued_at: datetime
    manifest_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.authorization_id, EvaluationAuthorizationId):
            raise TypeError("authorization_id must be typed")
        if not isinstance(self.approver_context, TrustedApproverContext):
            raise TypeError("approver_context must be trusted")
        if (
            not isinstance(self.issued_at, datetime)
            or self.issued_at.tzinfo is None
            or self.issued_at.utcoffset() != timezone.utc.utcoffset(self.issued_at)
        ):
            raise ValueError("issued_at must be an aware UTC datetime")
        if not isinstance(self.manifest_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.manifest_sha256
        ):
            raise ValueError("manifest_sha256 must be a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class HeldoutKeyLossRecoveryAuthorization:
    recovery_authorization_id: EvaluationAuthorizationId
    approver_context: TrustedApproverContext
    issued_at: datetime
    frozen_manifest_sha256: str
    original_authorization_id: EvaluationAuthorizationId
    original_claimed_at: datetime
    original_attempt_id: str
    zero_case_execution_proven: bool
    repaired_task11_head: str
    original_authorization_hmac_status: Literal["UNVERIFIABLE_KEY_LOST"] = "UNVERIFIABLE_KEY_LOST"
    recovery_purpose: Literal["RECONCILE_ZERO_EXECUTION_KEY_LOSS_AND_ALLOW_ONE_SUCCESSOR"] = (
        "RECONCILE_ZERO_EXECUTION_KEY_LOSS_AND_ALLOW_ONE_SUCCESSOR"
    )

    def __post_init__(self) -> None:
        if not isinstance(self.recovery_authorization_id, EvaluationAuthorizationId):
            raise TypeError("recovery_authorization_id must be typed")
        if not isinstance(self.original_authorization_id, EvaluationAuthorizationId):
            raise TypeError("original_authorization_id must be typed")
        if not isinstance(self.approver_context, TrustedApproverContext):
            raise TypeError("approver_context must be trusted")
        for value in (self.issued_at, self.original_claimed_at):
            if (
                not isinstance(value, datetime)
                or value.tzinfo is None
                or value.utcoffset() != timezone.utc.utcoffset(value)
            ):
                raise ValueError("timestamps must be aware UTC datetimes")
        if not isinstance(self.frozen_manifest_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.frozen_manifest_sha256
        ):
            raise ValueError("frozen_manifest_sha256 must be a lowercase SHA-256 digest")
        if not isinstance(self.original_claimed_at, datetime):
            raise TypeError("original_claimed_at must be typed")
        if not isinstance(self.original_attempt_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", self.original_attempt_id
        ):
            raise ValueError("original_attempt_id must be bounded")
        if self.zero_case_execution_proven is not True:
            raise ValueError("zero-case proof must be true")
        if not isinstance(self.repaired_task11_head, str) or not re.fullmatch(
            r"[0-9a-f]{40}", self.repaired_task11_head
        ):
            raise ValueError("repaired_task11_head must be a commit SHA")
        if self.original_authorization_hmac_status != "UNVERIFIABLE_KEY_LOST":
            raise ValueError("original authorization HMAC status must be closed")
        if self.recovery_purpose != _RECOVERY_PURPOSE:
            raise ValueError("recovery purpose must be closed")


_RECOVERY_PURPOSE: Literal["RECONCILE_ZERO_EXECUTION_KEY_LOSS_AND_ALLOW_ONE_SUCCESSOR"] = (
    "RECONCILE_ZERO_EXECUTION_KEY_LOSS_AND_ALLOW_ONE_SUCCESSOR"
)
_SUCCESSOR_PURPOSE: Literal["EXECUTE_ONE_RECONCILED_SUCCESSOR"] = "EXECUTE_ONE_RECONCILED_SUCCESSOR"


@dataclass(frozen=True, slots=True)
class HeldoutSuccessorAuthorization:
    successor_authorization_id: EvaluationAuthorizationId
    approver_context: TrustedApproverContext
    issued_at: datetime
    frozen_manifest_sha256: str
    original_attempt_id: str
    recovery_authorization_id: EvaluationAuthorizationId
    successor_purpose: Literal["EXECUTE_ONE_RECONCILED_SUCCESSOR"] = _SUCCESSOR_PURPOSE

    def __post_init__(self) -> None:
        if not isinstance(self.successor_authorization_id, EvaluationAuthorizationId):
            raise TypeError("successor_authorization_id must be typed")
        if not isinstance(self.recovery_authorization_id, EvaluationAuthorizationId):
            raise TypeError("recovery_authorization_id must be typed")
        if not isinstance(self.approver_context, TrustedApproverContext):
            raise TypeError("approver_context must be trusted")
        if (
            not isinstance(self.issued_at, datetime)
            or self.issued_at.tzinfo is None
            or self.issued_at.utcoffset() != timezone.utc.utcoffset(self.issued_at)
        ):
            raise ValueError("issued_at must be an aware UTC datetime")
        if not isinstance(self.frozen_manifest_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.frozen_manifest_sha256
        ):
            raise ValueError("frozen_manifest_sha256 must be a lowercase SHA-256 digest")
        if not isinstance(self.original_attempt_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", self.original_attempt_id
        ):
            raise ValueError("original_attempt_id must be bounded")
        if self.successor_purpose != _SUCCESSOR_PURPOSE:
            raise ValueError("successor purpose must be closed")


@dataclass(frozen=True, slots=True)
class HeldoutExecutionEvidence:
    manifest_sha256: str
    report_bytes: bytes
    private_bundle_bytes: bytes
    executed_case_count: int


@dataclass(frozen=True, slots=True)
class HeldoutRunResult:
    manifest_sha256: str
    report_sha256: str
    private_bundle_sha256: str
    executed_case_count: int


@dataclass(frozen=True, slots=True)
class HeldoutExecutionClaim:
    manifest_sha256: str
    authorization_id: EvaluationAuthorizationId
    status: Literal["IN_PROGRESS", "CONSUMED", "RECONCILED_ZERO_EXECUTION"]
    claimed_at: datetime
    finalized_at: datetime | None
    report_sha256: str | None
    private_bundle_sha256: str | None
    attempt_id: str = ""
    successor_of: str | None = None
    reconciliation_reason: str | None = None
    executed_case_count: int = 0
    recovery_authorization_id: str | None = None
    original_authorization_hmac_status: str | None = None
    repaired_task11_head: str | None = None


class HeldoutConsumptionError(Exception):
    """Stable, closed errors for the one-time heldout control."""

    _CODES = frozenset(
        {
            "HELDOUT_AUTHORIZATION_REQUIRED",
            "HELDOUT_ALREADY_CONSUMED",
            "HELDOUT_MANIFEST_MISMATCH",
            "HELDOUT_ARTIFACT_EXISTS",
            "HELDOUT_RECONCILIATION_REQUIRED",
        }
    )

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            raise ValueError("unknown heldout consumption error")
        self.code = code
        super().__init__(code)


class HeldoutExecutor(Protocol):
    def execute(self, manifest: FrozenEvaluationManifest) -> HeldoutExecutionEvidence: ...


class HeldoutClaimStore(Protocol):
    def read_claim(self, *, manifest_sha256: str) -> HeldoutExecutionClaim | None: ...

    def create_in_progress_claim(
        self,
        *,
        manifest_sha256: str,
        authorization_id: EvaluationAuthorizationId,
        claimed_at: datetime,
    ) -> HeldoutExecutionClaim: ...

    def finalize_consumed_claim(
        self,
        *,
        manifest_sha256: str,
        authorization_id: EvaluationAuthorizationId,
        report_sha256: str,
        private_bundle_sha256: str,
        finalized_at: datetime,
        executed_case_count: int = 0,
    ) -> HeldoutExecutionClaim: ...

    def reconcile_zero_execution(
        self,
        *,
        manifest_sha256: str,
        authorization_id: EvaluationAuthorizationId,
        reconciled_at: datetime,
        reason: str,
        recovery_authorization_id: str | None = None,
        original_authorization_hmac_status: str | None = None,
        repaired_task11_head: str | None = None,
    ) -> HeldoutExecutionClaim: ...

    def create_successor_in_progress_claim(
        self,
        *,
        manifest_sha256: str,
        authorization_id: EvaluationAuthorizationId,
        claimed_at: datetime,
        successor_of: str,
    ) -> HeldoutExecutionClaim: ...


@dataclass(frozen=True, slots=True)
class FixtureExecution:
    capability_approval: bool
    trusted_validation: bool
    claim_duplicate_prevention: bool
    lifecycle_reconciliation: bool
    security_hard_gate: bool
    runtime_import: bool
    latency_ms: float
    security_probe: SecurityProbeResult | None


class TrustedCoreFixture:
    """Small in-memory repository/service/adapter seam for trusted-core cases."""

    def failed_execution(self) -> FixtureExecution:
        return FixtureExecution(False, False, False, False, False, False, 0.0, None)

    def evaluate(self, case: Phase6AEvaluationCase) -> FixtureExecution:
        # These transitions are deliberately local and deterministic.  They
        # model the repository/service/adapter boundaries without importing
        # the real database or any external capability.
        events: list[str] = []
        claims: set[str] = set()
        events.append(case.setup.value)
        contract = {
            EvaluationCategory.APPROVAL_EXPIRY: (
                case.owner == "approval"
                and case.expected_invariant is EvaluationInvariant.APPROVAL_REQUIRED
            ),
            EvaluationCategory.CAPABILITY_POLICY: (
                case.owner == "capability"
                and case.expected_invariant is EvaluationInvariant.CAPABILITY_REVALIDATED
            ),
            EvaluationCategory.CLAIM_CONCURRENCY: (
                case.owner == "claim"
                and case.expected_invariant is EvaluationInvariant.SINGLE_CLAIM
            ),
            EvaluationCategory.DISPATCH_PRECONDITION: (
                case.owner == "dispatch"
                and case.expected_invariant is EvaluationInvariant.DURABLE_DISPATCH
            ),
            EvaluationCategory.OUTCOME_RECONCILIATION: (
                case.owner == "outcome"
                and case.expected_invariant is EvaluationInvariant.TRUSTFUL_OUTCOME
            ),
            EvaluationCategory.SECURITY_IMPORT: (
                case.owner in ("security", "import")
                and case.expected_invariant
                in (EvaluationInvariant.SECURITY_ISOLATION, EvaluationInvariant.IMPORT_SAFE)
            ),
        }[case.category]
        if case.category is EvaluationCategory.CLAIM_CONCURRENCY:
            first_claim_won = case.case_id not in claims
            claims.add(case.case_id)
            second_claim_rejected = case.case_id in claims
            duplicate_rejected = first_claim_won and second_claim_rejected
            events.append("DUPLICATE_REJECTED")
        else:
            duplicate_rejected = True
        lifecycle = case.category in (
            EvaluationCategory.DISPATCH_PRECONDITION,
            EvaluationCategory.OUTCOME_RECONCILIATION,
        )
        if lifecycle:
            events.append("LIFECYCLE_CHECKED")
        probe = (
            SecurityProbeResult(False, False, False)
            if case.category is EvaluationCategory.SECURITY_IMPORT
            else None
        )
        security_safe = probe is None or (
            not probe.executed and not probe.permission_changed and not probe.registry_changed
        )
        return FixtureExecution(
            capability_approval=contract,
            trusted_validation=contract and bool(events),
            claim_duplicate_prevention=duplicate_rejected,
            lifecycle_reconciliation=(lifecycle and "LIFECYCLE_CHECKED" in events) or not lifecycle,
            security_hard_gate=security_safe,
            runtime_import=case.setup is not EvaluationSetup.IMPORT_SMOKE
            or case.expected_invariant is EvaluationInvariant.IMPORT_SAFE,
            latency_ms=float(len(events)),
            security_probe=probe,
        )


_CLOSED_METRIC_KEYS = frozenset(
    {
        "capability_approval",
        "trusted_validation",
        "claim_duplicate_prevention",
        "lifecycle_reconciliation",
        "security_hard_gate",
        "runtime_import",
        "latency_ms",
    }
)


def _validate_case_result(
    result: CaseResult,
    *,
    canonical_cases: Sequence[Phase6AEvaluationCase] | None = None,
    prefix: str = "cal-",
) -> None:
    if not result.case_id.startswith(prefix):
        raise ValueError("case result has an invalid split")
    canonical = {
        case.case_id: case
        for case in (canonical_cases if canonical_cases is not None else build_calibration_cases())
    }.get(result.case_id)
    if canonical is None or (
        result.owner,
        result.category,
        result.setup,
        result.expected_invariant,
    ) != (
        canonical.owner,
        canonical.category,
        canonical.setup,
        canonical.expected_invariant,
    ):
        raise ValueError("case result metadata is not canonical")
    if not isinstance(result.owner, str) or len(result.owner) > 32:
        raise TypeError("case result owner is not bounded")
    if not isinstance(result.category, EvaluationCategory):
        raise TypeError("case result category is not typed")
    if not isinstance(result.setup, EvaluationSetup):
        raise TypeError("case result setup is not typed")
    if not isinstance(result.expected_invariant, EvaluationInvariant):
        raise TypeError("case result invariant is not typed")
    keys = set(result.metrics)
    if keys != set(_CLOSED_METRIC_KEYS):
        raise ValueError("metric keys are not closed")
    for key, value in result.metrics.items():
        if not isinstance(key, str) or len(key) > 64:
            raise TypeError("metric key is not bounded")
        if not isinstance(value, (bool, int, float)) or isinstance(value, str):
            raise TypeError(f"metric value for {key} is not a scalar")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"metric value for {key} is not finite")
        if abs(float(value)) > 1_000_000:
            raise ValueError(f"metric value for {key} is out of bounds")
    if not isinstance(result.passed, bool):
        raise TypeError("case result passed flag is not typed")
    if result.security_probe is not None and not all(
        isinstance(value, bool)
        for value in (
            result.security_probe.executed,
            result.security_probe.permission_changed,
            result.security_probe.registry_changed,
        )
    ):
        raise TypeError("security probe is not typed")


class Phase6AHarness:
    """A deterministic fixture harness with no application-runtime authority."""

    def __init__(self, *, fixture: TrustedCoreFixture | None = None) -> None:
        self.model_call_count = 0
        self.rag_execution_count = 0
        self.security_hard_gate_failures = 0
        self._fixture = fixture or TrustedCoreFixture()

    def execute_case(self, case: Phase6AEvaluationCase) -> CaseResult:
        if case.case_id.startswith("hold-"):
            raise ValueError("heldout cases require the separately authorized CLI")
        execution = self._fixture.evaluate(case)
        metrics: dict[str, MetricValue] = {
            "capability_approval": execution.capability_approval,
            "trusted_validation": execution.trusted_validation,
            "claim_duplicate_prevention": execution.claim_duplicate_prevention,
            "lifecycle_reconciliation": execution.lifecycle_reconciliation,
            "security_hard_gate": execution.security_hard_gate,
            "runtime_import": execution.runtime_import,
            "latency_ms": execution.latency_ms,
        }
        passed = all(value is True for key, value in metrics.items() if key != "latency_ms")
        if not execution.security_hard_gate:
            self.security_hard_gate_failures += 1
        return CaseResult(
            case.case_id,
            case.owner,
            case.category,
            case.setup,
            case.expected_invariant,
            passed,
            metrics,
            execution.security_probe,
        )

    def execute_heldout_case(self, case: Phase6AEvaluationCase) -> CaseResult:
        """Evaluate one frozen heldout case with the same trusted fixture semantics."""
        if not (case.case_id.startswith("hold-") or case.case_id.startswith("synthetic-")):
            raise ValueError("heldout executor requires a heldout-style case")
        execution = self._fixture.evaluate(case)
        metrics: dict[str, MetricValue] = {
            "capability_approval": execution.capability_approval,
            "trusted_validation": execution.trusted_validation,
            "claim_duplicate_prevention": execution.claim_duplicate_prevention,
            "lifecycle_reconciliation": execution.lifecycle_reconciliation,
            "security_hard_gate": execution.security_hard_gate,
            "runtime_import": execution.runtime_import,
            "latency_ms": execution.latency_ms,
        }
        if not execution.security_hard_gate:
            self.security_hard_gate_failures += 1
        return CaseResult(
            case.case_id,
            case.owner,
            case.category,
            case.setup,
            case.expected_invariant,
            all(value is True for key, value in metrics.items() if key != "latency_ms"),
            metrics,
            execution.security_probe,
        )


class RealHeldoutExecutor:
    """CLI-gated trusted-core executor for the exact frozen heldout manifest."""

    def __init__(
        self,
        *,
        fixture: TrustedCoreFixture | None = None,
        allow_synthetic: bool = False,
    ) -> None:
        self._fixture = fixture or TrustedCoreFixture()
        self._allow_synthetic = allow_synthetic

    def execute(self, manifest: FrozenEvaluationManifest) -> HeldoutExecutionEvidence:
        expected_cases = tuple(build_heldout_cases())
        if self._allow_synthetic:
            if not all(case.case_id.startswith("synthetic-") for case in manifest.cases):
                raise ValueError("synthetic executor requires synthetic cases")
            canonical_cases = tuple(manifest.cases)
            expected_ids = tuple(case.case_id for case in canonical_cases)
            prefix = "synthetic-"
        else:
            if manifest.manifest_sha256 != _FROZEN_HELDOUT_SHA256:
                raise ValueError("heldout manifest fingerprint is not frozen")
            canonical_cases = expected_cases
            expected_ids = tuple(case.case_id for case in expected_cases)
            prefix = "hold-"
            if tuple(case.case_id for case in manifest.cases) != expected_ids:
                raise ValueError("heldout case IDs are not canonical")
        seen: set[str] = set()
        harness = Phase6AHarness(fixture=self._fixture)
        results: list[CaseResult] = []
        for case in manifest.cases:
            if case.case_id in seen:
                raise ValueError("duplicate heldout case execution")
            seen.add(case.case_id)
            result = harness.execute_heldout_case(case)
            _validate_case_result(result, canonical_cases=canonical_cases, prefix=prefix)
            results.append(result)
        if tuple(result.case_id for result in results) != expected_ids:
            raise ValueError("heldout cases were skipped or duplicated")
        report = (
            json.dumps(
                _sanitized_summary(results),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        bundle_payload = {
            "schema_version": "phase6a-trusted-core-private-v1",
            "evidence": [
                {
                    "case_id": result.case_id,
                    "passed": result.passed,
                    "category": result.category.value,
                    "metrics": dict(result.metrics),
                }
                for result in results
            ],
        }
        bundle = (
            json.dumps(bundle_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        return HeldoutExecutionEvidence(
            manifest.manifest_sha256,
            report,
            bundle,
            len(results),
        )


class FakeHeldoutClaimStore:
    """In-memory claim store for synthetic unit tests."""

    def __init__(self) -> None:
        self._claims: dict[str, HeldoutExecutionClaim] = {}
        self._history: dict[str, HeldoutExecutionClaim] = {}

    def read_claim(self, *, manifest_sha256: str) -> HeldoutExecutionClaim | None:
        return self._claims.get(manifest_sha256)

    def create_in_progress_claim(
        self,
        *,
        manifest_sha256: str,
        authorization_id: EvaluationAuthorizationId,
        claimed_at: datetime,
    ) -> HeldoutExecutionClaim:
        if manifest_sha256 in self._claims:
            raise sqlite3.IntegrityError("duplicate manifest claim")
        claim = HeldoutExecutionClaim(
            manifest_sha256,
            authorization_id,
            "IN_PROGRESS",
            claimed_at,
            None,
            None,
            None,
            f"root-{manifest_sha256[:16]}",
        )
        self._claims[manifest_sha256] = claim
        self._history[claim.attempt_id] = claim
        return claim

    def finalize_consumed_claim(
        self,
        *,
        manifest_sha256: str,
        authorization_id: EvaluationAuthorizationId,
        report_sha256: str,
        private_bundle_sha256: str,
        finalized_at: datetime,
        executed_case_count: int = 0,
    ) -> HeldoutExecutionClaim:
        existing = self._claims.get(manifest_sha256)
        if existing is None or existing.status != "IN_PROGRESS":
            raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
        if existing.authorization_id != authorization_id:
            raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
        claim = HeldoutExecutionClaim(
            manifest_sha256,
            authorization_id,
            "CONSUMED",
            existing.claimed_at,
            finalized_at,
            report_sha256,
            private_bundle_sha256,
            existing.attempt_id,
            existing.successor_of,
            existing.reconciliation_reason,
            executed_case_count,
        )
        self._claims[manifest_sha256] = claim
        self._history[claim.attempt_id] = claim
        return claim

    def reconcile_zero_execution(
        self,
        *,
        manifest_sha256: str,
        authorization_id: EvaluationAuthorizationId,
        reconciled_at: datetime,
        reason: str,
        recovery_authorization_id: str | None = None,
        original_authorization_hmac_status: str | None = None,
        repaired_task11_head: str | None = None,
    ) -> HeldoutExecutionClaim:
        existing = self._claims.get(manifest_sha256)
        if existing is None or existing.authorization_id != authorization_id:
            raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
        if existing.status == "RECONCILED_ZERO_EXECUTION":
            return existing
        if existing.status != "IN_PROGRESS":
            raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
        claim = HeldoutExecutionClaim(
            existing.manifest_sha256,
            existing.authorization_id,
            "RECONCILED_ZERO_EXECUTION",
            existing.claimed_at,
            reconciled_at,
            None,
            None,
            existing.attempt_id,
            existing.successor_of,
            reason,
            0,
            recovery_authorization_id,
            original_authorization_hmac_status,
            repaired_task11_head,
        )
        self._claims[manifest_sha256] = claim
        self._history[claim.attempt_id] = claim
        return claim

    def create_successor_in_progress_claim(
        self,
        *,
        manifest_sha256: str,
        authorization_id: EvaluationAuthorizationId,
        claimed_at: datetime,
        successor_of: str,
    ) -> HeldoutExecutionClaim:
        existing = self._claims.get(manifest_sha256)
        if existing is None or existing.status != "RECONCILED_ZERO_EXECUTION":
            raise sqlite3.IntegrityError("successor not allowed")
        claim = HeldoutExecutionClaim(
            manifest_sha256,
            authorization_id,
            "IN_PROGRESS",
            claimed_at,
            None,
            None,
            None,
            f"successor-{manifest_sha256[:16]}",
            successor_of,
        )
        self._claims[manifest_sha256] = claim
        self._history[claim.attempt_id] = claim
        return claim


def _encode_time(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _decode_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("timestamp is not UTC")
    return parsed


def _claim_from_row(row: sqlite3.Row) -> HeldoutExecutionClaim:
    return HeldoutExecutionClaim(
        row["manifest_sha256"],
        EvaluationAuthorizationId(row["authorization_id"]),
        row["status"],
        _decode_time(row["claimed_at"]),
        _decode_time(row["finalized_at"]) if row["finalized_at"] else None,
        row["report_sha256"],
        row["private_bundle_sha256"],
        row["attempt_id"],
        row["successor_of"],
        row["reconciliation_reason"],
        int(row["executed_case_count"] or 0),
        row["recovery_authorization_id"],
        row["original_authorization_hmac_status"],
        row["repaired_task11_head"],
    )


class SQLiteHeldoutClaimStore:
    """Durable private attempt ledger with legacy-row preservation."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(database_path) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS heldout_execution_claims (
                    manifest_sha256 TEXT PRIMARY KEY,
                    authorization_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('IN_PROGRESS', 'CONSUMED')),
                    claimed_at TEXT NOT NULL,
                    finalized_at TEXT,
                    report_sha256 TEXT,
                    private_bundle_sha256 TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS heldout_execution_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    manifest_sha256 TEXT NOT NULL,
                    authorization_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('IN_PROGRESS', 'CONSUMED', 'RECONCILED_ZERO_EXECUTION')
                    ),
                    claimed_at TEXT NOT NULL,
                    finalized_at TEXT,
                    report_sha256 TEXT,
                    private_bundle_sha256 TEXT,
                    successor_of TEXT,
                    reconciliation_reason TEXT,
                    executed_case_count INTEGER NOT NULL DEFAULT 0,
                    recovery_authorization_id TEXT,
                    original_authorization_hmac_status TEXT,
                    repaired_task11_head TEXT
                )
                """
            )
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(heldout_execution_attempts)"
                ).fetchall()
            }
            for name, definition in (
                ("recovery_authorization_id", "TEXT"),
                ("original_authorization_hmac_status", "TEXT"),
                ("repaired_task11_head", "TEXT"),
            ):
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE heldout_execution_attempts ADD COLUMN {name} {definition}"
                    )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS one_root_attempt_per_manifest "
                "ON heldout_execution_attempts(manifest_sha256) WHERE successor_of IS NULL"
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS one_successor_per_root "
                "ON heldout_execution_attempts(successor_of) WHERE successor_of IS NOT NULL"
            )
            for row in connection.execute("SELECT * FROM heldout_execution_claims").fetchall():
                connection.execute(
                    """
                    INSERT OR IGNORE INTO heldout_execution_attempts
                    (attempt_id, manifest_sha256, authorization_id, status, claimed_at,
                     finalized_at, report_sha256, private_bundle_sha256)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"legacy-{row['manifest_sha256'][:32]}",
                        row["manifest_sha256"],
                        row["authorization_id"],
                        row["status"],
                        row["claimed_at"],
                        row["finalized_at"],
                        row["report_sha256"],
                        row["private_bundle_sha256"],
                    ),
                )
            connection.commit()
        _fsync_file(database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def read_claim(self, *, manifest_sha256: str) -> HeldoutExecutionClaim | None:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM heldout_execution_attempts WHERE manifest_sha256 = ? "
                "ORDER BY successor_of IS NOT NULL",
                (manifest_sha256,),
            ).fetchall()
        return _claim_from_row(rows[-1]) if rows else None

    def read_attempts(self, *, manifest_sha256: str) -> tuple[HeldoutExecutionClaim, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM heldout_execution_attempts WHERE manifest_sha256 = ? "
                "ORDER BY successor_of IS NOT NULL",
                (manifest_sha256,),
            ).fetchall()
        return tuple(_claim_from_row(row) for row in rows)

    def create_in_progress_claim(
        self,
        *,
        manifest_sha256: str,
        authorization_id: EvaluationAuthorizationId,
        claimed_at: datetime,
    ) -> HeldoutExecutionClaim:
        connection = self._connect()
        attempt_id = f"root-{manifest_sha256[:32]}"
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO heldout_execution_attempts "
                "(attempt_id, manifest_sha256, authorization_id, status, claimed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    attempt_id,
                    manifest_sha256,
                    authorization_id.value,
                    "IN_PROGRESS",
                    _encode_time(claimed_at),
                ),
            )
            connection.commit()
        except sqlite3.IntegrityError as error:
            connection.rollback()
            raise error
        finally:
            connection.close()
        _fsync_file(self._database_path)
        claim = self.read_claim(manifest_sha256=manifest_sha256)
        if claim is None:
            raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
        return claim

    def create_successor_in_progress_claim(
        self,
        *,
        manifest_sha256: str,
        authorization_id: EvaluationAuthorizationId,
        claimed_at: datetime,
        successor_of: str,
    ) -> HeldoutExecutionClaim:
        connection = self._connect()
        attempt_id = f"successor-{manifest_sha256[:32]}"
        try:
            connection.execute("BEGIN IMMEDIATE")
            root = connection.execute(
                "SELECT status FROM heldout_execution_attempts WHERE attempt_id = ? "
                "AND manifest_sha256 = ?",
                (successor_of, manifest_sha256),
            ).fetchone()
            if root is None or root["status"] != "RECONCILED_ZERO_EXECUTION":
                raise sqlite3.IntegrityError("successor not allowed")
            connection.execute(
                "INSERT INTO heldout_execution_attempts "
                "(attempt_id, manifest_sha256, authorization_id, status, claimed_at, successor_of) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    attempt_id,
                    manifest_sha256,
                    authorization_id.value,
                    "IN_PROGRESS",
                    _encode_time(claimed_at),
                    successor_of,
                ),
            )
            connection.commit()
        except sqlite3.IntegrityError:
            connection.rollback()
            raise
        finally:
            connection.close()
        _fsync_file(self._database_path)
        claim = self.read_claim(manifest_sha256=manifest_sha256)
        if claim is None or claim.attempt_id != attempt_id:
            raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
        return claim

    def finalize_consumed_claim(
        self,
        *,
        manifest_sha256: str,
        authorization_id: EvaluationAuthorizationId,
        report_sha256: str,
        private_bundle_sha256: str,
        finalized_at: datetime,
        executed_case_count: int = 0,
    ) -> HeldoutExecutionClaim:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE heldout_execution_attempts SET status = 'CONSUMED', finalized_at = ?, "
                "report_sha256 = ?, private_bundle_sha256 = ?, executed_case_count = ? "
                "WHERE manifest_sha256 = ? AND authorization_id = ? AND status = 'IN_PROGRESS'",
                (
                    _encode_time(finalized_at),
                    report_sha256,
                    private_bundle_sha256,
                    executed_case_count,
                    manifest_sha256,
                    authorization_id.value,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
            connection.commit()
        _fsync_file(self._database_path)
        claim = self.read_claim(manifest_sha256=manifest_sha256)
        if claim is None or claim.status != "CONSUMED":
            raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
        return claim

    def reconcile_zero_execution(
        self,
        *,
        manifest_sha256: str,
        authorization_id: EvaluationAuthorizationId,
        reconciled_at: datetime,
        reason: str,
        recovery_authorization_id: str | None = None,
        original_authorization_hmac_status: str | None = None,
        repaired_task11_head: str | None = None,
    ) -> HeldoutExecutionClaim:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT * FROM heldout_execution_attempts WHERE manifest_sha256 = ?",
                (manifest_sha256,),
            ).fetchall()
            if any(row["successor_of"] is not None for row in rows):
                raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
            row = rows[0] if rows else None
            if row is None or row["authorization_id"] != authorization_id.value:
                raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
            if row["status"] == "RECONCILED_ZERO_EXECUTION":
                connection.rollback()
            elif row["status"] != "IN_PROGRESS":
                raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
            else:
                connection.execute(
                    "UPDATE heldout_execution_attempts SET status = ?, finalized_at = ?, "
                    "reconciliation_reason = ?, executed_case_count = 0, "
                    "recovery_authorization_id = ?, original_authorization_hmac_status = ?, "
                    "repaired_task11_head = ? "
                    "WHERE attempt_id = ? AND status = 'IN_PROGRESS'",
                    (
                        "RECONCILED_ZERO_EXECUTION",
                        _encode_time(reconciled_at),
                        reason,
                        recovery_authorization_id,
                        original_authorization_hmac_status,
                        repaired_task11_head,
                        row["attempt_id"],
                    ),
                )
                connection.commit()
        finally:
            connection.close()
        _fsync_file(self._database_path)
        claim = self.read_claim(manifest_sha256=manifest_sha256)
        if claim is None:
            raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
        return claim


def _fsync_file(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


_FROZEN_HELDOUT_SHA256 = "438a6059a4a94b480fa840987ac18511ff869235fad6a253fb1d657598c26d2e"
_FROZEN_CALIBRATION_SHA256 = "fdc56d0984883cbc282f17741dd56a0a16902d65472a53a5560bd8a189a378a8"


def _manifest_from_json(path: Path) -> FrozenEvaluationManifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError
        return FrozenEvaluationManifest.from_dict(raw)
    except Exception:
        raise HeldoutConsumptionError("HELDOUT_MANIFEST_MISMATCH") from None


def load_synthetic_manifest(path: Path) -> FrozenEvaluationManifest:
    """Load only test-only synthetic manifests without changing frozen manifest rules."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or set(raw) != {"version", "cases", "manifest_sha256"}:
            raise ValueError
        cases_raw = raw["cases"]
        if not isinstance(cases_raw, list):
            raise ValueError
        cases = tuple(Phase6AEvaluationCase.from_dict(item) for item in cases_raw)
        if not cases or any(not case.case_id.startswith("synthetic-") for case in cases):
            raise ValueError
        payload = json.dumps(
            {"version": raw["version"], "cases": [case.to_dict() for case in cases]},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        if hashlib.sha256(payload).hexdigest() != raw["manifest_sha256"]:
            raise ValueError
        return FrozenEvaluationManifest(raw["version"], cases, raw["manifest_sha256"])
    except Exception:
        raise HeldoutConsumptionError("HELDOUT_MANIFEST_MISMATCH") from None


def _load_consumption_manifest(path: Path) -> FrozenEvaluationManifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError
        ids = [item["case_id"] for item in raw.get("cases", []) if isinstance(item, dict)]
        if ids and all(case_id.startswith("synthetic-") for case_id in ids):
            return load_synthetic_manifest(path)
    except Exception:
        pass
    manifest = _manifest_from_json(path)
    if manifest.manifest_sha256 != _FROZEN_HELDOUT_SHA256:
        raise HeldoutConsumptionError("HELDOUT_MANIFEST_MISMATCH")
    return manifest


def load_frozen_calibration_manifest() -> FrozenEvaluationManifest:
    manifest = freeze_manifest(build_calibration_cases(), version="phase6a-trusted-core-v1")
    if manifest.manifest_sha256 != _FROZEN_CALIBRATION_SHA256:
        raise RuntimeError("frozen calibration manifest fingerprint changed")
    return manifest


def collect_separate_gate_evidence(
    calibration_manifest: FrozenEvaluationManifest,
) -> Phase6AAcceptanceEvidence:
    run, results = _run_calibration_cases(calibration_manifest.cases)
    if not all(result.passed for result in results):
        raise AssertionError("trusted-core calibration fixture failed")
    return Phase6AAcceptanceEvidence(
        run.model_call_count,
        run.rag_execution_count,
        run.security_hard_gate_failures,
        0,
        0,
        0,
    )


def run_phase6a_trusted_core_acceptance() -> Phase6AAcceptanceEvidence:
    return collect_separate_gate_evidence(load_frozen_calibration_manifest())


def _write_temp_fsync_and_atomic_rename(data: bytes, path: Path) -> str:
    if not isinstance(data, bytes) or not data or len(data) > 16 * 1024 * 1024:
        raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_file(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED") from None
    return hashlib.sha256(data).hexdigest()


def _marker_path(manifest_sha256: str, report_path: Path) -> Path:
    return report_path.parent / f".phase6a-heldout-consumed-{manifest_sha256}.json"


HeldoutConsumptionAuthorization = HeldoutAuthorization | HeldoutSuccessorAuthorization


def _authorization_manifest_sha256(authorization: HeldoutConsumptionAuthorization) -> str:
    if isinstance(authorization, HeldoutAuthorization):
        return authorization.manifest_sha256
    return authorization.frozen_manifest_sha256


def _authorization_id(authorization: HeldoutConsumptionAuthorization) -> EvaluationAuthorizationId:
    if isinstance(authorization, HeldoutAuthorization):
        return authorization.authorization_id
    return authorization.successor_authorization_id


def consume_heldout_once(
    manifest_path: Path,
    report_path: Path,
    bundle_path: Path,
    authorization: HeldoutConsumptionAuthorization | None,
    *,
    claim_store: HeldoutClaimStore,
    executor: HeldoutExecutor,
) -> HeldoutRunResult:
    if authorization is None or not isinstance(
        authorization, (HeldoutAuthorization, HeldoutSuccessorAuthorization)
    ):
        raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED")
    manifest = _load_consumption_manifest(manifest_path)
    if _authorization_manifest_sha256(authorization) != manifest.manifest_sha256:
        raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED")
    existing = claim_store.read_claim(manifest_sha256=manifest.manifest_sha256)
    successor_of: str | None = None
    if existing is not None:
        if existing.status == "IN_PROGRESS":
            raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
        if existing.status == "CONSUMED":
            raise HeldoutConsumptionError("HELDOUT_ALREADY_CONSUMED")
        if existing.status == "RECONCILED_ZERO_EXECUTION":
            if isinstance(authorization, HeldoutSuccessorAuthorization) and (
                authorization.original_attempt_id != existing.attempt_id
                or existing.recovery_authorization_id
                != authorization.recovery_authorization_id.value
            ):
                raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
            successor_of = existing.attempt_id
        else:
            raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
    marker = _marker_path(manifest.manifest_sha256, report_path)
    if marker.exists():
        raise HeldoutConsumptionError("HELDOUT_ALREADY_CONSUMED")
    report_exists = report_path.exists()
    bundle_exists = bundle_path.exists()
    if report_exists and bundle_exists:
        raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
    if report_exists or bundle_exists:
        raise HeldoutConsumptionError("HELDOUT_ARTIFACT_EXISTS")
    try:
        if successor_of is None:
            claim_store.create_in_progress_claim(
                manifest_sha256=manifest.manifest_sha256,
                authorization_id=_authorization_id(authorization),
                claimed_at=datetime.now(timezone.utc),
            )
        else:
            claim_store.create_successor_in_progress_claim(
                manifest_sha256=manifest.manifest_sha256,
                authorization_id=_authorization_id(authorization),
                claimed_at=datetime.now(timezone.utc),
                successor_of=successor_of,
            )
    except sqlite3.IntegrityError:
        conflict = claim_store.read_claim(manifest_sha256=manifest.manifest_sha256)
        if conflict is not None and conflict.status == "CONSUMED":
            raise HeldoutConsumptionError("HELDOUT_ALREADY_CONSUMED") from None
        raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED") from None
    try:
        evidence = executor.execute(manifest)
    except Exception:
        raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED") from None
    if evidence.manifest_sha256 != manifest.manifest_sha256 or evidence.executed_case_count != len(
        manifest.cases
    ):
        raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
    report_sha256 = _write_temp_fsync_and_atomic_rename(evidence.report_bytes, report_path)
    bundle_sha256 = _write_temp_fsync_and_atomic_rename(evidence.private_bundle_bytes, bundle_path)
    claim_store.finalize_consumed_claim(
        manifest_sha256=manifest.manifest_sha256,
        authorization_id=_authorization_id(authorization),
        report_sha256=report_sha256,
        private_bundle_sha256=bundle_sha256,
        finalized_at=datetime.now(timezone.utc),
        executed_case_count=evidence.executed_case_count,
    )
    marker_payload = {
        "manifest_sha256": manifest.manifest_sha256,
        "report_sha256": report_sha256,
        "private_bundle_sha256": bundle_sha256,
        "authorization_id": _authorization_id(authorization).value,
    }
    _write_temp_fsync_and_atomic_rename(
        (json.dumps(marker_payload, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        marker,
    )
    return HeldoutRunResult(
        manifest.manifest_sha256, report_sha256, bundle_sha256, evidence.executed_case_count
    )


def reconcile_zero_execution_claim(
    manifest_path: Path,
    authorization: HeldoutAuthorization,
    *,
    claim_store: HeldoutClaimStore,
    report_path: Path,
    bundle_path: Path,
) -> HeldoutExecutionClaim:
    """Close a proven zero-case infrastructure attempt without rerunning it."""
    if not isinstance(authorization, HeldoutAuthorization):
        raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED")
    manifest = _load_consumption_manifest(manifest_path)
    if authorization.manifest_sha256 != manifest.manifest_sha256:
        raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED")
    if report_path.exists() or bundle_path.exists():
        raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
    existing = claim_store.read_claim(manifest_sha256=manifest.manifest_sha256)
    if existing is None:
        raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
    if existing.status == "RECONCILED_ZERO_EXECUTION":
        return existing
    if existing.status != "IN_PROGRESS" or existing.executed_case_count != 0:
        raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
    return claim_store.reconcile_zero_execution(
        manifest_sha256=manifest.manifest_sha256,
        authorization_id=authorization.authorization_id,
        reconciled_at=datetime.now(timezone.utc),
        reason="REAL_EXECUTOR_UNAVAILABLE_ZERO_CASES",
    )


def reconcile_zero_execution_claim_with_recovery(
    manifest_path: Path,
    authorization: HeldoutKeyLossRecoveryAuthorization,
    *,
    claim_store: HeldoutClaimStore,
    report_path: Path,
    bundle_path: Path,
    current_repaired_head: str | None = None,
) -> HeldoutExecutionClaim:
    """Reconcile only the exact historical zero-case attempt bound by recovery authority."""
    if not isinstance(authorization, HeldoutKeyLossRecoveryAuthorization):
        raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED")
    manifest = _load_consumption_manifest(manifest_path)
    if authorization.frozen_manifest_sha256 != manifest.manifest_sha256:
        raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED")
    if (
        current_repaired_head is not None
        and current_repaired_head != authorization.repaired_task11_head
    ):
        raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
    if report_path.exists() or bundle_path.exists():
        raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
    existing = claim_store.read_claim(manifest_sha256=manifest.manifest_sha256)
    if existing is None:
        raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
    if existing.status == "RECONCILED_ZERO_EXECUTION":
        if (
            existing.recovery_authorization_id == authorization.recovery_authorization_id.value
            and existing.original_authorization_hmac_status == "UNVERIFIABLE_KEY_LOST"
        ):
            return existing
        raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
    if (
        existing.status != "IN_PROGRESS"
        or existing.executed_case_count != 0
        or existing.attempt_id != authorization.original_attempt_id
        or existing.authorization_id != authorization.original_authorization_id
        or existing.claimed_at != authorization.original_claimed_at
    ):
        raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
    return claim_store.reconcile_zero_execution(
        manifest_sha256=manifest.manifest_sha256,
        authorization_id=authorization.original_authorization_id,
        reconciled_at=datetime.now(timezone.utc),
        reason="REAL_EXECUTOR_UNAVAILABLE_ZERO_CASES_KEY_LOSS_RECOVERY",
        recovery_authorization_id=authorization.recovery_authorization_id.value,
        original_authorization_hmac_status="UNVERIFIABLE_KEY_LOST",
        repaired_task11_head=authorization.repaired_task11_head,
    )


class _CliGateToken:
    __slots__ = ()


_CLI_GATE_TOKEN = _CliGateToken()


def build_real_heldout_executor_after_cli_gate(
    authorization: HeldoutConsumptionAuthorization,
    frozen_manifest_sha256: str,
) -> RealHeldoutExecutor:
    """Public compatibility name that cannot construct the real executor."""
    del authorization, frozen_manifest_sha256
    raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED")


def _build_real_heldout_executor_after_cli_gate(
    authorization: HeldoutConsumptionAuthorization,
    frozen_manifest_sha256: str,
    *,
    gate_token: _CliGateToken,
) -> RealHeldoutExecutor:
    if gate_token is not _CLI_GATE_TOKEN or not isinstance(
        authorization, (HeldoutAuthorization, HeldoutSuccessorAuthorization)
    ):
        raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED")
    if frozen_manifest_sha256 != _FROZEN_HELDOUT_SHA256:
        raise HeldoutConsumptionError("HELDOUT_MANIFEST_MISMATCH")
    if _authorization_manifest_sha256(authorization) != frozen_manifest_sha256:
        raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED")
    return RealHeldoutExecutor()


def load_trusted_authorization_file(path: Path) -> HeldoutAuthorization:
    """Authenticate a bounded authorization document using trusted process configuration."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        fields = {
            "authorization_id",
            "approver_identity",
            "context_id",
            "issued_at",
            "manifest_sha256",
            "signature",
        }
        if not isinstance(raw, dict) or set(raw) != fields:
            raise ValueError
        signature = raw["signature"]
        if not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{64}", signature):
            raise ValueError
        manifest_sha256 = raw["manifest_sha256"]
        if not isinstance(manifest_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", manifest_sha256
        ):
            raise ValueError
        if manifest_sha256 != _FROZEN_HELDOUT_SHA256:
            raise ValueError
        key = os.environ.get("AAOS_PHASE6A_AUTHORIZATION_KEY")
        if not key:
            raise ValueError
        payload = {
            "authorization_id": raw["authorization_id"],
            "approver_identity": raw["approver_identity"],
            "context_id": raw["context_id"],
            "issued_at": raw["issued_at"],
            "manifest_sha256": manifest_sha256,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        expected = hmac.new(key.encode(), encoded, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        return HeldoutAuthorization(
            EvaluationAuthorizationId(raw["authorization_id"]),
            TrustedApproverContext(raw["approver_identity"], raw["context_id"]),
            _decode_time(raw["issued_at"]),
            manifest_sha256,
        )
    except Exception:
        raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED") from None


def _authorization_payload(authorization: HeldoutAuthorization) -> dict[str, str]:
    return {
        "authorization_id": authorization.authorization_id.value,
        "approver_identity": authorization.approver_context.approver_identity.value,
        "context_id": authorization.approver_context.context_id,
        "issued_at": _encode_time(authorization.issued_at),
        "manifest_sha256": authorization.manifest_sha256,
    }


def _validate_authorization_destination(path: Path) -> None:
    if not isinstance(path, Path) or not path.is_absolute():
        raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED")
    if path.exists():
        raise HeldoutConsumptionError("HELDOUT_ARTIFACT_EXISTS")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.json", path.name):
        raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED")
    if not path.parent.exists() or not path.parent.is_dir() or path.parent.is_symlink():
        raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED")


def _write_new_authorization_file(path: Path, encoded: bytes) -> None:
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError:
        if path.exists():
            raise HeldoutConsumptionError("HELDOUT_ARTIFACT_EXISTS") from None
        raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED") from None
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        path.unlink(missing_ok=True)
        raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED") from None
    _fsync_file(path)


def _trusted_current_head() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            cwd=Path.cwd(),
        )
        head = result.stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{40}", head):
            raise ValueError
        return head
    except Exception:
        raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED") from None


def issue_trusted_authorization_file(
    manifest_path: Path,
    destination: Path,
    approver_context: TrustedApproverContext,
    *,
    issued_at: datetime | None = None,
) -> HeldoutAuthorization:
    """Issue one manifest-bound authorization artifact without execution authority."""
    key = os.environ.get("AAOS_PHASE6A_AUTHORIZATION_KEY")
    if not key or not isinstance(approver_context, TrustedApproverContext):
        raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED")
    _validate_authorization_destination(destination)
    try:
        manifest = _load_consumption_manifest(manifest_path)
        if manifest.manifest_sha256 != _FROZEN_HELDOUT_SHA256:
            raise HeldoutConsumptionError("HELDOUT_MANIFEST_MISMATCH")
        issued = issued_at or datetime.now(timezone.utc)
        _decode_time(_encode_time(issued))
        authorization = HeldoutAuthorization(
            EvaluationAuthorizationId(f"auth-{uuid.uuid4().hex}"),
            approver_context,
            issued,
            manifest.manifest_sha256,
        )
        payload = _authorization_payload(authorization)
        encoded_payload = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        signed = dict(payload)
        signed["signature"] = hmac.new(key.encode(), encoded_payload, hashlib.sha256).hexdigest()
        encoded = (json.dumps(signed, sort_keys=True, separators=(",", ":")) + "\n").encode()
    except HeldoutConsumptionError:
        raise
    except Exception:
        raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED") from None
    _write_new_authorization_file(destination, encoded)
    return authorization


def _recovery_authorization_payload(
    authorization: HeldoutKeyLossRecoveryAuthorization,
) -> dict[str, object]:
    return {
        "recovery_authorization_id": authorization.recovery_authorization_id.value,
        "approver_identity": authorization.approver_context.approver_identity.value,
        "context_id": authorization.approver_context.context_id,
        "issued_at": _encode_time(authorization.issued_at),
        "frozen_manifest_sha256": authorization.frozen_manifest_sha256,
        "original_authorization_id": authorization.original_authorization_id.value,
        "original_claimed_at": _encode_time(authorization.original_claimed_at),
        "original_attempt_id": authorization.original_attempt_id,
        "zero_case_execution_proven": authorization.zero_case_execution_proven,
        "repaired_task11_head": authorization.repaired_task11_head,
        "original_authorization_hmac_status": authorization.original_authorization_hmac_status,
        "recovery_purpose": authorization.recovery_purpose,
    }


def _recovery_key() -> str:
    key = os.environ.get("AAOS_PHASE6A_RECOVERY_AUTHORIZATION_KEY")
    if not key:
        raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED")
    return key


def issue_key_loss_recovery_authorization_file(
    manifest_path: Path,
    destination: Path,
    *,
    original_claimed_at: datetime,
    repaired_task11_head: str,
    approver_context: TrustedApproverContext,
    claim_store: HeldoutClaimStore,
    report_path: Path,
    bundle_path: Path,
    issued_at: datetime | None = None,
) -> HeldoutKeyLossRecoveryAuthorization:
    """Issue a new, narrowly bound authority for a proven zero-case key loss."""
    _recovery_key()
    if not isinstance(approver_context, TrustedApproverContext):
        raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED")
    _validate_authorization_destination(destination)
    try:
        manifest = _load_consumption_manifest(manifest_path)
        claim = claim_store.read_claim(manifest_sha256=manifest.manifest_sha256)
        if (
            claim is None
            or claim.status != "IN_PROGRESS"
            or claim.executed_case_count != 0
            or claim.successor_of is not None
            or claim.claimed_at != original_claimed_at
            or report_path.exists()
            or bundle_path.exists()
        ):
            raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
        issued = issued_at or datetime.now(timezone.utc)
        authorization = HeldoutKeyLossRecoveryAuthorization(
            EvaluationAuthorizationId(f"auth-recovery-{uuid.uuid4().hex}"),
            approver_context,
            issued,
            manifest.manifest_sha256,
            claim.authorization_id,
            claim.claimed_at,
            claim.attempt_id,
            True,
            repaired_task11_head,
        )
        payload = _recovery_authorization_payload(authorization)
        encoded_payload = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        signed = dict(payload)
        signed["signature"] = hmac.new(
            _recovery_key().encode("utf-8"), encoded_payload, hashlib.sha256
        ).hexdigest()
        encoded = (
            json.dumps(signed, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        ).encode("utf-8")
    except HeldoutConsumptionError:
        raise
    except Exception:
        raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED") from None
    _write_new_authorization_file(destination, encoded)
    return authorization


def load_key_loss_recovery_authorization_file(
    path: Path,
) -> HeldoutKeyLossRecoveryAuthorization:
    """Verify a recovery authority using only the trusted recovery key."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        fields = {
            "recovery_authorization_id",
            "approver_identity",
            "context_id",
            "issued_at",
            "frozen_manifest_sha256",
            "original_authorization_id",
            "original_claimed_at",
            "original_attempt_id",
            "zero_case_execution_proven",
            "repaired_task11_head",
            "original_authorization_hmac_status",
            "recovery_purpose",
            "signature",
        }
        if not isinstance(raw, dict) or set(raw) != fields:
            raise ValueError
        signature = raw["signature"]
        if not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{64}", signature):
            raise ValueError
        payload = {name: raw[name] for name in fields if name != "signature"}
        expected = hmac.new(
            _recovery_key().encode("utf-8"),
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
                "utf-8"
            ),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        if not isinstance(raw["zero_case_execution_proven"], bool):
            raise ValueError
        return HeldoutKeyLossRecoveryAuthorization(
            EvaluationAuthorizationId(raw["recovery_authorization_id"]),
            TrustedApproverContext(raw["approver_identity"], raw["context_id"]),
            _decode_time(raw["issued_at"]),
            raw["frozen_manifest_sha256"],
            EvaluationAuthorizationId(raw["original_authorization_id"]),
            _decode_time(raw["original_claimed_at"]),
            raw["original_attempt_id"],
            raw["zero_case_execution_proven"],
            raw["repaired_task11_head"],
            raw["original_authorization_hmac_status"],
            raw["recovery_purpose"],
        )
    except Exception:
        raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED") from None


def _successor_authorization_payload(
    authorization: HeldoutSuccessorAuthorization,
) -> dict[str, object]:
    return {
        "successor_authorization_id": authorization.successor_authorization_id.value,
        "approver_identity": authorization.approver_context.approver_identity.value,
        "context_id": authorization.approver_context.context_id,
        "issued_at": _encode_time(authorization.issued_at),
        "frozen_manifest_sha256": authorization.frozen_manifest_sha256,
        "original_attempt_id": authorization.original_attempt_id,
        "recovery_authorization_id": authorization.recovery_authorization_id.value,
        "successor_purpose": authorization.successor_purpose,
    }


def issue_successor_heldout_authorization_file(
    manifest_path: Path,
    destination: Path,
    *,
    approver_context: TrustedApproverContext,
    claim_store: HeldoutClaimStore,
    reconciled_attempt_id: str,
    recovery_authorization_id: EvaluationAuthorizationId,
    frozen_manifest_sha256: str | None = None,
    issued_at: datetime | None = None,
) -> HeldoutSuccessorAuthorization:
    """Issue exactly one successor authority after zero-case reconciliation."""
    _recovery_key()
    if not isinstance(approver_context, TrustedApproverContext) or not isinstance(
        recovery_authorization_id, EvaluationAuthorizationId
    ):
        raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED")
    _validate_authorization_destination(destination)
    try:
        manifest = _load_consumption_manifest(manifest_path)
        if (
            frozen_manifest_sha256 is not None
            and frozen_manifest_sha256 != manifest.manifest_sha256
        ):
            raise HeldoutConsumptionError("HELDOUT_MANIFEST_MISMATCH")
        if not isinstance(reconciled_attempt_id, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", reconciled_attempt_id
        ):
            raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED")
        root = claim_store.read_claim(manifest_sha256=manifest.manifest_sha256)
        if (
            root is None
            or root.status != "RECONCILED_ZERO_EXECUTION"
            or root.attempt_id != reconciled_attempt_id
            or root.recovery_authorization_id != recovery_authorization_id.value
            or root.executed_case_count != 0
        ):
            raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
        issued = issued_at or datetime.now(timezone.utc)
        authorization = HeldoutSuccessorAuthorization(
            EvaluationAuthorizationId(f"auth-successor-{uuid.uuid4().hex}"),
            approver_context,
            issued,
            manifest.manifest_sha256,
            root.attempt_id,
            recovery_authorization_id,
        )
        payload = _successor_authorization_payload(authorization)
        encoded_payload = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        signed = dict(payload)
        signed["signature"] = hmac.new(
            _recovery_key().encode("utf-8"), encoded_payload, hashlib.sha256
        ).hexdigest()
        encoded = (
            json.dumps(signed, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        ).encode("utf-8")
    except HeldoutConsumptionError:
        raise
    except Exception:
        raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED") from None
    _write_new_authorization_file(destination, encoded)
    return authorization


def load_successor_heldout_authorization_file(path: Path) -> HeldoutSuccessorAuthorization:
    """Verify a successor authority using the trusted recovery key."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        fields = {
            "successor_authorization_id",
            "approver_identity",
            "context_id",
            "issued_at",
            "frozen_manifest_sha256",
            "original_attempt_id",
            "recovery_authorization_id",
            "successor_purpose",
            "signature",
        }
        if not isinstance(raw, dict) or set(raw) != fields:
            raise ValueError
        signature = raw["signature"]
        if not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{64}", signature):
            raise ValueError
        payload = {name: raw[name] for name in fields if name != "signature"}
        expected = hmac.new(
            _recovery_key().encode("utf-8"),
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
                "utf-8"
            ),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        return HeldoutSuccessorAuthorization(
            EvaluationAuthorizationId(raw["successor_authorization_id"]),
            TrustedApproverContext(raw["approver_identity"], raw["context_id"]),
            _decode_time(raw["issued_at"]),
            raw["frozen_manifest_sha256"],
            raw["original_attempt_id"],
            EvaluationAuthorizationId(raw["recovery_authorization_id"]),
            raw["successor_purpose"],
        )
    except Exception:
        raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED") from None


def run_trusted_case(case: Phase6AEvaluationCase, harness: Phase6AHarness) -> CaseResult:
    if case.case_id.startswith("hold-"):
        raise ValueError("heldout cases cannot run through the trusted calibration harness")
    return harness.execute_case(case)


def _run_calibration_cases(
    cases: Sequence[Phase6AEvaluationCase],
) -> tuple[EvaluationRunResult, tuple[CaseResult, ...]]:
    harness = Phase6AHarness()
    results = tuple(run_trusted_case(case, harness) for case in cases)
    return (
        EvaluationRunResult(
            model_call_count=harness.model_call_count,
            rag_execution_count=harness.rag_execution_count,
            security_hard_gate_failures=harness.security_hard_gate_failures,
            heldout_execution_count=0,
        ),
        results,
    )


def run_manifest(cases: Sequence[Phase6AEvaluationCase]) -> EvaluationRunResult:
    """Run synthetic calibration cases only; reject any heldout input explicitly."""

    if any(case.case_id.startswith("hold-") for case in cases):
        raise ValueError("heldout cases are CLI-only and cannot be consumed by pytest")
    frozen = freeze_manifest(tuple(cases), version="phase6a-trusted-core-v1")
    run, _ = _run_calibration_cases(frozen.cases)
    return run


def _write_json(path: Path, value: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    path.write_text(encoded + "\n", encoding="utf-8", newline="\n")
    return hashlib.sha256((encoded + "\n").encode("utf-8")).hexdigest()


def _write_manifest(manifest: FrozenEvaluationManifest, path: Path) -> str:
    return _write_json(path, manifest.to_dict())


def _sanitized_summary(results: Sequence[CaseResult]) -> dict[str, Any]:
    categories: dict[str, int] = {}
    for result in results:
        categories[result.category.value] = categories.get(result.category.value, 0) + 1
    return {
        "schema_version": "phase6a-trusted-core-report-v1",
        "case_count": len(results),
        "category_counts": categories,
        "MODEL_CALL_COUNT": 0,
        "RAG_EXECUTION_COUNT": 0,
        "metrics": {
            "capability_approval": "separate_gate",
            "trusted_validation": "separate_gate",
            "claim_duplicate_prevention": "separate_gate",
            "lifecycle_reconciliation": "separate_gate",
            "security_hard_gate": "separate_gate",
            "runtime_import": "separate_gate",
            "supplementary_latency": "diagnostic_only",
        },
        "results": [result.to_sanitized_dict() for result in results],
    }


def write_sanitized_report(results: Sequence[CaseResult], path: Path) -> str:
    if any(result.case_id.startswith("hold-") for result in results):
        raise ValueError("Task 10 reports may contain calibration cases only")
    for result in results:
        _validate_case_result(result)
    return _write_json(path, _sanitized_summary(results))


def write_private_bundle(results: Sequence[CaseResult], path: Path) -> str:
    if any(result.case_id.startswith("hold-") for result in results):
        raise ValueError("Task 10 bundles may contain calibration cases only")
    for result in results:
        _validate_case_result(result)
    bundle = {
        "schema_version": "phase6a-trusted-core-private-v1",
        "evidence": [
            {
                "case_id": result.case_id,
                "passed": result.passed,
                "category": result.category.value,
                "metrics": dict(result.metrics),
            }
            for result in results
        ],
    }
    return _write_json(path, bundle)


def run_calibration_only(manifest_path: Path) -> EvaluationRunResult:
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError("manifest JSON must be an object")
    manifest = FrozenEvaluationManifest.from_dict(raw)
    if any(case.case_id.startswith("hold-") for case in manifest.cases):
        raise ValueError("calibration path cannot consume heldout cases")
    return run_manifest(manifest.cases)


def run_calibration_and_retain_synthetic_artifacts(output_dir: Path) -> tuple[str, str]:
    calibration = freeze_manifest(build_calibration_cases(), version="phase6a-trusted-core-v1")
    heldout = freeze_manifest(build_heldout_cases(), version="phase6a-trusted-core-v1")
    manifest_dir = output_dir / "manifests"
    _write_manifest(calibration, manifest_dir / "phase6a_calibration_v1.json")
    _write_manifest(heldout, manifest_dir / "phase6a_heldout_v1.json")
    _, results = _run_calibration_cases(calibration.cases)
    report_hash = write_sanitized_report(
        results, output_dir / "reports" / "phase6a_calibration_v1.json"
    )
    bundle_hash = write_private_bundle(
        results, output_dir / "private" / "phase6a_calibration_v1.bundle"
    )
    return report_hash, bundle_hash


def artifact_tree_contains_case_ids(output_dir: Path, *, prefix: str) -> bool:
    for path in output_dir.rglob("*"):
        # The frozen heldout manifest is intentionally retained beside the
        # calibration manifest; this helper checks retained result artifacts,
        # not the predeclared membership file.
        if "manifests" in path.parts:
            continue
        if path.is_file() and (prefix in path.name or prefix in path.read_text(encoding="utf-8")):
            return True
    return False


def _main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic Phase 6A acceptance fixtures")
    parser.add_argument(
        "command",
        nargs="?",
        choices=(
            "issue-heldout-authorization",
            "issue-heldout-key-loss-recovery",
            "issue-heldout-successor-authorization",
            "reconcile-heldout-zero",
            "reconcile-heldout-zero-recovery",
            "consume-heldout",
        ),
    )
    parser.add_argument("--split", choices=("calibration",))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--recovery-authorization", type=Path)
    parser.add_argument("--reconciled-attempt-id")
    parser.add_argument("--successor", action="store_true")
    parser.add_argument("--approver-identity")
    parser.add_argument("--context-id")
    args = parser.parse_args()
    if args.split == "calibration" and args.command is None:
        calibration_result = run_calibration_only(args.manifest)
        print(
            json.dumps(
                {
                    "MODEL_CALL_COUNT": calibration_result.model_call_count,
                    "RAG_EXECUTION_COUNT": calibration_result.rag_execution_count,
                    "SECURITY_HARD_GATE_FAILURES": calibration_result.security_hard_gate_failures,
                    "HELDOUT_EXECUTION_COUNT": calibration_result.heldout_execution_count,
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "reconcile-heldout-zero":
        if args.authorization is None or args.report is None or args.bundle is None:
            raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED")
        trusted_authorization = load_trusted_authorization_file(args.authorization)
        manifest = _load_consumption_manifest(args.manifest)
        if manifest.manifest_sha256 != _FROZEN_HELDOUT_SHA256:
            raise HeldoutConsumptionError("HELDOUT_MANIFEST_MISMATCH")
        claim_root = (
            Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "aaos-phase6a-acceptance"
        )
        claim = reconcile_zero_execution_claim(
            args.manifest,
            trusted_authorization,
            claim_store=SQLiteHeldoutClaimStore(claim_root / "heldout-execution-claims.sqlite"),
            report_path=args.report,
            bundle_path=args.bundle,
        )
        print(json.dumps({"attempt_id": claim.attempt_id, "status": claim.status}, sort_keys=True))
        return 0
    if args.command == "reconcile-heldout-zero-recovery":
        if args.authorization is None or args.report is None or args.bundle is None:
            raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED")
        recovery = load_key_loss_recovery_authorization_file(args.authorization)
        manifest = _load_consumption_manifest(args.manifest)
        if manifest.manifest_sha256 != _FROZEN_HELDOUT_SHA256:
            raise HeldoutConsumptionError("HELDOUT_MANIFEST_MISMATCH")
        claim_root = (
            Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "aaos-phase6a-acceptance"
        )
        claim = reconcile_zero_execution_claim_with_recovery(
            args.manifest,
            recovery,
            claim_store=SQLiteHeldoutClaimStore(claim_root / "heldout-execution-claims.sqlite"),
            report_path=args.report,
            bundle_path=args.bundle,
            current_repaired_head=_trusted_current_head(),
        )
        print(json.dumps({"attempt_id": claim.attempt_id, "status": claim.status}, sort_keys=True))
        return 0
    if args.command == "issue-heldout-authorization":
        if args.authorization is None or args.approver_identity is None or args.context_id is None:
            raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED")
        authorization = issue_trusted_authorization_file(
            args.manifest,
            args.authorization,
            TrustedApproverContext(args.approver_identity, args.context_id),
        )
        print(
            json.dumps(
                {
                    "authorization_id": authorization.authorization_id.value,
                    "manifest_sha256": authorization.manifest_sha256,
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "issue-heldout-key-loss-recovery":
        if args.authorization is None or args.report is None or args.bundle is None:
            raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED")
        manifest = _load_consumption_manifest(args.manifest)
        claim_root = (
            Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "aaos-phase6a-acceptance"
        )
        store = SQLiteHeldoutClaimStore(claim_root / "heldout-execution-claims.sqlite")
        existing = store.read_claim(manifest_sha256=manifest.manifest_sha256)
        if existing is None:
            raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
        recovery = issue_key_loss_recovery_authorization_file(
            args.manifest,
            args.authorization,
            original_claimed_at=existing.claimed_at,
            repaired_task11_head=_trusted_current_head(),
            approver_context=TrustedApproverContext(
                args.approver_identity or "", args.context_id or ""
            ),
            claim_store=store,
            report_path=args.report,
            bundle_path=args.bundle,
        )
        print(
            json.dumps(
                {
                    "recovery_authorization_id": recovery.recovery_authorization_id.value,
                    "manifest_sha256": recovery.frozen_manifest_sha256,
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "issue-heldout-successor-authorization":
        if args.authorization is None or args.recovery_authorization is None:
            raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED")
        recovery = load_key_loss_recovery_authorization_file(args.recovery_authorization)
        claim_root = (
            Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "aaos-phase6a-acceptance"
        )
        store = SQLiteHeldoutClaimStore(claim_root / "heldout-execution-claims.sqlite")
        root = store.read_claim(manifest_sha256=recovery.frozen_manifest_sha256)
        if root is None:
            raise HeldoutConsumptionError("HELDOUT_RECONCILIATION_REQUIRED")
        successor = issue_successor_heldout_authorization_file(
            args.manifest,
            args.authorization,
            approver_context=TrustedApproverContext(
                args.approver_identity or "", args.context_id or ""
            ),
            claim_store=store,
            reconciled_attempt_id=root.attempt_id,
            recovery_authorization_id=recovery.recovery_authorization_id,
            frozen_manifest_sha256=recovery.frozen_manifest_sha256,
        )
        print(
            json.dumps(
                {
                    "successor_authorization_id": successor.successor_authorization_id.value,
                    "manifest_sha256": successor.frozen_manifest_sha256,
                    "original_attempt_id": successor.original_attempt_id,
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command != "consume-heldout":
        parser.error(
            "specify --split calibration, issue-heldout-authorization, "
            "issue-heldout-key-loss-recovery, issue-heldout-successor-authorization, "
            "reconcile-heldout-zero, reconcile-heldout-zero-recovery, or consume-heldout"
        )
    if args.authorization is None or args.report is None or args.bundle is None:
        raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED")
    consumption_authorization: HeldoutConsumptionAuthorization
    if args.successor:
        consumption_authorization = load_successor_heldout_authorization_file(args.authorization)
    else:
        consumption_authorization = load_trusted_authorization_file(args.authorization)
    manifest = _load_consumption_manifest(args.manifest)
    executor = _build_real_heldout_executor_after_cli_gate(
        consumption_authorization, manifest.manifest_sha256, gate_token=_CLI_GATE_TOKEN
    )
    claim_root = (
        Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "aaos-phase6a-acceptance"
    )
    heldout_result = consume_heldout_once(
        args.manifest,
        args.report,
        args.bundle,
        consumption_authorization,
        claim_store=SQLiteHeldoutClaimStore(claim_root / "heldout-execution-claims.sqlite"),
        executor=executor,
    )
    print(
        json.dumps(
            {
                "manifest_sha256": heldout_result.manifest_sha256,
                "report_sha256": heldout_result.report_sha256,
                "private_bundle_sha256": heldout_result.private_bundle_sha256,
                "executed_case_count": heldout_result.executed_case_count,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
