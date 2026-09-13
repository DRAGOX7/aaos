"""Task 11 trusted-core acceptance and synthetic heldout-control tests."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest
from phase6a_evaluation.harness import (
    EvaluationAuthorizationId,
    FakeHeldoutClaimStore,
    HeldoutAuthorization,
    HeldoutConsumptionError,
    HeldoutExecutionEvidence,
    SQLiteHeldoutClaimStore,
    build_real_heldout_executor_after_cli_gate,
    consume_heldout_once,
    issue_trusted_authorization_file,
    load_synthetic_manifest,
    load_trusted_authorization_file,
    reconcile_zero_execution_claim,
    run_manifest,
    run_phase6a_trusted_core_acceptance,
)
from phase6a_evaluation.manifest import FrozenEvaluationManifest, build_heldout_cases

from aaos.approval.contracts import TrustedApproverContext


def _synthetic_manifest_path(tmp_path: Path, case_ids: tuple[str, ...]) -> Path:
    cases = [
        {
            "case_id": case_id,
            "owner": "security",
            "category": "security_import",
            "language_fixture": None,
            "setup": "ADVERSARIAL_TEXT",
            "expected_invariant": "SECURITY_ISOLATION",
        }
        for case_id in case_ids
    ]
    payload = {"version": "phase6a-trusted-core-v1", "cases": cases}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["manifest_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    path = tmp_path / "synthetic-manifest.json"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def _authorization(
    manifest_sha256: str = "438a6059a4a94b480fa840987ac18511ff869235fad6a253fb1d657598c26d2e",
) -> HeldoutAuthorization:
    return HeldoutAuthorization(
        EvaluationAuthorizationId("synthetic-auth"),
        TrustedApproverContext("synthetic-approver", "synthetic-context"),
        datetime.now(timezone.utc),
        manifest_sha256,
    )


class FakeHeldoutExecutor:
    def __init__(self) -> None:
        self.call_count = 0

    def execute(self, manifest: FrozenEvaluationManifest) -> HeldoutExecutionEvidence:
        self.call_count += 1
        count = len(manifest.cases)
        return HeldoutExecutionEvidence(
            manifest.manifest_sha256,
            b'{"sanitized":true}\n',
            b'{"private_evidence":true}\n',
            count,
        )


def test_phase6a_acceptance_gates_are_separate_and_zero_tolerance() -> None:
    evidence = run_phase6a_trusted_core_acceptance()
    assert evidence.model_call_count == 0
    assert evidence.rag_execution_count == 0
    assert evidence.security_hard_gate_failures == 0
    assert evidence.unauthorized_execution_count == 0
    assert evidence.invalid_argument_handler_count == 0
    assert evidence.unknown_outcome_automatic_retry_count == 0


def test_synthetic_consumption_requires_authorization(tmp_path: Path) -> None:
    manifest = _synthetic_manifest_path(tmp_path, ("synthetic-001",))
    with pytest.raises(HeldoutConsumptionError, match="HELDOUT_AUTHORIZATION_REQUIRED"):
        consume_heldout_once(
            manifest,
            tmp_path / "report.json",
            tmp_path / "bundle.bin",
            None,
            claim_store=FakeHeldoutClaimStore(),
            executor=FakeHeldoutExecutor(),
        )


def test_synthetic_consumption_is_one_time_and_filename_independent(tmp_path: Path) -> None:
    manifest_path = _synthetic_manifest_path(tmp_path, ("synthetic-001", "synthetic-002"))
    manifest = load_synthetic_manifest(manifest_path)
    store = SQLiteHeldoutClaimStore(tmp_path / "claims.sqlite")
    executor = FakeHeldoutExecutor()
    first = consume_heldout_once(
        manifest_path,
        tmp_path / "report-a.json",
        tmp_path / "bundle-a.bin",
        _authorization(manifest.manifest_sha256),
        claim_store=store,
        executor=executor,
    )
    assert first.executed_case_count == 2
    assert executor.call_count == 1
    with pytest.raises(HeldoutConsumptionError, match="HELDOUT_ALREADY_CONSUMED"):
        consume_heldout_once(
            manifest_path,
            tmp_path / "report-b.json",
            tmp_path / "bundle-b.bin",
            _authorization(manifest.manifest_sha256),
            claim_store=store,
            executor=FakeHeldoutExecutor(),
        )


def test_authorization_for_one_manifest_cannot_consume_another(
    tmp_path: Path,
) -> None:
    manifest_a_dir = tmp_path / "manifest-a"
    manifest_b_dir = tmp_path / "manifest-b"
    manifest_a_dir.mkdir()
    manifest_b_dir.mkdir()
    manifest_a_path = _synthetic_manifest_path(manifest_a_dir, ("synthetic-a",))
    manifest_b_path = _synthetic_manifest_path(manifest_b_dir, ("synthetic-b",))
    manifest_a = load_synthetic_manifest(manifest_a_path)
    executor = FakeHeldoutExecutor()
    with pytest.raises(HeldoutConsumptionError, match="HELDOUT_AUTHORIZATION_REQUIRED"):
        consume_heldout_once(
            manifest_b_path,
            tmp_path / "report.json",
            tmp_path / "bundle.bin",
            _authorization(manifest_a.manifest_sha256),
            claim_store=FakeHeldoutClaimStore(),
            executor=executor,
        )
    assert executor.call_count == 0


def test_preexisting_single_artifact_is_rejected_before_claim(tmp_path: Path) -> None:
    manifest_path = _synthetic_manifest_path(tmp_path, ("synthetic-001",))
    manifest = load_synthetic_manifest(manifest_path)
    (tmp_path / "report.json").write_text("pre-existing", encoding="utf-8")
    executor = FakeHeldoutExecutor()
    with pytest.raises(HeldoutConsumptionError, match="HELDOUT_ARTIFACT_EXISTS"):
        consume_heldout_once(
            manifest_path,
            tmp_path / "report.json",
            tmp_path / "bundle.bin",
            _authorization(manifest.manifest_sha256),
            claim_store=FakeHeldoutClaimStore(),
            executor=executor,
        )
    assert executor.call_count == 0


def test_existing_in_progress_claim_blocks_rerun(tmp_path: Path) -> None:
    manifest_path = _synthetic_manifest_path(tmp_path, ("synthetic-001",))
    manifest = load_synthetic_manifest(manifest_path)
    store = SQLiteHeldoutClaimStore(tmp_path / "claims.sqlite")
    store.create_in_progress_claim(
        manifest_sha256=manifest.manifest_sha256,
        authorization_id=_authorization(manifest.manifest_sha256).authorization_id,
        claimed_at=datetime.now(timezone.utc),
    )
    executor = FakeHeldoutExecutor()
    with pytest.raises(HeldoutConsumptionError, match="HELDOUT_RECONCILIATION_REQUIRED"):
        consume_heldout_once(
            manifest_path,
            tmp_path / "report.json",
            tmp_path / "bundle.bin",
            _authorization(manifest.manifest_sha256),
            claim_store=store,
            executor=executor,
        )
    assert executor.call_count == 0


def test_crash_after_executor_start_leaves_durable_claim(tmp_path: Path) -> None:
    manifest_path = _synthetic_manifest_path(tmp_path, ("synthetic-001",))
    manifest = load_synthetic_manifest(manifest_path)
    claim_db = tmp_path / "claims.sqlite"
    store = SQLiteHeldoutClaimStore(claim_db)

    class CrashExecutor:
        call_count = 0

        def execute(self, manifest: FrozenEvaluationManifest) -> HeldoutExecutionEvidence:
            self.call_count += 1
            claim = store.read_claim(manifest_sha256=manifest.manifest_sha256)
            assert claim is not None and claim.status == "IN_PROGRESS"
            raise RuntimeError("simulated crash")

    crashing = CrashExecutor()
    with pytest.raises(HeldoutConsumptionError, match="HELDOUT_RECONCILIATION_REQUIRED"):
        consume_heldout_once(
            manifest_path,
            tmp_path / "report.json",
            tmp_path / "bundle.bin",
            _authorization(manifest.manifest_sha256),
            claim_store=store,
            executor=crashing,
        )
    reopened = SQLiteHeldoutClaimStore(claim_db)
    second = FakeHeldoutExecutor()
    with pytest.raises(HeldoutConsumptionError, match="HELDOUT_RECONCILIATION_REQUIRED"):
        consume_heldout_once(
            manifest_path,
            tmp_path / "report-retry.json",
            tmp_path / "bundle-retry.bin",
            _authorization(manifest.manifest_sha256),
            claim_store=reopened,
            executor=second,
        )
    assert crashing.call_count == 1
    assert second.call_count == 0


def test_orphaned_artifacts_fail_closed_without_execution(tmp_path: Path) -> None:
    manifest_path = _synthetic_manifest_path(tmp_path, ("synthetic-001",))
    manifest = load_synthetic_manifest(manifest_path)
    (tmp_path / "report.json").write_text("orphan", encoding="utf-8")
    (tmp_path / "bundle.bin").write_bytes(b"orphan")
    executor = FakeHeldoutExecutor()
    with pytest.raises(HeldoutConsumptionError, match="HELDOUT_RECONCILIATION_REQUIRED"):
        consume_heldout_once(
            manifest_path,
            tmp_path / "report.json",
            tmp_path / "bundle.bin",
            _authorization(manifest.manifest_sha256),
            claim_store=FakeHeldoutClaimStore(),
            executor=executor,
        )
    assert executor.call_count == 0


def test_changed_manifest_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "changed.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(HeldoutConsumptionError, match="HELDOUT_MANIFEST_MISMATCH"):
        consume_heldout_once(
            path,
            tmp_path / "report.json",
            tmp_path / "bundle.bin",
            _authorization(),
            claim_store=FakeHeldoutClaimStore(),
            executor=FakeHeldoutExecutor(),
        )


def test_concurrent_consumers_create_one_claim_and_execute_once(tmp_path: Path) -> None:
    manifest_path = _synthetic_manifest_path(tmp_path, ("synthetic-001",))
    manifest = load_synthetic_manifest(manifest_path)
    store = SQLiteHeldoutClaimStore(tmp_path / "claims.sqlite")

    def consume(index: int) -> str:
        executor = FakeHeldoutExecutor()
        try:
            consume_heldout_once(
                manifest_path,
                tmp_path / f"report-{index}.json",
                tmp_path / f"bundle-{index}.bin",
                _authorization(manifest.manifest_sha256),
                claim_store=store,
                executor=executor,
            )
            return "executed"
        except HeldoutConsumptionError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(consume, (1, 2)))
    assert outcomes.count("executed") == 1
    assert set(outcomes) <= {
        "executed",
        "HELDOUT_ALREADY_CONSUMED",
        "HELDOUT_RECONCILIATION_REQUIRED",
    }


def test_normal_manifest_runner_rejects_every_real_heldout_case() -> None:
    with pytest.raises(ValueError, match="heldout cases"):
        run_manifest(build_heldout_cases())


def test_normal_acceptance_never_calls_real_heldout_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "phase6a_evaluation.harness.RealHeldoutExecutor.execute",
        lambda *_args, **_kwargs: pytest.fail("real heldout execution is CLI-only"),
    )
    run_phase6a_trusted_core_acceptance()


def test_real_executor_factory_requires_explicit_cli_gate() -> None:
    with pytest.raises(HeldoutConsumptionError, match="HELDOUT_AUTHORIZATION_REQUIRED"):
        build_real_heldout_executor_after_cli_gate(
            _authorization(),
            "438a6059a4a94b480fa840987ac18511ff869235fad6a253fb1d657598c26d2e",
        )


def test_authorization_file_requires_trusted_signature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {
        "authorization_id": "signed-auth",
        "approver_identity": "signed-approver",
        "context_id": "signed-context",
        "issued_at": "2026-09-13T00:00:00Z",
        "manifest_sha256": "438a6059a4a94b480fa840987ac18511ff869235fad6a253fb1d657598c26d2e",
    }
    key = "synthetic-test-key"
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signed = dict(payload)
    signed["signature"] = hmac.new(key.encode(), encoded, hashlib.sha256).hexdigest()
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps(signed), encoding="utf-8")
    monkeypatch.setenv("AAOS_PHASE6A_AUTHORIZATION_KEY", key)
    authorization = load_trusted_authorization_file(path)
    assert authorization.authorization_id.value == "signed-auth"
    monkeypatch.setenv("AAOS_PHASE6A_AUTHORIZATION_KEY", "wrong-key")
    with pytest.raises(HeldoutConsumptionError, match="HELDOUT_AUTHORIZATION_REQUIRED"):
        load_trusted_authorization_file(path)


def test_issue_authorization_creates_signed_manifest_bound_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = _synthetic_manifest_path(tmp_path, ("synthetic-001",))
    manifest = load_synthetic_manifest(manifest_path)
    monkeypatch.setattr(
        "phase6a_evaluation.harness._FROZEN_HELDOUT_SHA256", manifest.manifest_sha256
    )
    monkeypatch.setenv("AAOS_PHASE6A_AUTHORIZATION_KEY", "synthetic-issue-key")
    destination = tmp_path / "authorization.json"
    authorization = issue_trusted_authorization_file(
        manifest_path,
        destination,
        TrustedApproverContext("synthetic-approver", "synthetic-context"),
        issued_at=datetime(2026, 9, 13, tzinfo=timezone.utc),
    )
    assert destination.exists()
    assert authorization.manifest_sha256 == manifest.manifest_sha256
    assert load_trusted_authorization_file(destination) == authorization


def test_issue_authorization_requires_signing_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = _synthetic_manifest_path(tmp_path, ("synthetic-001",))
    manifest = load_synthetic_manifest(manifest_path)
    monkeypatch.setattr(
        "phase6a_evaluation.harness._FROZEN_HELDOUT_SHA256", manifest.manifest_sha256
    )
    monkeypatch.delenv("AAOS_PHASE6A_AUTHORIZATION_KEY", raising=False)
    with pytest.raises(HeldoutConsumptionError, match="HELDOUT_AUTHORIZATION_REQUIRED"):
        issue_trusted_authorization_file(
            manifest_path,
            tmp_path / "authorization.json",
            TrustedApproverContext("synthetic-approver", "synthetic-context"),
        )


def test_issue_authorization_rejects_existing_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = _synthetic_manifest_path(tmp_path, ("synthetic-001",))
    manifest = load_synthetic_manifest(manifest_path)
    monkeypatch.setattr(
        "phase6a_evaluation.harness._FROZEN_HELDOUT_SHA256", manifest.manifest_sha256
    )
    monkeypatch.setenv("AAOS_PHASE6A_AUTHORIZATION_KEY", "synthetic-issue-key")
    destination = tmp_path / "authorization.json"
    destination.write_text("existing", encoding="utf-8")
    with pytest.raises(HeldoutConsumptionError, match="HELDOUT_ARTIFACT_EXISTS"):
        issue_trusted_authorization_file(
            manifest_path,
            destination,
            TrustedApproverContext("synthetic-approver", "synthetic-context"),
        )


def test_issued_authorization_binds_manifest_and_rejects_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = _synthetic_manifest_path(tmp_path, ("synthetic-001",))
    manifest = load_synthetic_manifest(manifest_path)
    monkeypatch.setattr(
        "phase6a_evaluation.harness._FROZEN_HELDOUT_SHA256", manifest.manifest_sha256
    )
    monkeypatch.setenv("AAOS_PHASE6A_AUTHORIZATION_KEY", "synthetic-issue-key")
    destination = tmp_path / "authorization.json"
    issue_trusted_authorization_file(
        manifest_path,
        destination,
        TrustedApproverContext("synthetic-approver", "synthetic-context"),
        issued_at=datetime(2026, 9, 13, tzinfo=timezone.utc),
    )
    raw = json.loads(destination.read_text(encoding="utf-8"))
    raw["manifest_sha256"] = "0" * 64
    destination.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(HeldoutConsumptionError, match="HELDOUT_AUTHORIZATION_REQUIRED"):
        load_trusted_authorization_file(destination)


def test_issue_authorization_has_no_claim_or_execution_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = _synthetic_manifest_path(tmp_path, ("synthetic-001",))
    manifest = load_synthetic_manifest(manifest_path)
    monkeypatch.setattr(
        "phase6a_evaluation.harness._FROZEN_HELDOUT_SHA256", manifest.manifest_sha256
    )
    monkeypatch.setenv("AAOS_PHASE6A_AUTHORIZATION_KEY", "synthetic-issue-key")
    destination = tmp_path / "authorization.json"
    issue_trusted_authorization_file(
        manifest_path,
        destination,
        TrustedApproverContext("synthetic-approver", "synthetic-context"),
    )
    claim_db = tmp_path / "claims.sqlite"
    assert not claim_db.exists()
    assert not (tmp_path / "report.json").exists()
    assert not (tmp_path / "bundle.bin").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("authorization_id", "tampered-auth"),
        ("approver_identity", "tampered-approver"),
        ("context_id", "tampered-context"),
        ("issued_at", "2026-09-14T00:00:00Z"),
        ("manifest_sha256", "0" * 64),
    ],
)
def test_authorization_signature_covers_every_trusted_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
) -> None:
    manifest_path = _synthetic_manifest_path(tmp_path, ("synthetic-001",))
    manifest = load_synthetic_manifest(manifest_path)
    monkeypatch.setattr(
        "phase6a_evaluation.harness._FROZEN_HELDOUT_SHA256", manifest.manifest_sha256
    )
    monkeypatch.setenv("AAOS_PHASE6A_AUTHORIZATION_KEY", "synthetic-issue-key")
    destination = tmp_path / "authorization.json"
    issue_trusted_authorization_file(
        manifest_path,
        destination,
        TrustedApproverContext("synthetic-approver", "synthetic-context"),
        issued_at=datetime(2026, 9, 13, tzinfo=timezone.utc),
    )
    raw = json.loads(destination.read_text(encoding="utf-8"))
    raw[field] = value
    destination.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(HeldoutConsumptionError, match="HELDOUT_AUTHORIZATION_REQUIRED"):
        load_trusted_authorization_file(destination)


def test_authorization_rejects_unknown_fields_and_malformed_signature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = _synthetic_manifest_path(tmp_path, ("synthetic-001",))
    manifest = load_synthetic_manifest(manifest_path)
    monkeypatch.setattr(
        "phase6a_evaluation.harness._FROZEN_HELDOUT_SHA256", manifest.manifest_sha256
    )
    monkeypatch.setenv("AAOS_PHASE6A_AUTHORIZATION_KEY", "synthetic-issue-key")
    destination = tmp_path / "authorization.json"
    issue_trusted_authorization_file(
        manifest_path,
        destination,
        TrustedApproverContext("synthetic-approver", "synthetic-context"),
    )
    raw = json.loads(destination.read_text(encoding="utf-8"))
    raw["unexpected"] = "authority"
    destination.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(HeldoutConsumptionError, match="HELDOUT_AUTHORIZATION_REQUIRED"):
        load_trusted_authorization_file(destination)
    raw.pop("unexpected")
    raw["signature"] = "not-a-signature"
    destination.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(HeldoutConsumptionError, match="HELDOUT_AUTHORIZATION_REQUIRED"):
        load_trusted_authorization_file(destination)


def test_issue_authorization_rejects_non_frozen_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = _synthetic_manifest_path(tmp_path, ("synthetic-001",))
    monkeypatch.setenv("AAOS_PHASE6A_AUTHORIZATION_KEY", "synthetic-issue-key")
    destination = tmp_path / "authorization.json"
    with pytest.raises(HeldoutConsumptionError, match="HELDOUT_MANIFEST_MISMATCH"):
        issue_trusted_authorization_file(
            manifest_path,
            destination,
            TrustedApproverContext("synthetic-approver", "synthetic-context"),
        )


def test_issuance_cli_is_distinct_from_consumption() -> None:
    from phase6a_evaluation import harness

    assert "issue-heldout-authorization" in harness._main.__code__.co_consts
    assert "consume-heldout" in harness._main.__code__.co_consts


def test_real_executor_runs_every_synthetic_case_once(tmp_path: Path) -> None:
    from phase6a_evaluation.harness import RealHeldoutExecutor

    manifest_path = _synthetic_manifest_path(
        tmp_path, tuple(f"synthetic-{index:03d}" for index in range(1, 4))
    )
    manifest = load_synthetic_manifest(manifest_path)
    executor = RealHeldoutExecutor(allow_synthetic=True)
    evidence = executor.execute(manifest)
    assert evidence.manifest_sha256 == manifest.manifest_sha256
    assert evidence.executed_case_count == len(manifest.cases)
    assert json.loads(evidence.report_bytes)["case_count"] == 3


def test_real_executor_marks_broken_invariant_as_failed(tmp_path: Path) -> None:
    from phase6a_evaluation.harness import RealHeldoutExecutor, TrustedCoreFixture

    class BrokenFixture(TrustedCoreFixture):
        def evaluate(self, case):  # type: ignore[no-untyped-def]
            result = super().evaluate(case)
            return result.__class__(
                False,
                result.trusted_validation,
                result.claim_duplicate_prevention,
                result.lifecycle_reconciliation,
                result.security_hard_gate,
                result.runtime_import,
                result.latency_ms,
                result.security_probe,
            )

    manifest_path = _synthetic_manifest_path(tmp_path, ("synthetic-001",))
    manifest = load_synthetic_manifest(manifest_path)
    evidence = RealHeldoutExecutor(allow_synthetic=True, fixture=BrokenFixture()).execute(manifest)
    report = json.loads(evidence.report_bytes)
    assert report["results"][0]["passed"] is False


def test_real_executor_rejects_duplicate_case_execution(tmp_path: Path) -> None:
    from phase6a_evaluation.harness import RealHeldoutExecutor

    manifest_path = _synthetic_manifest_path(tmp_path, ("synthetic-001", "synthetic-001"))
    manifest = load_synthetic_manifest(manifest_path)
    with pytest.raises(ValueError, match="duplicate"):
        RealHeldoutExecutor(allow_synthetic=True).execute(manifest)


def test_zero_case_reconciliation_closes_only_in_progress_claim(tmp_path: Path) -> None:
    manifest_path = _synthetic_manifest_path(tmp_path, ("synthetic-001",))
    manifest = load_synthetic_manifest(manifest_path)
    store = SQLiteHeldoutClaimStore(tmp_path / "claims.sqlite")
    authorization = _authorization(manifest.manifest_sha256)
    store.create_in_progress_claim(
        manifest_sha256=manifest.manifest_sha256,
        authorization_id=authorization.authorization_id,
        claimed_at=datetime.now(timezone.utc),
    )
    reconciled = reconcile_zero_execution_claim(
        manifest_path,
        authorization,
        claim_store=store,
        report_path=tmp_path / "report.json",
        bundle_path=tmp_path / "bundle.bin",
    )
    assert reconciled.status == "RECONCILED_ZERO_EXECUTION"
    assert reconciled.executed_case_count == 0
    assert reconciled.reconciliation_reason == "REAL_EXECUTOR_UNAVAILABLE_ZERO_CASES"
    assert store.read_claim(manifest_sha256=manifest.manifest_sha256) == reconciled


def test_zero_case_reconciliation_is_idempotent_and_consumed_is_rejected(tmp_path: Path) -> None:
    manifest_path = _synthetic_manifest_path(tmp_path, ("synthetic-001",))
    manifest = load_synthetic_manifest(manifest_path)
    store = SQLiteHeldoutClaimStore(tmp_path / "claims.sqlite")
    authorization = _authorization(manifest.manifest_sha256)
    store.create_in_progress_claim(
        manifest_sha256=manifest.manifest_sha256,
        authorization_id=authorization.authorization_id,
        claimed_at=datetime.now(timezone.utc),
    )
    first = reconcile_zero_execution_claim(
        manifest_path,
        authorization,
        claim_store=store,
        report_path=tmp_path / "report.json",
        bundle_path=tmp_path / "bundle.bin",
    )
    second = reconcile_zero_execution_claim(
        manifest_path,
        authorization,
        claim_store=store,
        report_path=tmp_path / "report.json",
        bundle_path=tmp_path / "bundle.bin",
    )
    assert second == first


def test_legacy_in_progress_row_is_migrated_and_preserved(tmp_path: Path) -> None:
    import sqlite3

    database = tmp_path / "legacy.sqlite"
    claimed_at = "2026-09-13T00:00:00Z"
    manifest_sha256 = "a" * 64
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE heldout_execution_claims ("
            "manifest_sha256 TEXT PRIMARY KEY, authorization_id TEXT NOT NULL, "
            "status TEXT NOT NULL, claimed_at TEXT NOT NULL, finalized_at TEXT, "
            "report_sha256 TEXT, private_bundle_sha256 TEXT)"
        )
        connection.execute(
            "INSERT INTO heldout_execution_claims VALUES (?, ?, ?, ?, NULL, NULL, NULL)",
            (manifest_sha256, "legacy-auth", "IN_PROGRESS", claimed_at),
        )
        connection.commit()
    store = SQLiteHeldoutClaimStore(database)
    attempts = store.read_attempts(manifest_sha256=manifest_sha256)
    assert len(attempts) == 1
    assert attempts[0].status == "IN_PROGRESS"
    assert attempts[0].authorization_id.value == "legacy-auth"
    assert attempts[0].claimed_at.isoformat().replace("+00:00", "Z") == claimed_at


def test_reconciled_root_allows_exactly_one_successor(tmp_path: Path) -> None:
    manifest_path = _synthetic_manifest_path(tmp_path, ("synthetic-001",))
    manifest = load_synthetic_manifest(manifest_path)
    store = SQLiteHeldoutClaimStore(tmp_path / "claims.sqlite")
    auth = _authorization(manifest.manifest_sha256)
    store.create_in_progress_claim(
        manifest_sha256=manifest.manifest_sha256,
        authorization_id=auth.authorization_id,
        claimed_at=datetime.now(timezone.utc),
    )
    root = store.reconcile_zero_execution(
        manifest_sha256=manifest.manifest_sha256,
        authorization_id=auth.authorization_id,
        reconciled_at=datetime.now(timezone.utc),
        reason="REAL_EXECUTOR_UNAVAILABLE_ZERO_CASES",
    )
    successor = store.create_successor_in_progress_claim(
        manifest_sha256=manifest.manifest_sha256,
        authorization_id=auth.authorization_id,
        claimed_at=datetime.now(timezone.utc),
        successor_of=root.attempt_id,
    )
    assert successor.successor_of == root.attempt_id
    with pytest.raises(sqlite3.IntegrityError):
        store.create_successor_in_progress_claim(
            manifest_sha256=manifest.manifest_sha256,
            authorization_id=auth.authorization_id,
            claimed_at=datetime.now(timezone.utc),
            successor_of=root.attempt_id,
        )


def test_zero_case_reconciliation_rejects_existing_successor_attempt(tmp_path: Path) -> None:
    manifest_path = _synthetic_manifest_path(tmp_path, ("synthetic-001",))
    manifest = load_synthetic_manifest(manifest_path)
    store = SQLiteHeldoutClaimStore(tmp_path / "claims.sqlite")
    auth = _authorization(manifest.manifest_sha256)
    store.create_in_progress_claim(
        manifest_sha256=manifest.manifest_sha256,
        authorization_id=auth.authorization_id,
        claimed_at=datetime.now(timezone.utc),
    )
    root = store.reconcile_zero_execution(
        manifest_sha256=manifest.manifest_sha256,
        authorization_id=auth.authorization_id,
        reconciled_at=datetime.now(timezone.utc),
        reason="REAL_EXECUTOR_UNAVAILABLE_ZERO_CASES",
    )
    store.create_successor_in_progress_claim(
        manifest_sha256=manifest.manifest_sha256,
        authorization_id=auth.authorization_id,
        claimed_at=datetime.now(timezone.utc),
        successor_of=root.attempt_id,
    )
    with pytest.raises(HeldoutConsumptionError, match="HELDOUT_RECONCILIATION_REQUIRED"):
        reconcile_zero_execution_claim(
            manifest_path,
            auth,
            claim_store=store,
            report_path=tmp_path / "report.json",
            bundle_path=tmp_path / "bundle.bin",
        )
