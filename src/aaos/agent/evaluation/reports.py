"""Safe, versioned evaluation and private-bundle report envelopes."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from hashlib import sha256
from types import MappingProxyType
from typing import Iterable, Mapping

from ..contracts import AgentErrorCode, TerminalResultKind, ToolName, canonical_json
from .contracts import (
    EVENT_SCHEMA_VERSION,
    METRIC_SCHEMA_VERSION,
    TRACE_SCHEMA_VERSION,
    BenchmarkCase,
    BenchmarkCategory,
    EvaluationManifest,
    ScopeOracle,
)
from .metrics import EvaluationMetrics, EvaluationTrace, score_evaluation


def _digest(value: object) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _sha256(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


_SAFE_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_TOOL_VALUES = {name.value for name in ToolName}
_TERMINAL_VALUES = {kind.value for kind in TerminalResultKind}
_ERROR_VALUES = {code.value for code in AgentErrorCode}
_CATEGORY_VALUES = {category.value for category in BenchmarkCategory}
_SCOPE_VALUES = {scope.value for scope in ScopeOracle}
_PRIVATE_BUNDLE_TOKEN = object()


@dataclass(frozen=True, slots=True)
class EvaluationMetadata:
    benchmark_version: str
    dataset_fingerprint: str
    split_fingerprint: str
    runtime_policy_version: str
    runtime_policy_fingerprint: str
    event_schema_version: str = EVENT_SCHEMA_VERSION
    trace_schema_version: str = TRACE_SCHEMA_VERSION
    metric_schema_version: str = METRIC_SCHEMA_VERSION
    retention_policy_version: str = "phase5-local-private-bundle-v1"

    def __post_init__(self) -> None:
        for name in ("benchmark_version", "runtime_policy_version", "retention_policy_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-blank")
        _sha256(self.dataset_fingerprint, "dataset_fingerprint")
        _sha256(self.split_fingerprint, "split_fingerprint")
        _sha256(self.runtime_policy_fingerprint, "runtime_policy_fingerprint")
        if self.event_schema_version != EVENT_SCHEMA_VERSION:
            raise ValueError("unsupported event schema version")
        if self.trace_schema_version != TRACE_SCHEMA_VERSION:
            raise ValueError("unsupported trace schema version")
        if self.metric_schema_version != METRIC_SCHEMA_VERSION:
            raise ValueError("unsupported metric schema version")

    def to_dict(self) -> dict[str, str]:
        return {
            "benchmark_version": self.benchmark_version,
            "dataset_fingerprint": self.dataset_fingerprint,
            "event_schema_version": self.event_schema_version,
            "metric_schema_version": self.metric_schema_version,
            "retention_policy_version": self.retention_policy_version,
            "runtime_policy_fingerprint": self.runtime_policy_fingerprint,
            "runtime_policy_version": self.runtime_policy_version,
            "split_fingerprint": self.split_fingerprint,
            "trace_schema_version": self.trace_schema_version,
        }


@dataclass(frozen=True, slots=True)
class PrivateEvaluationBundle:
    """Minimal local post-hoc inputs; raw prompts/results are intentionally absent."""

    benchmark_fingerprint: str
    split_fingerprint: str
    manifest_case_ids: tuple[str, ...]
    rows: tuple[Mapping[str, object], ...]
    schema_version: str = METRIC_SCHEMA_VERSION
    trace_schema_version: str = TRACE_SCHEMA_VERSION
    _factory_token: object = field(default=None, repr=False, compare=False)
    _fingerprint: str = field(init=False, repr=False, compare=True)

    def __post_init__(self) -> None:
        if self._factory_token is not _PRIVATE_BUNDLE_TOKEN:
            raise ValueError("private bundles must be created by the trusted evaluator")
        _sha256(self.benchmark_fingerprint, "benchmark_fingerprint")
        _sha256(self.split_fingerprint, "split_fingerprint")
        if not isinstance(self.manifest_case_ids, tuple) or not self.manifest_case_ids:
            raise ValueError("manifest_case_ids must be a non-empty tuple")
        if len(set(self.manifest_case_ids)) != len(self.manifest_case_ids):
            raise ValueError("manifest_case_ids must be unique")
        if not all(
            isinstance(case_id, str) and _SAFE_CASE_ID.fullmatch(case_id)
            for case_id in self.manifest_case_ids
        ):
            raise ValueError("manifest_case_ids contain an unsafe identifier")
        if self.schema_version != METRIC_SCHEMA_VERSION:
            raise ValueError("unsupported private bundle schema version")
        if self.trace_schema_version != TRACE_SCHEMA_VERSION:
            raise ValueError("unsupported trace schema version")
        if not isinstance(self.rows, tuple) or not self.rows:
            raise ValueError("private bundle rows must be non-empty")
        if len(self.rows) != len(self.manifest_case_ids):
            raise ValueError("private bundle must contain exactly one row per case")
        if not all(isinstance(row, Mapping) for row in self.rows):
            raise TypeError("private bundle rows must be mappings")
        row_ids = tuple(row.get("case_id") for row in self.rows)
        if row_ids != self.manifest_case_ids:
            raise ValueError("private bundle rows must match manifest order")
        normalized_rows: list[Mapping[str, object]] = []
        for row in self.rows:
            if not isinstance(row, Mapping):
                raise TypeError("private bundle rows must be mappings")
            if set(row) - _SAFE_ROW_FIELDS:
                raise ValueError("private bundle contains an unsupported field")
            if set(row) != _REQUIRED_ROW_FIELDS:
                raise ValueError("private bundle row is incomplete")
            case_id = row.get("case_id")
            if not isinstance(case_id, str) or _SAFE_CASE_ID.fullmatch(case_id) is None:
                raise ValueError("private bundle case_id is invalid")
            language = row.get("language")
            if language not in {"en", "ar"}:
                raise ValueError("private bundle language is invalid")
            split = row.get("split")
            if split not in {"development", "calibration", "final_heldout"}:
                raise ValueError("private bundle split is invalid")
            if row["category"] not in _CATEGORY_VALUES:
                raise ValueError("private bundle category is invalid")
            if row["scope_oracle"] not in _SCOPE_VALUES:
                raise ValueError("private bundle scope is invalid")
            for name in (
                "expected_tool_name",
                "observed_terminal_kind",
                "observed_error_code",
                "expected_terminal_kind",
                "expected_error_code",
                "observed_scope",
            ):
                value = row[name]
                if name.endswith("tool_name"):
                    allowed = _TOOL_VALUES
                elif name.endswith("scope"):
                    allowed = _SCOPE_VALUES
                elif name.endswith("terminal_kind"):
                    allowed = _TERMINAL_VALUES
                else:
                    allowed = _ERROR_VALUES
                if value is not None and value not in allowed:
                    raise ValueError("private bundle contains an invalid enum value")
            tool_names = row["observed_tool_names"]
            if not isinstance(tool_names, (list, tuple)) or any(
                value not in _TOOL_VALUES for value in tool_names
            ):
                raise ValueError("private bundle observed tools are invalid")
            for name in ("proposal_repairs", "model_calls"):
                value = row[name]
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ValueError("private bundle counters are invalid")
            for name in ("expected_max_tool_calls", "observed_tool_call_count"):
                value = row[name]
                if value is not None and (
                    isinstance(value, bool) or not isinstance(value, int) or value < 0
                ):
                    raise ValueError("private bundle counters are invalid")
            if row["expected_max_tool_calls"] == 0:
                raise ValueError("private bundle expected_max_tool_calls is invalid")
            for name in (
                "expected_no_tool",
                "requires_structured_repair",
                "expected_grounded",
                "expected_permission_allowed",
                "observed_grounded",
                "observed_permission_allowed",
            ):
                value = row[name]
                if value is not None and not isinstance(value, bool):
                    raise ValueError("private bundle boolean field is invalid")
            flags = row.get("security_flags")
            if not isinstance(flags, Mapping) or not all(
                isinstance(key, str) and isinstance(value, bool) for key, value in flags.items()
            ):
                raise ValueError("private bundle security flags are invalid")
            if set(flags) - _FLAG_VALUES:
                raise ValueError("private bundle security flags are unsupported")
            normalized_row = dict(row)
            normalized_row["observed_tool_names"] = tuple(tool_names)
            normalized_row["security_flags"] = MappingProxyType(dict(flags))
            normalized_rows.append(MappingProxyType(normalized_row))
        object.__setattr__(self, "rows", tuple(normalized_rows))
        object.__setattr__(self, "_fingerprint", _digest(self.to_dict()))

    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    def to_dict(self) -> dict[str, object]:
        return {
            "benchmark_fingerprint": self.benchmark_fingerprint,
            "manifest_case_ids": list(self.manifest_case_ids),
            "rows": [dict(row) for row in self.rows],
            "schema_version": self.schema_version,
            "split_fingerprint": self.split_fingerprint,
            "trace_schema_version": self.trace_schema_version,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_dict())


_SAFE_ROW_FIELDS = frozenset(
    {
        "case_id",
        "category",
        "language",
        "split",
        "scope_oracle",
        "expected_tool_name",
        "expected_terminal_kind",
        "expected_error_code",
        "expected_no_tool",
        "requires_structured_repair",
        "expected_max_tool_calls",
        "expected_grounded",
        "expected_permission_allowed",
        "observed_tool_names",
        "observed_terminal_kind",
        "observed_error_code",
        "observed_scope",
        "observed_tool_call_count",
        "observed_grounded",
        "observed_permission_allowed",
        "proposal_repairs",
        "model_calls",
        "security_flags",
    }
)
_REQUIRED_ROW_FIELDS = frozenset(_SAFE_ROW_FIELDS)
_FLAG_VALUES = frozenset(
    {
        "unknown_tool_execution",
        "unauthorized_tool_execution",
        "invalid_argument_handler_execution",
        "arbitrary_host_file_access",
        "prompt_injection_policy_bypass",
        "loop_limit_escape",
        "arbitrary_code_execution",
        "unapproved_external_write",
        "trusted_orchestration_runtime_failure",
        "grounded_lineage_violation",
        "private_payload_exposure",
        "raw_result_fallback",
        "invalid_output_exposure",
        "secret_exposure",
        "operational_path_exposure",
        "raw_exception_exposure",
        "contradictory_late_success",
        "event_privacy_violation",
        "model_runtime_policy_mutation",
        "model_tool_contract_mutation",
        "scope_policy_bypass",
        "permission_policy_bypass",
        "disabled_tool_execution",
        "runtime_failure",
        "model_timeout",
        "tool_timeout",
        "repetition_limit_escape",
        "runtime_limit_bypass",
    }
)
_SAFE_SUMMARY_FIELDS = frozenset(
    {
        "case_id",
        "category",
        "language",
        "scope_oracle",
        "observed_error_code",
        "observed_grounded",
        "observed_permission_allowed",
        "observed_scope",
        "observed_terminal_kind",
        "observed_tool_call_count",
        "observed_tool_names",
        "split",
    }
)
_REQUIRED_SUMMARY_FIELDS = frozenset(_SAFE_SUMMARY_FIELDS)


def build_private_bundle(
    manifest: EvaluationManifest, traces: Iterable[EvaluationTrace]
) -> PrivateEvaluationBundle:
    """Retain only exact post-hoc metric inputs and safe identifiers."""

    traces_tuple = tuple(traces)
    metrics = score_evaluation(manifest, traces_tuple)
    del metrics  # validation is intentional; the bundle does not duplicate metrics.
    by_id = {trace.case_id: trace for trace in traces_tuple}
    rows: list[dict[str, object]] = []
    for case in manifest.cases:
        trace = by_id[case.case_id]
        rows.append(
            {
                "case_id": case.case_id,
                "category": case.category.value,
                "scope_oracle": case.scope_oracle.value,
                "expected_error_code": (
                    case.expected_error_code.value if case.expected_error_code is not None else None
                ),
                "expected_no_tool": case.expected_no_tool,
                "requires_structured_repair": case.requires_structured_repair,
                "expected_max_tool_calls": case.expected_max_tool_calls,
                "expected_grounded": case.expected_grounded,
                "expected_permission_allowed": case.expected_permission_allowed,
                "expected_terminal_kind": (
                    case.expected_terminal_kind.value
                    if case.expected_terminal_kind is not None
                    else None
                ),
                "expected_tool_name": (
                    case.expected_tool_name.value if case.expected_tool_name is not None else None
                ),
                "language": case.language.value,
                "model_calls": trace.model_calls,
                "observed_error_code": (
                    trace.observed_error_code.value
                    if trace.observed_error_code is not None
                    else None
                ),
                "observed_scope": (
                    trace.observed_scope.value if trace.observed_scope is not None else None
                ),
                "observed_tool_call_count": trace.observed_tool_call_count,
                "observed_grounded": trace.observed_grounded,
                "observed_permission_allowed": trace.observed_permission_allowed,
                "observed_terminal_kind": (
                    trace.observed_terminal_kind.value
                    if trace.observed_terminal_kind is not None
                    else None
                ),
                "observed_tool_names": [name.value for name in trace.observed_tool_names],
                "proposal_repairs": trace.proposal_repairs,
                "security_flags": trace.to_dict()["security_flags"],
                "split": case.split.value,
            }
        )
    return PrivateEvaluationBundle(
        benchmark_fingerprint=manifest.dataset_fingerprint,
        split_fingerprint=manifest.split_fingerprint,
        manifest_case_ids=manifest.case_ids,
        rows=tuple(rows),
        _factory_token=_PRIVATE_BUNDLE_TOKEN,
    )


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    metadata: EvaluationMetadata
    metrics: EvaluationMetrics
    trace_summaries: tuple[Mapping[str, object], ...]
    private_bundle_fingerprint: str
    schema_version: str = METRIC_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != METRIC_SCHEMA_VERSION:
            raise ValueError("unsupported evaluation result schema version")
        _sha256(self.private_bundle_fingerprint, "private_bundle_fingerprint")
        if not isinstance(self.trace_summaries, tuple) or not self.trace_summaries:
            raise ValueError("trace_summaries must be a non-empty tuple")
        normalized: list[Mapping[str, object]] = []
        seen: set[str] = set()
        for summary in self.trace_summaries:
            if not isinstance(summary, Mapping):
                raise TypeError("trace summaries must be mappings")
            if set(summary) - _SAFE_SUMMARY_FIELDS:
                raise ValueError("trace summary contains an unsupported field")
            if set(summary) != _REQUIRED_SUMMARY_FIELDS:
                raise ValueError("trace summary is incomplete")
            case_id = summary["case_id"]
            if not isinstance(case_id, str) or not _SAFE_CASE_ID.fullmatch(case_id):
                raise ValueError("trace summary case_id is invalid")
            if case_id in seen:
                raise ValueError("trace summary case IDs must be unique")
            seen.add(case_id)
            if summary["language"] not in {"en", "ar"}:
                raise ValueError("trace summary language is invalid")
            if summary["category"] not in _CATEGORY_VALUES:
                raise ValueError("trace summary category is invalid")
            if summary["scope_oracle"] not in _SCOPE_VALUES:
                raise ValueError("trace summary scope is invalid")
            if summary["split"] not in {"development", "calibration", "final_heldout"}:
                raise ValueError("trace summary split is invalid")
            tool_names = summary["observed_tool_names"]
            if not isinstance(tool_names, (list, tuple)) or any(
                value not in _TOOL_VALUES for value in tool_names
            ):
                raise ValueError("trace summary observed tools are invalid")
            for name, allowed in (
                ("observed_terminal_kind", _TERMINAL_VALUES),
                ("observed_error_code", _ERROR_VALUES),
                ("observed_scope", _SCOPE_VALUES),
            ):
                if summary[name] is not None and summary[name] not in allowed:
                    raise ValueError("trace summary enum value is invalid")
            for name in ("observed_grounded", "observed_permission_allowed"):
                value = summary[name]
                if value is not None and not isinstance(value, bool):
                    raise ValueError("trace summary boolean is invalid")
            count = summary["observed_tool_call_count"]
            if count is not None and (
                isinstance(count, bool) or not isinstance(count, int) or count < 0
            ):
                raise ValueError("trace summary tool count is invalid")
            normalized_summary = dict(summary)
            normalized_summary["observed_tool_names"] = tuple(tool_names)
            normalized.append(MappingProxyType(normalized_summary))
        object.__setattr__(self, "trace_summaries", tuple(normalized))

    def to_dict(self) -> dict[str, object]:
        return {
            "metadata": self.metadata.to_dict(),
            "metrics": self.metrics.to_dict(),
            "private_bundle_fingerprint": self.private_bundle_fingerprint,
            "schema_version": self.schema_version,
            "trace_summaries": [dict(summary) for summary in self.trace_summaries],
        }

    def to_json(self) -> str:
        """Return deterministic JSON without writing to a caller-controlled path."""

        return canonical_json(self.to_dict())


def build_evaluation_report(
    manifest: EvaluationManifest,
    traces: Iterable[EvaluationTrace],
    *,
    runtime_policy_version: str,
    runtime_policy_fingerprint: str,
) -> EvaluationReport:
    traces_tuple = tuple(traces)
    metrics = score_evaluation(manifest, traces_tuple)
    bundle = build_private_bundle(manifest, traces_tuple)
    by_id = {trace.case_id: trace for trace in traces_tuple}
    summaries = tuple(_trace_summary(case, by_id[case.case_id]) for case in manifest.cases)
    return EvaluationReport(
        metadata=EvaluationMetadata(
            benchmark_version=manifest.benchmark_version,
            dataset_fingerprint=manifest.dataset_fingerprint,
            split_fingerprint=manifest.split_fingerprint,
            runtime_policy_version=runtime_policy_version,
            runtime_policy_fingerprint=runtime_policy_fingerprint,
        ),
        metrics=metrics,
        trace_summaries=summaries,
        private_bundle_fingerprint=bundle.fingerprint,
    )


def _trace_summary(case: BenchmarkCase, trace: EvaluationTrace) -> dict[str, object]:
    """Create a safe summary after the manifest lookup has been validated."""

    # The explicit attributes keep this serializer free of arbitrary object dumping.
    return {
        "case_id": case.case_id,
        "category": case.category.value,
        "language": case.language.value,
        "scope_oracle": case.scope_oracle.value,
        "observed_error_code": (
            trace.observed_error_code.value if trace.observed_error_code is not None else None
        ),
        "observed_terminal_kind": (
            trace.observed_terminal_kind.value if trace.observed_terminal_kind is not None else None
        ),
        "observed_scope": (
            trace.observed_scope.value if trace.observed_scope is not None else None
        ),
        "observed_grounded": trace.observed_grounded,
        "observed_permission_allowed": trace.observed_permission_allowed,
        "observed_tool_call_count": trace.observed_tool_call_count,
        "observed_tool_names": [name.value for name in trace.observed_tool_names],
        "split": case.split.value,
    }


__all__ = [
    "EvaluationMetadata",
    "EvaluationReport",
    "PrivateEvaluationBundle",
    "build_evaluation_report",
    "build_private_bundle",
]
