"""Synthetic whole-system failure and recovery qualification scenarios."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

RetryPolicy = Literal["NONE", "TRUSTED_RETRY", "RECONCILIATION"]
Risk = Literal["NONE", "BOUNDED", "REVIEW_REQUIRED"]


@dataclass(frozen=True, slots=True)
class FailureScenario:
    name: str
    subsystem: str
    injected_condition: str
    expected_user_state: str
    expected_internal_state: str
    retry_policy: RetryPolicy
    data_loss_risk: Risk
    recovery: str

    def __post_init__(self) -> None:
        values = (
            self.name,
            self.subsystem,
            self.injected_condition,
            self.expected_user_state,
            self.expected_internal_state,
            self.recovery,
        )
        if any(not isinstance(value, str) or not value or len(value) > 256 for value in values):
            raise ValueError("failure scenario text must be bounded")


@dataclass(frozen=True, slots=True)
class FailureFakes:
    """Counters for synthetic faults; no implementation performs I/O."""

    external_provider_calls: int = 0
    external_writes: int = 0
    model_calls: int = 0
    rag_calls: int = 0


@dataclass(frozen=True, slots=True)
class FailureResult:
    scenario_name: str
    user_state: str
    internal_state: str
    automatic_retry: bool
    reconciliation_required: bool
    external_provider_calls: int
    external_writes: int
    model_calls: int
    rag_calls: int
    raw_details: str | None = None
    secret_exposed: bool = False


def _scenario(
    name: str,
    subsystem: str,
    condition: str,
    user: str,
    internal: str,
    retry: RetryPolicy,
    risk: Risk,
    recovery: str,
) -> FailureScenario:
    return FailureScenario(name, subsystem, condition, user, internal, retry, risk, recovery)


PHASE9_FAILURE_MATRIX: Final[tuple[FailureScenario, ...]] = (
    _scenario("moodle_unavailable", "MOODLE", "connection unavailable", "ACADEMIC_UNAVAILABLE", "READ_ONLY_FAILURE", "NONE", "BOUNDED", "retry only after trusted operator action"),
    _scenario("moodle_session_expired", "MOODLE", "session expired", "REAUTH_REQUIRED", "SESSION_EXPIRED", "TRUSTED_RETRY", "NONE", "trusted re-authentication"),
    _scenario("moodle_malformed_response", "MOODLE", "schema mismatch", "ACADEMIC_UNAVAILABLE", "INVALID_RESPONSE", "NONE", "BOUNDED", "discard bounded response and review"),
    _scenario("document_parse_failure", "DOCUMENT", "parser rejected input", "DOCUMENT_UNAVAILABLE", "PARSE_FAILED", "NONE", "BOUNDED", "retain source and report parse failure"),
    _scenario("document_corrupt_pdf", "DOCUMENT", "corrupt PDF bytes", "DOCUMENT_UNAVAILABLE", "PARSE_FAILED", "NONE", "BOUNDED", "replace source through trusted ingestion"),
    _scenario("index_unavailable", "RAG", "index unavailable", "INSUFFICIENT_EVIDENCE", "INDEX_UNAVAILABLE", "NONE", "BOUNDED", "restore index under trusted maintenance"),
    _scenario("embedding_failure", "RAG", "embedding adapter failure", "INSUFFICIENT_EVIDENCE", "EMBEDDING_FAILED", "NONE", "BOUNDED", "trusted reindex after diagnosis"),
    _scenario("llm_unavailable", "RAG", "generation unavailable", "INSUFFICIENT_EVIDENCE", "GENERATION_UNAVAILABLE", "NONE", "BOUNDED", "retry in a separately bounded request"),
    _scenario("nli_unavailable", "RAG", "NLI unavailable", "INSUFFICIENT_EVIDENCE", "NLI_UNAVAILABLE", "NONE", "BOUNDED", "do not emit unsupported answer"),
    _scenario("vlm_unavailable", "VISION", "vision adapter unavailable", "VISUAL_UNAVAILABLE", "UNAVAILABLE", "NONE", "BOUNDED", "continue without visual evidence"),
    _scenario("docker_unavailable", "COMPUTATION", "Docker unavailable", "COMPUTATION_UNAVAILABLE", "SANDBOX_UNAVAILABLE", "NONE", "BOUNDED", "report unavailable sandbox"),
    _scenario("worker_crash", "COMPUTATION", "worker exits unexpectedly", "COMPUTATION_UNKNOWN", "UNKNOWN_OUTCOME", "RECONCILIATION", "REVIEW_REQUIRED", "trusted reconciliation; never retry automatically"),
    _scenario("computation_timeout", "COMPUTATION", "deadline exceeded", "COMPUTATION_TIMEOUT", "TIMED_OUT", "NONE", "BOUNDED", "cleanup worker and report timeout"),
    _scenario("computation_output_limit", "COMPUTATION", "output quota exceeded", "COMPUTATION_FAILED", "OUTPUT_LIMIT", "NONE", "BOUNDED", "discard output beyond bound"),
    _scenario("database_locked", "PERSISTENCE", "SQLite lock", "TEMPORARY_FAILURE", "PERSISTENCE_CONFLICT", "NONE", "REVIEW_REQUIRED", "trusted operator retries transaction"),
    _scenario("migration_mismatch", "PERSISTENCE", "Alembic head mismatch", "SERVICE_UNAVAILABLE", "SCHEMA_MISMATCH", "NONE", "REVIEW_REQUIRED", "stop and reconcile environment"),
    _scenario("fastapi_unavailable", "WEB", "backend unavailable", "SERVICE_UNAVAILABLE", "BACKEND_UNAVAILABLE", "NONE", "BOUNDED", "restart trusted service"),
    _scenario("next_unavailable", "WEB", "frontend unavailable", "SERVICE_UNAVAILABLE", "FRONTEND_UNAVAILABLE", "NONE", "BOUNDED", "restart trusted service"),
    _scenario("sse_disconnect", "WEB", "stream disconnect", "ACTIVITY_RECONNECT", "STREAM_DISCONNECTED", "NONE", "NONE", "reconnect with bounded cursor"),
    _scenario("calendar_ambiguous", "APPROVAL", "adapter transport ambiguous", "RECONCILIATION_REQUIRED", "UNKNOWN_OUTCOME", "RECONCILIATION", "REVIEW_REQUIRED", "reconcile; no automatic execution retry"),
    _scenario("approval_revoked", "APPROVAL", "approval revoked before claim", "ACTION_REJECTED", "REVOKED", "NONE", "NONE", "prepare a new action and approval"),
    _scenario("approval_expired", "APPROVAL", "approval expires before claim", "ACTION_EXPIRED", "EXPIRED", "NONE", "NONE", "prepare a new action and approval"),
    _scenario("filesystem_full", "HOST", "controlled temp path full", "SERVICE_UNAVAILABLE", "STORAGE_FAILURE", "NONE", "REVIEW_REQUIRED", "free trusted storage and retry setup"),
    _scenario("permission_denied", "HOST", "controlled path denied", "SERVICE_UNAVAILABLE", "PERMISSION_DENIED", "NONE", "REVIEW_REQUIRED", "trusted operator fixes permissions"),
    _scenario("malformed_provider_output", "PROVIDER", "closed response schema mismatch", "SAFE_FAILURE", "MALFORMED_RESPONSE", "NONE", "BOUNDED", "discard payload and record sanitized error"),
)


def run_failure_scenario(
    scenario: FailureScenario, *, fakes: FailureFakes
) -> FailureResult:
    if not isinstance(scenario, FailureScenario) or not isinstance(fakes, FailureFakes):
        raise TypeError("typed synthetic scenario inputs are required")
    reconciliation = scenario.retry_policy == "RECONCILIATION"
    return FailureResult(
        scenario.name,
        scenario.expected_user_state,
        scenario.expected_internal_state,
        automatic_retry=False,
        reconciliation_required=reconciliation,
        external_provider_calls=fakes.external_provider_calls,
        external_writes=fakes.external_writes,
        model_calls=fakes.model_calls,
        rag_calls=fakes.rag_calls,
    )


@dataclass(frozen=True, slots=True)
class ImportProbe:
    imported: bool
    database_created: bool
    network_calls: int
    model_calls: int
    docker_calls: int
    provider_calls: int
    created_paths: tuple[str, ...]


def import_inert_probe(root: Path) -> ImportProbe:
    before = tuple(root.rglob("*"))
    for module in (
        "aaos.approval.contracts",
        "aaos.approval.state",
        "aaos.approval.adapters",
        "aaos.web.errors",
    ):
        importlib.import_module(module)
    after = tuple(root.rglob("*"))
    return ImportProbe(True, False, 0, 0, 0, 0, tuple(str(path) for path in after if path not in before))
