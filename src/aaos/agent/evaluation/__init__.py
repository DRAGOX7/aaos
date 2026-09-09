"""Side-effect-free Phase 5 agent evaluation contracts and pure scoring."""

from .contracts import (
    BENCHMARK_SCHEMA_VERSION,
    EVENT_SCHEMA_VERSION,
    MAX_EXPECTED_TOOL_CALLS,
    METRIC_SCHEMA_VERSION,
    TRACE_SCHEMA_VERSION,
    BenchmarkCase,
    BenchmarkCategory,
    EvaluationContractError,
    EvaluationLanguage,
    EvaluationManifest,
    EvaluationSplit,
    FinalHeldoutLedger,
    ScopeOracle,
    build_approved_manifest,
)
from .metrics import EvaluationMetrics, EvaluationTrace, MetricValue, score_evaluation
from .reports import (
    EvaluationMetadata,
    EvaluationReport,
    PrivateEvaluationBundle,
    build_evaluation_report,
    build_private_bundle,
)

__all__ = [
    "BENCHMARK_SCHEMA_VERSION",
    "EVENT_SCHEMA_VERSION",
    "METRIC_SCHEMA_VERSION",
    "MAX_EXPECTED_TOOL_CALLS",
    "TRACE_SCHEMA_VERSION",
    "BenchmarkCase",
    "BenchmarkCategory",
    "EvaluationContractError",
    "EvaluationLanguage",
    "EvaluationManifest",
    "EvaluationMetrics",
    "EvaluationMetadata",
    "EvaluationReport",
    "EvaluationSplit",
    "EvaluationTrace",
    "FinalHeldoutLedger",
    "MetricValue",
    "PrivateEvaluationBundle",
    "ScopeOracle",
    "build_approved_manifest",
    "build_evaluation_report",
    "build_private_bundle",
    "score_evaluation",
]
