from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping, Sequence

from aaos.agent.contracts import JSONValue

PHASE6B_EVALUATION_SCHEMA_VERSION = "phase6b-google-calendar-v1"
PHASE6B_CASE_IDS = tuple(f"phase6b-cal-{index:03d}" for index in range(1, 31))


class EvaluationOwner(StrEnum):
    VALIDATION_CANONICALIZATION = "validation_canonicalization"
    TRUSTED_CONFIGURATION_AUTHORITY = "trusted_configuration_authority"
    PRECONDITION_DUPLICATE_PREVENTION = "precondition_duplicate_prevention"
    PROVIDER_ERROR_TAXONOMY = "provider_error_taxonomy"
    UNKNOWN_RECONCILIATION = "unknown_reconciliation"
    PRIVACY_IMPORT_SECURITY = "privacy_import_security"


@dataclass(frozen=True, slots=True)
class FixtureSetup:
    kind: str
    parameters: Mapping[str, JSONValue]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind or len(self.kind) > 96:
            raise ValueError("fixture setup kind must be a bounded nonempty string")
        if not isinstance(self.parameters, Mapping):
            raise TypeError("fixture setup parameters must be a mapping")
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "parameters": dict(self.parameters)}


@dataclass(frozen=True, slots=True)
class ExpectedInvariant:
    kind: str
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind or len(self.kind) > 96:
            raise ValueError("expected invariant kind must be bounded")
        if not isinstance(self.value, str) or not self.value or len(self.value) > 160:
            raise ValueError("expected invariant value must be bounded")

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "value": self.value}


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    case_id: str
    owner: EvaluationOwner
    setup: FixtureSetup
    expected: ExpectedInvariant

    def __post_init__(self) -> None:
        if self.case_id not in PHASE6B_CASE_IDS:
            raise ValueError("case_id is not part of the frozen Phase 6B matrix")
        if not isinstance(self.owner, EvaluationOwner):
            raise TypeError("owner must be an EvaluationOwner")
        if not isinstance(self.setup, FixtureSetup):
            raise TypeError("setup must be a FixtureSetup")
        if not isinstance(self.expected, ExpectedInvariant):
            raise TypeError("expected must be an ExpectedInvariant")

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "owner": self.owner.value,
            "setup": self.setup.to_dict(),
            "expected": self.expected.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class FrozenManifest:
    schema_version: str
    cases: Sequence[EvaluationCase]
    manifest_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "cases": [case.to_dict() for case in self.cases],
            "manifest_sha256": self.manifest_sha256,
        }


def _case(
    index: int,
    owner: EvaluationOwner,
    setup_kind: str,
    expected_kind: str,
    expected_value: str,
    **parameters: JSONValue,
) -> EvaluationCase:
    return EvaluationCase(
        case_id=f"phase6b-cal-{index:03d}",
        owner=owner,
        setup=FixtureSetup(setup_kind, parameters),
        expected=ExpectedInvariant(expected_kind, expected_value),
    )


def build_cases() -> tuple[EvaluationCase, ...]:
    v = EvaluationOwner.VALIDATION_CANONICALIZATION
    c = EvaluationOwner.TRUSTED_CONFIGURATION_AUTHORITY
    p = EvaluationOwner.PRECONDITION_DUPLICATE_PREVENTION
    e = EvaluationOwner.PROVIDER_ERROR_TAXONOMY
    r = EvaluationOwner.UNKNOWN_RECONCILIATION
    s = EvaluationOwner.PRIVACY_IMPORT_SECURITY

    cases = (
        _case(1, v, "valid_minimal_timed_event", "canonical_dto", "six_approved_fields"),
        _case(2, v, "valid_complete_event", "canonical_optionals", "preserved"),
        _case(
            3,
            v,
            "summary_too_long",
            "validation_before_provider",
            "rejected",
            summary_length=201,
        ),
        _case(
            4,
            v,
            "invalid_iana_timezone",
            "validation_before_provider",
            "rejected",
            timezone="Mars/Phobos",
        ),
        _case(
            5,
            v,
            "duration_too_long",
            "validation_before_provider",
            "rejected",
            duration_seconds=86401,
        ),
        _case(
            6,
            v,
            "unknown_field_or_control_text",
            "closed_schema_authority",
            "rejected_no_authority",
        ),
        _case(7, c, "integration_disabled", "real_factory", "unavailable", enabled=False),
        _case(
            8,
            c,
            "missing_credential_path",
            "real_factory",
            "unavailable",
            credential_path="",
        ),
        _case(
            9,
            c,
            "wrong_service_identity",
            "real_factory",
            "unavailable",
            identity_matches=False,
        ),
        _case(
            10,
            c,
            "wrong_oauth_scope",
            "real_factory",
            "unavailable",
            scope_matches=False,
        ),
        _case(
            11,
            c,
            "request_supplies_other_calendar",
            "trusted_calendar_target",
            "configured_calendar_only",
        ),
        _case(
            12,
            p,
            "remote_id_absent",
            "provider_insert_count",
            "exactly_one",
            remote_exists=False,
        ),
        _case(
            13,
            p,
            "remote_id_already_exists",
            "provider_insert_count",
            "zero_precondition_stale",
            remote_exists=True,
        ),
        _case(
            14,
            p,
            "remote_changes_after_advisory_get",
            "execute_side_fence",
            "zero_inserts",
        ),
        _case(
            15,
            p,
            "two_workers_same_provider_id",
            "concurrent_insert_bound",
            "at_most_one_no_alternate_id",
            worker_count=2,
        ),
        _case(
            16,
            p,
            "action_target_or_config_drift",
            "adapter_write_count",
            "zero_before_call",
        ),
        _case(
            17,
            e,
            "pre_dispatch_permission_rejection",
            "provider_outcome",
            "authoritative_failure",
        ),
        _case(
            18,
            e,
            "insert_timeout",
            "provider_outcome",
            "unknown_zero_retry",
        ),
        _case(
            19,
            e,
            "connection_reset_after_request",
            "provider_outcome",
            "unknown_zero_retry",
        ),
        _case(
            20,
            e,
            "malformed_response_after_dispatch",
            "provider_outcome",
            "unknown_raw_response_discarded",
        ),
        _case(
            21,
            e,
            "rate_or_server_error",
            "provider_outcome",
            "typed_safe_error_zero_write_retry",
        ),
        _case(
            22,
            r,
            "accepted_insert_then_exact_get",
            "reconciliation",
            "confirmed_success",
        ),
        _case(
            23,
            r,
            "existing_semantic_mismatch",
            "reconciliation",
            "unknown_no_mutation",
        ),
        _case(
            24,
            r,
            "provider_not_found",
            "reconciliation",
            "unknown_no_retry",
        ),
        _case(
            25,
            r,
            "crash_after_executing_before_call",
            "recovery",
            "skip_execute_unknown",
        ),
        _case(
            26,
            r,
            "late_result_loses_cas",
            "late_result",
            "stale_audit_persisted_state_wins",
        ),
        _case(
            27,
            s,
            "credential_like_text",
            "credential_exposure",
            "zero",
            marker="<synthetic-credential-like-text>",
        ),
        _case(
            28,
            s,
            "raw_provider_response_extra_fields",
            "projection",
            "raw_fields_dropped",
        ),
        _case(
            29,
            s,
            "adversarial_api_method_or_callable",
            "generic_provider_authority",
            "absent",
        ),
        _case(
            30,
            s,
            "fresh_import_with_io_guards",
            "import_safety",
            "NO_SIDE_EFFECTS",
        ),
    )

    if tuple(case.case_id for case in cases) != PHASE6B_CASE_IDS:
        raise RuntimeError("Phase 6B evaluation case IDs changed")
    if len({case.case_id for case in cases}) != 30:
        raise RuntimeError("Phase 6B evaluation case IDs are not unique")
    return cases


def canonical_manifest_bytes(cases: Sequence[EvaluationCase]) -> bytes:
    ordered = tuple(cases)
    payload = {
        "schema_version": PHASE6B_EVALUATION_SCHEMA_VERSION,
        "cases": [case.to_dict() for case in ordered],
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def freeze_manifest(cases: Sequence[EvaluationCase] | None = None) -> FrozenManifest:
    selected = tuple(build_cases() if cases is None else cases)
    if tuple(case.case_id for case in selected) != PHASE6B_CASE_IDS:
        raise ValueError("manifest membership or ordering is not canonical")
    if len({case.case_id for case in selected}) != 30:
        raise ValueError("manifest contains duplicate case IDs")
    digest = hashlib.sha256(canonical_manifest_bytes(selected)).hexdigest()
    return FrozenManifest(PHASE6B_EVALUATION_SCHEMA_VERSION, selected, digest)


FROZEN_MANIFEST = freeze_manifest()
FROZEN_MANIFEST_SHA256 = FROZEN_MANIFEST.manifest_sha256


def owner_counts(cases: Sequence[EvaluationCase] | None = None) -> Mapping[str, int]:
    selected = build_cases() if cases is None else tuple(cases)
    counts: dict[str, int] = {}
    for case in selected:
        counts[case.owner.value] = counts.get(case.owner.value, 0) + 1
    return MappingProxyType(counts)
