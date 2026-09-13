"""Task 11 trusted-core acceptance and synthetic heldout-control tests."""

from __future__ import annotations

import hashlib
import hmac
import json
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
    load_synthetic_manifest,
    load_trusted_authorization_file,
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


def _authorization() -> HeldoutAuthorization:
    return HeldoutAuthorization(
        EvaluationAuthorizationId("synthetic-auth"),
        TrustedApproverContext("synthetic-approver", "synthetic-context"),
        datetime.now(timezone.utc),
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
    store = SQLiteHeldoutClaimStore(tmp_path / "claims.sqlite")
    executor = FakeHeldoutExecutor()
    first = consume_heldout_once(
        manifest_path,
        tmp_path / "report-a.json",
        tmp_path / "bundle-a.bin",
        _authorization(),
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
            _authorization(),
            claim_store=store,
            executor=FakeHeldoutExecutor(),
        )


def test_preexisting_single_artifact_is_rejected_before_claim(tmp_path: Path) -> None:
    manifest_path = _synthetic_manifest_path(tmp_path, ("synthetic-001",))
    (tmp_path / "report.json").write_text("pre-existing", encoding="utf-8")
    executor = FakeHeldoutExecutor()
    with pytest.raises(HeldoutConsumptionError, match="HELDOUT_ARTIFACT_EXISTS"):
        consume_heldout_once(
            manifest_path,
            tmp_path / "report.json",
            tmp_path / "bundle.bin",
            _authorization(),
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
        authorization_id=_authorization().authorization_id,
        claimed_at=datetime.now(timezone.utc),
    )
    executor = FakeHeldoutExecutor()
    with pytest.raises(HeldoutConsumptionError, match="HELDOUT_RECONCILIATION_REQUIRED"):
        consume_heldout_once(
            manifest_path,
            tmp_path / "report.json",
            tmp_path / "bundle.bin",
            _authorization(),
            claim_store=store,
            executor=executor,
        )
    assert executor.call_count == 0


def test_crash_after_executor_start_leaves_durable_claim(tmp_path: Path) -> None:
    manifest_path = _synthetic_manifest_path(tmp_path, ("synthetic-001",))
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
            _authorization(),
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
            _authorization(),
            claim_store=reopened,
            executor=second,
        )
    assert crashing.call_count == 1
    assert second.call_count == 0


def test_orphaned_artifacts_fail_closed_without_execution(tmp_path: Path) -> None:
    manifest = _synthetic_manifest_path(tmp_path, ("synthetic-001",))
    (tmp_path / "report.json").write_text("orphan", encoding="utf-8")
    (tmp_path / "bundle.bin").write_bytes(b"orphan")
    executor = FakeHeldoutExecutor()
    with pytest.raises(HeldoutConsumptionError, match="HELDOUT_RECONCILIATION_REQUIRED"):
        consume_heldout_once(
            manifest,
            tmp_path / "report.json",
            tmp_path / "bundle.bin",
            _authorization(),
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
    store = SQLiteHeldoutClaimStore(tmp_path / "claims.sqlite")

    def consume(index: int) -> str:
        executor = FakeHeldoutExecutor()
        try:
            consume_heldout_once(
                manifest_path,
                tmp_path / f"report-{index}.json",
                tmp_path / f"bundle-{index}.bin",
                _authorization(),
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
