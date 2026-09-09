"""Versioned evaluation contracts for the C8 least-authority proposal.

The v1 evaluation contract remains immutable and continues to describe the
historical tool/scope-coupled proposal.  This module derives a separate v2
manifest uniformly from v1 cases: model-facing scoring is reduced to the
retrieve-versus-terminal decision while trusted execution oracles remain
private evaluator data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Iterable

from ..contracts import AgentErrorCode, TerminalResultKind, ToolName, canonical_json
from .contracts import (
    BenchmarkCase,
    BenchmarkCategory,
    EvaluationLanguage,
    EvaluationSplit,
    ScopeOracle,
    build_approved_manifest,
)
from .metrics import MetricValue

V2_BENCHMARK_SCHEMA_VERSION = "phase5-agent-benchmark-v2"
V2_TRACE_SCHEMA_VERSION = "phase5-agent-trace-v2"
V2_METRIC_SCHEMA_VERSION = "phase5-agent-evaluation-v2"
V2_EXPECTED_ACTION_RETRIEVE = "retrieve"
V2_EXPECTED_ACTION_TERMINAL = "terminal_text"
MAX_V2_TERMINAL_TEXT_CHARACTERS = 16_384
_SAFE_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_MAX_V2_REQUEST_TEXT_CHARACTERS = 16_384
_V2_SCHEMA_DESCRIPTOR = {
    "additional_properties": False,
    "model_visible_fields": ["request_text"],
    "proposal_actions": [V2_EXPECTED_ACTION_RETRIEVE, V2_EXPECTED_ACTION_TERMINAL],
    "proposal_terminal_text_max_characters": MAX_V2_TERMINAL_TEXT_CHARACTERS,
    "schema_version": V2_BENCHMARK_SCHEMA_VERSION,
}


class V2ExpectedAction(StrEnum):
    RETRIEVE = V2_EXPECTED_ACTION_RETRIEVE
    TERMINAL_TEXT = V2_EXPECTED_ACTION_TERMINAL


def _digest(value: object) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


V2_SCHEMA_FINGERPRINT = _digest(_V2_SCHEMA_DESCRIPTOR)


@dataclass(frozen=True, slots=True)
class V2BenchmarkCase:
    """Contract-only projection of one immutable v1 benchmark case."""

    case_id: str
    split: EvaluationSplit
    category: BenchmarkCategory
    language: EvaluationLanguage
    request_text: str
    expected_action: V2ExpectedAction
    expected_terminal_kind: TerminalResultKind | None = None
    expected_error_code: AgentErrorCode | None = None
    expected_grounded: bool | None = None
    expected_no_tool: bool = False
    trusted_execution_tool: ToolName | None = None
    trusted_scope_oracle: ScopeOracle = ScopeOracle.NONE
    requires_structured_repair: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.case_id, str)
            or _SAFE_CASE_ID.fullmatch(self.case_id.strip()) is None
        ):
            raise ValueError("case_id must be a safe logical identifier")
        object.__setattr__(self, "case_id", self.case_id.strip())
        if not isinstance(self.split, EvaluationSplit):
            raise TypeError("split must be an EvaluationSplit")
        if not isinstance(self.category, BenchmarkCategory):
            raise TypeError("category must be a BenchmarkCategory")
        if not isinstance(self.language, EvaluationLanguage):
            raise TypeError("language must be an EvaluationLanguage")
        if not isinstance(self.request_text, str) or not self.request_text.strip():
            raise ValueError("request_text must be non-blank")
        normalized_request = self.request_text.strip()
        if len(normalized_request) > _MAX_V2_REQUEST_TEXT_CHARACTERS:
            raise ValueError("request_text exceeds the v2 evaluation bound")
        object.__setattr__(self, "request_text", normalized_request)
        if not isinstance(self.expected_action, V2ExpectedAction):
            raise TypeError("expected_action must be a V2ExpectedAction")
        if self.expected_terminal_kind is not None and not isinstance(
            self.expected_terminal_kind, TerminalResultKind
        ):
            raise TypeError("expected_terminal_kind is unsupported")
        if self.expected_error_code is not None and not isinstance(
            self.expected_error_code, AgentErrorCode
        ):
            raise TypeError("expected_error_code is unsupported")
        if self.expected_grounded is not None and not isinstance(self.expected_grounded, bool):
            raise TypeError("expected_grounded must be a bool or None")
        if not isinstance(self.expected_no_tool, bool):
            raise TypeError("expected_no_tool must be a bool")
        if self.trusted_execution_tool is not None and not isinstance(
            self.trusted_execution_tool, ToolName
        ):
            raise TypeError("trusted_execution_tool is unsupported")
        if not isinstance(self.trusted_scope_oracle, ScopeOracle):
            raise TypeError("trusted_scope_oracle is unsupported")
        if not isinstance(self.requires_structured_repair, bool):
            raise TypeError("requires_structured_repair must be a bool")

    @classmethod
    def from_v1(cls, case: BenchmarkCase) -> V2BenchmarkCase:
        if not isinstance(case, BenchmarkCase):
            raise TypeError("case must be a BenchmarkCase")
        # This is a uniform contract projection, not case-specific tuning.
        # Only the one executable Phase 5 capability maps to RETRIEVE; all
        # other historical tool or adversarial requests are terminal/safe-
        # failure opportunities under C8.
        action = (
            V2ExpectedAction.RETRIEVE
            if case.expected_tool_name is ToolName.RAG_QUERY and not case.expected_no_tool
            else V2ExpectedAction.TERMINAL_TEXT
        )
        return cls(
            case_id=case.case_id,
            split=case.split,
            category=case.category,
            language=case.language,
            request_text=case.request_text,
            expected_action=action,
            expected_terminal_kind=case.expected_terminal_kind,
            expected_error_code=case.expected_error_code,
            expected_grounded=case.expected_grounded,
            expected_no_tool=case.expected_no_tool,
            trusted_execution_tool=case.expected_tool_name,
            trusted_scope_oracle=case.scope_oracle,
            requires_structured_repair=case.requires_structured_repair,
        )

    def model_visible_input(self) -> dict[str, str]:
        """Only request data crosses into model-facing evaluation."""

        return {"request_text": self.request_text}

    def trusted_oracle(self) -> dict[str, object]:
        """Private evaluator oracle; never include this in model prompts."""

        return {
            "expected_action": self.expected_action.value,
            "expected_error_code": (
                self.expected_error_code.value if self.expected_error_code else None
            ),
            "expected_grounded": self.expected_grounded,
            "expected_no_tool": self.expected_no_tool,
            "expected_terminal_kind": (
                self.expected_terminal_kind.value if self.expected_terminal_kind else None
            ),
            "trusted_execution_tool": (
                self.trusted_execution_tool.value if self.trusted_execution_tool else None
            ),
            "trusted_scope_oracle": self.trusted_scope_oracle.value,
        }

    def safe_identity(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "category": self.category.value,
            "expected_action": self.expected_action.value,
            "expected_error_code": (
                self.expected_error_code.value if self.expected_error_code else None
            ),
            "expected_grounded": self.expected_grounded,
            "expected_no_tool": self.expected_no_tool,
            "expected_terminal_kind": (
                self.expected_terminal_kind.value if self.expected_terminal_kind else None
            ),
            "language": self.language.value,
            "request_sha256": _digest(self.request_text),
            "requires_structured_repair": self.requires_structured_repair,
            "split": self.split.value,
        }


@dataclass(frozen=True, slots=True)
class V2EvaluationManifest:
    """Ordered, fingerprinted v2 membership independent of v1 objects."""

    benchmark_version: str
    cases: tuple[V2BenchmarkCase, ...]
    schema_version: str = V2_BENCHMARK_SCHEMA_VERSION
    source_contract_version: str = "phase5-agent-benchmark-v1"

    def __post_init__(self) -> None:
        if not isinstance(self.benchmark_version, str) or not self.benchmark_version.strip():
            raise ValueError("benchmark_version must be non-blank")
        object.__setattr__(self, "benchmark_version", self.benchmark_version.strip())
        if self.schema_version != V2_BENCHMARK_SCHEMA_VERSION:
            raise ValueError("unsupported v2 benchmark schema version")
        if (
            not isinstance(self.source_contract_version, str)
            or not self.source_contract_version.strip()
        ):
            raise ValueError("source_contract_version must be non-blank")
        object.__setattr__(self, "source_contract_version", self.source_contract_version.strip())
        if not isinstance(self.cases, tuple) or not self.cases:
            raise ValueError("cases must be a non-empty tuple")
        if not all(isinstance(case, V2BenchmarkCase) for case in self.cases):
            raise TypeError("cases must contain V2BenchmarkCase values")
        ids = tuple(case.case_id for case in self.cases)
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate case IDs are not permitted")

    @property
    def case_ids(self) -> tuple[str, ...]:
        return tuple(case.case_id for case in self.cases)

    @property
    def schema_fingerprint(self) -> str:
        """Fingerprint of the v2 contract independent of case membership."""

        return V2_SCHEMA_FINGERPRINT

    @property
    def dataset_fingerprint(self) -> str:
        return _digest(
            {
                "benchmark_version": self.benchmark_version,
                "cases": [case.safe_identity() for case in self.cases],
                "schema_version": self.schema_version,
                "source_contract_version": self.source_contract_version,
            }
        )

    @property
    def split_fingerprint(self) -> str:
        return _digest({"ordered_membership": [(c.case_id, c.split.value) for c in self.cases]})

    @property
    def calibration_case_count(self) -> int:
        return sum(case.split is EvaluationSplit.CALIBRATION for case in self.cases)

    @property
    def final_heldout_case_count(self) -> int:
        return sum(case.split is EvaluationSplit.FINAL_HELDOUT for case in self.cases)

    def language_counts(self, split: EvaluationSplit | str) -> dict[str, int]:
        try:
            selected = EvaluationSplit(split)
        except (TypeError, ValueError) as exc:
            raise ValueError("split must be an EvaluationSplit") from exc
        return {
            language.value: sum(
                case.split is selected and case.language is language for case in self.cases
            )
            for language in EvaluationLanguage
        }


@dataclass(frozen=True, slots=True)
class V2EvaluationTrace:
    """Sanitized trace whose model-facing result is action-level only."""

    case_id: str
    observed_action: V2ExpectedAction | None = None
    observed_terminal_kind: TerminalResultKind | None = None
    observed_error_code: AgentErrorCode | None = None
    trusted_rag_call_count: int = 0
    proposal_format_valid: bool = True
    proposal_repairs: int = 0
    model_calls: int = 0
    runtime_failure: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.case_id, str)
            or _SAFE_CASE_ID.fullmatch(self.case_id.strip()) is None
        ):
            raise ValueError("case_id must be a safe logical identifier")
        object.__setattr__(self, "case_id", self.case_id.strip())
        if self.observed_action is not None and not isinstance(
            self.observed_action, V2ExpectedAction
        ):
            raise TypeError("observed_action is unsupported")
        if self.observed_terminal_kind is not None and not isinstance(
            self.observed_terminal_kind, TerminalResultKind
        ):
            raise TypeError("observed_terminal_kind is unsupported")
        if self.observed_error_code is not None and not isinstance(
            self.observed_error_code, AgentErrorCode
        ):
            raise TypeError("observed_error_code is unsupported")
        for name, value in (
            ("trusted_rag_call_count", self.trusted_rag_call_count),
            ("proposal_repairs", self.proposal_repairs),
            ("model_calls", self.model_calls),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.proposal_repairs > 1:
            raise ValueError("proposal_repairs exceeds the approved bound")
        if not isinstance(self.proposal_format_valid, bool) or not isinstance(
            self.runtime_failure, bool
        ):
            raise TypeError("trace flags must be booleans")

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "model_calls": self.model_calls,
            "observed_action": self.observed_action.value if self.observed_action else None,
            "observed_error_code": (
                self.observed_error_code.value if self.observed_error_code else None
            ),
            "observed_terminal_kind": (
                self.observed_terminal_kind.value if self.observed_terminal_kind else None
            ),
            "proposal_format_valid": self.proposal_format_valid,
            "proposal_repairs": self.proposal_repairs,
            "runtime_failure": self.runtime_failure,
            "trusted_rag_call_count": self.trusted_rag_call_count,
            "trace_schema_version": V2_TRACE_SCHEMA_VERSION,
        }


@dataclass(frozen=True, slots=True)
class V2EvaluationMetrics:
    """Action/terminal metrics; trusted tool identity is not a model score."""

    action_accuracy: MetricValue
    terminal_accuracy: MetricValue
    proposal_format_validity: MetricValue
    case_accuracy: MetricValue
    runtime_failures: int

    def __post_init__(self) -> None:
        for name in (
            "action_accuracy",
            "terminal_accuracy",
            "proposal_format_validity",
            "case_accuracy",
        ):
            if not isinstance(getattr(self, name), MetricValue):
                raise TypeError(f"{name} must be a MetricValue")
        if isinstance(self.runtime_failures, bool) or not isinstance(self.runtime_failures, int):
            raise TypeError("runtime_failures must be an integer")
        if self.runtime_failures < 0:
            raise ValueError("runtime_failures must be non-negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "case_accuracy": self.case_accuracy.to_dict(),
            "metric_schema_version": V2_METRIC_SCHEMA_VERSION,
            "proposal_format_validity": self.proposal_format_validity.to_dict(),
            "runtime_failures": self.runtime_failures,
            "terminal_accuracy": self.terminal_accuracy.to_dict(),
            "action_accuracy": self.action_accuracy.to_dict(),
        }


def score_v2_evaluation(
    manifest: V2EvaluationManifest, traces: Iterable[V2EvaluationTrace]
) -> V2EvaluationMetrics:
    """Score only C8 model responsibilities and trusted end-to-end outcome."""

    if not isinstance(manifest, V2EvaluationManifest):
        raise TypeError("manifest must be a V2EvaluationManifest")
    by_id = {trace.case_id: trace for trace in traces}
    if set(by_id) != set(manifest.case_ids):
        raise ValueError("traces must contain exactly one entry for every manifest case")
    action_hits = sum(
        by_id[case.case_id].observed_action is case.expected_action for case in manifest.cases
    )
    terminal_cases = [
        case for case in manifest.cases if case.expected_action is V2ExpectedAction.TERMINAL_TEXT
    ]
    terminal_hits = sum(
        by_id[case.case_id].observed_terminal_kind is case.expected_terminal_kind
        for case in terminal_cases
    )
    format_hits = sum(by_id[case.case_id].proposal_format_valid for case in manifest.cases)
    case_hits = sum(
        by_id[case.case_id].observed_action is case.expected_action
        and (
            case.expected_action is V2ExpectedAction.RETRIEVE
            or by_id[case.case_id].observed_terminal_kind is case.expected_terminal_kind
        )
        for case in manifest.cases
    )
    return V2EvaluationMetrics(
        action_accuracy=MetricValue(action_hits, len(manifest.cases)),
        terminal_accuracy=MetricValue(terminal_hits, len(terminal_cases)),
        proposal_format_validity=MetricValue(format_hits, len(manifest.cases)),
        case_accuracy=MetricValue(case_hits, len(manifest.cases)),
        runtime_failures=sum(by_id[case.case_id].runtime_failure for case in manifest.cases),
    )


def migrate_v1_cases(cases: Iterable[BenchmarkCase]) -> tuple[V2BenchmarkCase, ...]:
    """Apply the same contract-only migration to every supplied v1 case."""

    return tuple(V2BenchmarkCase.from_v1(case) for case in cases)


def build_v2_manifests() -> tuple[V2EvaluationManifest, V2EvaluationManifest]:
    """Build separate v2 calibration/heldout manifests without model runs."""

    v1 = build_approved_manifest()
    calibration = V2EvaluationManifest(
        benchmark_version="phase5-agent-aaos-v2-calibration",
        cases=migrate_v1_cases(
            case for case in v1.cases if case.split is EvaluationSplit.CALIBRATION
        ),
    )
    heldout = V2EvaluationManifest(
        benchmark_version="phase5-agent-aaos-v2-heldout",
        cases=migrate_v1_cases(
            case for case in v1.cases if case.split is EvaluationSplit.FINAL_HELDOUT
        ),
    )
    return calibration, heldout


__all__ = [
    "MAX_V2_TERMINAL_TEXT_CHARACTERS",
    "V2_BENCHMARK_SCHEMA_VERSION",
    "V2_EXPECTED_ACTION_RETRIEVE",
    "V2_EXPECTED_ACTION_TERMINAL",
    "V2_METRIC_SCHEMA_VERSION",
    "V2_SCHEMA_FINGERPRINT",
    "V2_TRACE_SCHEMA_VERSION",
    "V2BenchmarkCase",
    "V2EvaluationManifest",
    "V2ExpectedAction",
    "V2EvaluationMetrics",
    "V2EvaluationTrace",
    "build_v2_manifests",
    "migrate_v1_cases",
    "score_v2_evaluation",
]
