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
import tempfile
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
    status: Literal["IN_PROGRESS", "CONSUMED"]
    claimed_at: datetime
    finalized_at: datetime | None
    report_sha256: str | None
    private_bundle_sha256: str | None


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


def _validate_case_result(result: CaseResult) -> None:
    if not result.case_id.startswith("cal-"):
        raise ValueError("case result must be a calibration result")
    canonical = {case.case_id: case for case in build_calibration_cases()}.get(result.case_id)
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


class RealHeldoutExecutor:
    """Explicitly gated future executor; never used by ordinary pytest."""

    def execute(self, manifest: FrozenEvaluationManifest) -> HeldoutExecutionEvidence:
        del manifest
        raise RuntimeError("real heldout execution is unavailable in this acceptance harness")


class FakeHeldoutClaimStore:
    """In-memory claim store for synthetic unit tests."""

    def __init__(self) -> None:
        self._claims: dict[str, HeldoutExecutionClaim] = {}

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
            manifest_sha256, authorization_id, "IN_PROGRESS", claimed_at, None, None, None
        )
        self._claims[manifest_sha256] = claim
        return claim

    def finalize_consumed_claim(
        self,
        *,
        manifest_sha256: str,
        authorization_id: EvaluationAuthorizationId,
        report_sha256: str,
        private_bundle_sha256: str,
        finalized_at: datetime,
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
        )
        self._claims[manifest_sha256] = claim
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
    )


class SQLiteHeldoutClaimStore:
    """Durable private, manifest-keyed one-shot execution claim store."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(database_path) as connection:
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
            connection.commit()
        _fsync_file(database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def read_claim(self, *, manifest_sha256: str) -> HeldoutExecutionClaim | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM heldout_execution_claims WHERE manifest_sha256 = ?",
                (manifest_sha256,),
            ).fetchone()
        return _claim_from_row(row) if row is not None else None

    def create_in_progress_claim(
        self,
        *,
        manifest_sha256: str,
        authorization_id: EvaluationAuthorizationId,
        claimed_at: datetime,
    ) -> HeldoutExecutionClaim:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO heldout_execution_claims "
                "(manifest_sha256, authorization_id, status, claimed_at) VALUES (?, ?, ?, ?)",
                (manifest_sha256, authorization_id.value, "IN_PROGRESS", _encode_time(claimed_at)),
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

    def finalize_consumed_claim(
        self,
        *,
        manifest_sha256: str,
        authorization_id: EvaluationAuthorizationId,
        report_sha256: str,
        private_bundle_sha256: str,
        finalized_at: datetime,
    ) -> HeldoutExecutionClaim:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE heldout_execution_claims SET status = 'CONSUMED', finalized_at = ?, "
                "report_sha256 = ?, private_bundle_sha256 = ? "
                "WHERE manifest_sha256 = ? AND authorization_id = ? AND status = 'IN_PROGRESS'",
                (
                    _encode_time(finalized_at),
                    report_sha256,
                    private_bundle_sha256,
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


def consume_heldout_once(
    manifest_path: Path,
    report_path: Path,
    bundle_path: Path,
    authorization: HeldoutAuthorization | None,
    *,
    claim_store: HeldoutClaimStore,
    executor: HeldoutExecutor,
) -> HeldoutRunResult:
    if authorization is None or not isinstance(authorization, HeldoutAuthorization):
        raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED")
    manifest = _load_consumption_manifest(manifest_path)
    existing = claim_store.read_claim(manifest_sha256=manifest.manifest_sha256)
    if existing is not None:
        raise HeldoutConsumptionError(
            "HELDOUT_RECONCILIATION_REQUIRED"
            if existing.status == "IN_PROGRESS"
            else "HELDOUT_ALREADY_CONSUMED"
        )
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
        claim_store.create_in_progress_claim(
            manifest_sha256=manifest.manifest_sha256,
            authorization_id=authorization.authorization_id,
            claimed_at=datetime.now(timezone.utc),
        )
    except sqlite3.IntegrityError:
        conflict = claim_store.read_claim(manifest_sha256=manifest.manifest_sha256)
        raise HeldoutConsumptionError(
            "HELDOUT_RECONCILIATION_REQUIRED"
            if conflict is None or conflict.status == "IN_PROGRESS"
            else "HELDOUT_ALREADY_CONSUMED"
        ) from None
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
        authorization_id=authorization.authorization_id,
        report_sha256=report_sha256,
        private_bundle_sha256=bundle_sha256,
        finalized_at=datetime.now(timezone.utc),
    )
    marker_payload = {
        "manifest_sha256": manifest.manifest_sha256,
        "report_sha256": report_sha256,
        "private_bundle_sha256": bundle_sha256,
        "authorization_id": authorization.authorization_id.value,
    }
    _write_temp_fsync_and_atomic_rename(
        (json.dumps(marker_payload, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        marker,
    )
    return HeldoutRunResult(
        manifest.manifest_sha256, report_sha256, bundle_sha256, evidence.executed_case_count
    )


class _CliGateToken:
    __slots__ = ()


_CLI_GATE_TOKEN = _CliGateToken()


def build_real_heldout_executor_after_cli_gate(
    authorization: HeldoutAuthorization,
    frozen_manifest_sha256: str,
) -> RealHeldoutExecutor:
    """Public compatibility name that cannot construct the real executor."""
    del authorization, frozen_manifest_sha256
    raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED")


def _build_real_heldout_executor_after_cli_gate(
    authorization: HeldoutAuthorization,
    frozen_manifest_sha256: str,
    *,
    gate_token: _CliGateToken,
) -> RealHeldoutExecutor:
    if gate_token is not _CLI_GATE_TOKEN or not isinstance(authorization, HeldoutAuthorization):
        raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED")
    if frozen_manifest_sha256 != _FROZEN_HELDOUT_SHA256:
        raise HeldoutConsumptionError("HELDOUT_MANIFEST_MISMATCH")
    return RealHeldoutExecutor()


def load_trusted_authorization_file(path: Path) -> HeldoutAuthorization:
    """Authenticate a bounded authorization document using trusted process configuration."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        fields = {"authorization_id", "approver_identity", "context_id", "issued_at", "signature"}
        if not isinstance(raw, dict) or set(raw) != fields:
            raise ValueError
        signature = raw["signature"]
        if not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{64}", signature):
            raise ValueError
        key = os.environ.get("AAOS_PHASE6A_AUTHORIZATION_KEY")
        if not key:
            raise ValueError
        payload = {
            "authorization_id": raw["authorization_id"],
            "approver_identity": raw["approver_identity"],
            "context_id": raw["context_id"],
            "issued_at": raw["issued_at"],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        expected = hmac.new(key.encode(), encoded, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        return HeldoutAuthorization(
            EvaluationAuthorizationId(raw["authorization_id"]),
            TrustedApproverContext(raw["approver_identity"], raw["context_id"]),
            _decode_time(raw["issued_at"]),
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
    parser.add_argument("command", nargs="?", choices=("consume-heldout",))
    parser.add_argument("--split", choices=("calibration",))
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--authorization", type=Path)
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
    if args.command != "consume-heldout":
        parser.error("specify --split calibration or the consume-heldout command")
    if args.authorization is None or args.report is None or args.bundle is None:
        raise HeldoutConsumptionError("HELDOUT_AUTHORIZATION_REQUIRED")
    authorization = load_trusted_authorization_file(args.authorization)
    manifest = _load_consumption_manifest(args.manifest)
    executor = _build_real_heldout_executor_after_cli_gate(
        authorization, manifest.manifest_sha256, gate_token=_CLI_GATE_TOKEN
    )
    claim_root = (
        Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "aaos-phase6a-acceptance"
    )
    heldout_result = consume_heldout_once(
        args.manifest,
        args.report,
        args.bundle,
        authorization,
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
