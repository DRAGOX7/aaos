"""C10/V4 ownership-aware routing acceptance contract.

V4 keeps the V1, V2 and V3 artifacts immutable.  It adds an outcome-
independent routing denominator and a balanced, deterministic routing corpus;
trusted-system cases are migrated from V3 without being counted as model
routing opportunities.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from hashlib import sha256
from typing import Iterable

from ..contracts import AgentErrorCode, TerminalResultKind, ToolName, canonical_json
from .contracts import BenchmarkCategory, EvaluationLanguage, EvaluationSplit
from .metrics import MetricValue
from .v2 import V2ExpectedAction
from .v3 import (
    V3_SCHEMA_FINGERPRINT,
    V3BenchmarkCase,
    V3EvaluationOwner,
    V3ProposalFormatEfficiencyMetrics,
    V3RuntimeEnvironmentMetrics,
    V3SecurityHardGateMetrics,
    V3TrustedSystemMetrics,
    build_v3_manifests,
)

V4_BENCHMARK_SCHEMA_VERSION = "phase5-agent-benchmark-v4"
V4_TRACE_SCHEMA_VERSION = "phase5-agent-trace-v4"
V4_METRIC_SCHEMA_VERSION = "phase5-agent-evaluation-v4"
V4_SOURCE_CONTRACT_VERSION = "phase5-agent-benchmark-v3"
V4_ROUTING_AUTHORING_SPEC_VERSION = "c10-routing-matrix-v1"
V4_CALIBRATION_SOURCE = "DETERMINISTIC_C10_ROUTING_MATRIX_V1_PLUS_C9_TRUSTED_MIGRATION"
V4_HELDOUT_SOURCE = (
    "DETERMINISTIC_PRE_V4_CALIBRATION_C10_ROUTING_MATRIX_V1_PLUS_C9_TRUSTED_MIGRATION"
)
V4_MIN_ROUTING_CASES = 64
V4_MIN_CASES_PER_ROUTING_STRATUM = 16
V4_ROUTING_ACCURACY_TARGET = 0.95
V4_ROUTING_ACCURACY_NUMERATOR = 95
V4_ROUTING_ACCURACY_DENOMINATOR = 100
V4_CALIBRATION_ROUTING_CASE_COUNT = 64
V4_HELDOUT_ROUTING_CASE_COUNT = 64
V4_ROUTING_STRATA = (
    "en/retrieve",
    "en/terminal_text",
    "ar/retrieve",
    "ar/terminal_text",
)
V4_NEAR_DUPLICATE_POLICY = (
    "NFKC+casefold+collapse-whitespace exact equality; no model embeddings or results"
)
_SAFE_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_MAX_REQUEST_TEXT_CHARACTERS = 16_384


_CALIBRATION_RETRIEVE_EN = (
    "Using the local university materials, what guidance is given about grading policy?",
    "Using the local university materials, what guidance is given about laboratory safety?",
    "Using the local university materials, what guidance is given about office hours?",
    "Using the local university materials, what guidance is given about assignment submission?",
    "Using the local university materials, what guidance is given about attendance?",
    "Using the local university materials, what guidance is given about exam review?",
    "Using the local university materials, what guidance is given about project milestones?",
    "Using the local university materials, what guidance is given about group work?",
    "Using the local university materials, what guidance is given about the reading list?",
    "Using the local university materials, what guidance is given about late work?",
    "Using the local university materials, what guidance is given about feedback?",
    "Using the local university materials, what guidance is given about accessibility?",
    "Using the local university materials, what guidance is given about practical sessions?",
    "Using the local university materials, what guidance is given about assessment weighting?",
    "Using the local university materials, what guidance is given about course prerequisites?",
    "Using the local university materials, what guidance is given about academic integrity?",
)
_CALIBRATION_TERMINAL_EN = (
    "Please acknowledge this message without consulting university materials.",
    "Please provide a brief conversational greeting without retrieving local knowledge.",
    "Please restate that the request was received, without answering a factual question.",
    "Please give a short neutral closing message without using course information.",
    "Please ask the user to clarify their goal; no local facts are requested.",
    "Please confirm readiness for the next instruction without consulting documents.",
    "Please provide a bounded courtesy response and do not retrieve course content.",
    "Please say that you can help after clarification, without consulting local sources.",
    "Please acknowledge the user's preference without making a factual claim.",
    "Please return a short non-grounded confirmation and do not search materials.",
    "Please ask whether the user wants information or conversation; do not retrieve.",
    "Please provide a concise farewell without using university knowledge.",
    "Please confirm that no factual answer was requested and avoid retrieval.",
    "Please respond with a short neutral acknowledgement only.",
    "Please state that the message is understood without consulting local materials.",
    "Please provide a bounded conversational response without external or local lookup.",
)
_CALIBRATION_RETRIEVE_AR = (
    "بالاعتماد على المواد الجامعية المحلية، ما الإرشادات المتعلقة بسياسة التقييم؟",
    "بالاعتماد على المواد الجامعية المحلية، ما الإرشادات المتعلقة بسلامة المختبر؟",
    "بالاعتماد على المواد الجامعية المحلية، ما الإرشادات المتعلقة بالساعات المكتبية؟",
    "بالاعتماد على المواد الجامعية المحلية، ما الإرشادات المتعلقة بتسليم الواجب؟",
    "بالاعتماد على المواد الجامعية المحلية، ما الإرشادات المتعلقة بالحضور؟",
    "بالاعتماد على المواد الجامعية المحلية، ما الإرشادات المتعلقة بمراجعة الامتحان؟",
    "بالاعتماد على المواد الجامعية المحلية، ما الإرشادات المتعلقة بمراحل المشروع؟",
    "بالاعتماد على المواد الجامعية المحلية، ما الإرشادات المتعلقة بالعمل الجماعي؟",
    "بالاعتماد على المواد الجامعية المحلية، ما الإرشادات المتعلقة بقائمة القراءة؟",
    "بالاعتماد على المواد الجامعية المحلية، ما الإرشادات المتعلقة بالتسليم المتأخر؟",
    "بالاعتماد على المواد الجامعية المحلية، ما الإرشادات المتعلقة بالتغذية الراجعة؟",
    "بالاعتماد على المواد الجامعية المحلية، ما الإرشادات المتعلقة بإتاحة الوصول؟",
    "بالاعتماد على المواد الجامعية المحلية، ما الإرشادات المتعلقة بالجلسات العملية؟",
    "بالاعتماد على المواد الجامعية المحلية، ما الإرشادات المتعلقة بوزن التقييم؟",
    "بالاعتماد على المواد الجامعية المحلية، ما الإرشادات المتعلقة بمتطلبات المقرر؟",
    "بالاعتماد على المواد الجامعية المحلية، ما الإرشادات المتعلقة بالنزاهة الأكاديمية؟",
)
_CALIBRATION_TERMINAL_AR = (
    "يرجى تأكيد استلام هذه الرسالة دون الرجوع إلى المواد الجامعية.",
    "يرجى تقديم تحية قصيرة دون استرجاع معرفة محلية.",
    "يرجى إعادة صياغة تأكيد الاستلام دون الإجابة عن سؤال واقعي.",
    "يرجى تقديم خاتمة محايدة قصيرة دون استخدام معلومات المقرر.",
    "يرجى طلب توضيح الهدف؛ لا توجد حقائق محلية مطلوبة.",
    "يرجى تأكيد الاستعداد للتعليمات التالية دون الرجوع إلى الوثائق.",
    "يرجى تقديم رد مجاملة محدود وعدم استرجاع محتوى المقرر.",
    "يرجى القول إنك تستطيع المساعدة بعد التوضيح دون الرجوع إلى المصادر المحلية.",
    "يرجى تأكيد تفضيل المستخدم دون ذكر ادعاء واقعي.",
    "يرجى إعادة تأكيد غير مبنية على مصادر ودون البحث في المواد.",
    "يرجى السؤال عما إذا كان المستخدم يريد معلومات أو محادثة؛ لا تسترجع شيئاً.",
    "يرجى تقديم وداع موجز دون استخدام معرفة جامعية.",
    "يرجى تأكيد عدم طلب إجابة واقعية وتجنب الاسترجاع.",
    "يرجى الرد بتأكيد محايد وموجز فقط.",
    "يرجى ذكر أن الرسالة مفهومة دون الرجوع إلى المواد المحلية.",
    "يرجى تقديم رد محادثي محدود دون بحث خارجي أو محلي.",
)

_HELDOUT_RETRIEVE_EN = (
    "Consult the local course sources and summarize the stated enrollment rule.",
    "Consult the local course sources and summarize the stated participation rule.",
    "Consult the local course sources and summarize the stated tutorial process.",
    "Consult the local course sources and summarize the stated examination format.",
    "Consult the local course sources and summarize the stated project deliverables.",
    "Consult the local course sources and summarize the stated consultation process.",
    "Consult the local course sources and summarize the stated marking review process.",
    "Consult the local course sources and summarize the stated collaboration rule.",
    "Consult the local course sources and summarize the stated reading expectation.",
    "Consult the local course sources and summarize the stated deadline policy.",
    "Consult the local course sources and summarize the stated feedback process.",
    "Consult the local course sources and summarize the stated support arrangement.",
    "Consult the local course sources and summarize the stated practical requirement.",
    "Consult the local course sources and summarize the stated assessment structure.",
    "Consult the local course sources and summarize the stated prerequisite rule.",
    "Consult the local course sources and summarize the stated conduct expectation.",
)
_HELDOUT_TERMINAL_EN = (
    "Acknowledge this note without looking up any course or university fact.",
    "Offer a concise greeting; no local-information answer is requested.",
    "Confirm receipt and wait for a substantive question.",
    "Return a short neutral closing without consulting local sources.",
    "Ask the user to clarify what they need, without retrieving information.",
    "Confirm that you are ready for another instruction, without document access.",
    "Give a brief courtesy response and do not search the course materials.",
    "Say that clarification is needed before any local lookup is useful.",
    "Acknowledge the preference without asserting any factual content.",
    "Return a bounded confirmation without consulting local sources.",
    "Ask whether the user wants a fact lookup or a conversational reply.",
    "Provide a concise farewell without university information.",
    "Confirm that this is not a request for factual retrieval.",
    "Return a neutral acknowledgement only, without local lookup.",
    "State that the message is understood and wait for a question.",
    "Provide a bounded conversational response without source access.",
)
_HELDOUT_RETRIEVE_AR = (
    "ارجع إلى المصادر المحلية للمقرر ولخّص قاعدة التسجيل المذكورة.",
    "ارجع إلى المصادر المحلية للمقرر ولخّص قاعدة المشاركة المذكورة.",
    "ارجع إلى المصادر المحلية للمقرر ولخّص آلية الجلسات التعليمية المذكورة.",
    "ارجع إلى المصادر المحلية للمقرر ولخّص صيغة الامتحان المذكورة.",
    "ارجع إلى المصادر المحلية للمقرر ولخّص مخرجات المشروع المذكورة.",
    "ارجع إلى المصادر المحلية للمقرر ولخّص آلية الاستشارة المذكورة.",
    "ارجع إلى المصادر المحلية للمقرر ولخّص آلية مراجعة العلامات المذكورة.",
    "ارجع إلى المصادر المحلية للمقرر ولخّص قاعدة التعاون المذكورة.",
    "ارجع إلى المصادر المحلية للمقرر ولخّص توقعات القراءة المذكورة.",
    "ارجع إلى المصادر المحلية للمقرر ولخّص سياسة المواعيد النهائية المذكورة.",
    "ارجع إلى المصادر المحلية للمقرر ولخّص آلية الملاحظات المذكورة.",
    "ارجع إلى المصادر المحلية للمقرر ولخّص ترتيبات الدعم المذكورة.",
    "ارجع إلى المصادر المحلية للمقرر ولخّص المتطلب العملي المذكور.",
    "ارجع إلى المصادر المحلية للمقرر ولخّص هيكل التقييم المذكور.",
    "ارجع إلى المصادر المحلية للمقرر ولخّص قاعدة المتطلبات السابقة المذكورة.",
    "ارجع إلى المصادر المحلية للمقرر ولخّص توقعات السلوك المذكورة.",
)
_HELDOUT_TERMINAL_AR = (
    "أكد استلام هذه الملاحظة دون البحث عن أي حقيقة جامعية أو من المقرر.",
    "قدّم تحية موجزة؛ لا توجد إجابة عن معلومات محلية مطلوبة.",
    "أكد الاستلام وانتظر سؤالاً موضوعياً.",
    "قدّم خاتمة محايدة قصيرة دون الرجوع إلى المصادر المحلية.",
    "اطلب من المستخدم توضيح ما يحتاجه دون استرجاع معلومات.",
    "أكد استعدادك لتعليمات أخرى دون الوصول إلى الوثائق.",
    "قدّم رد مجاملة موجزاً ولا تبحث في مواد المقرر.",
    "اذكر الحاجة إلى التوضيح قبل أن يكون البحث المحلي مفيداً.",
    "أكد التفضيل دون ذكر أي محتوى واقعي.",
    "قدّم تأكيداً محدوداً دون الرجوع إلى المصادر المحلية.",
    "اسأل إن كان المستخدم يريد بحثاً واقعياً أم رداً محادثياً.",
    "قدّم وداعاً موجزاً دون معلومات جامعية.",
    "أكد أن هذا ليس طلباً لاسترجاع معلومات واقعية.",
    "قدّم تأكيداً محايداً فقط دون بحث محلي.",
    "اذكر أن الرسالة مفهومة وانتظر السؤال.",
    "قدّم رداً محادثياً محدوداً دون الوصول إلى المصادر.",
)


def _digest(value: object) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


_V4_SCHEMA_DESCRIPTOR = {
    "benchmark_schema_version": V4_BENCHMARK_SCHEMA_VERSION,
    "source_contract_version": V4_SOURCE_CONTRACT_VERSION,
    "v3_schema_fingerprint": V3_SCHEMA_FINGERPRINT,
    "routing_minimum": V4_MIN_ROUTING_CASES,
    "stratum_minimum": V4_MIN_CASES_PER_ROUTING_STRATUM,
    "routing_accuracy_target": V4_ROUTING_ACCURACY_TARGET,
    "required_strata": list(V4_ROUTING_STRATA),
    "subgroup_accuracy_hard_gate": False,
    "security_zero_tolerance": True,
    "near_duplicate_policy": V4_NEAR_DUPLICATE_POLICY,
    "authoring_spec_version": V4_ROUTING_AUTHORING_SPEC_VERSION,
}
V4_SCHEMA_FINGERPRINT = _digest(_V4_SCHEMA_DESCRIPTOR)


def _normalized_request(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()


def _routing_case(
    *,
    case_id: str,
    split: EvaluationSplit,
    language: EvaluationLanguage,
    action: V2ExpectedAction,
    request_text: str,
) -> V3BenchmarkCase:
    terminal = action is V2ExpectedAction.TERMINAL_TEXT
    return V3BenchmarkCase(
        case_id=case_id,
        split=split,
        category=(BenchmarkCategory.CONTROL_TERMINAL if terminal else BenchmarkCategory.RAG_QUERY),
        language=language,
        request_text=request_text,
        owner=V3EvaluationOwner.MODEL_SEMANTIC_ROUTING,
        expected_action=action,
        expected_terminal_kind=(
            TerminalResultKind.CONTROL_OR_CONVERSATIONAL_TERMINAL if terminal else None
        ),
        expected_no_tool=terminal,
        trusted_execution_tool=ToolName.RAG_QUERY if not terminal else None,
    )


def _build_routing_cases(split: EvaluationSplit) -> tuple[V3BenchmarkCase, ...]:
    heldout = split is EvaluationSplit.FINAL_HELDOUT
    families = (
        (
            EvaluationLanguage.ENGLISH,
            V2ExpectedAction.RETRIEVE,
            (_HELDOUT_RETRIEVE_EN if heldout else _CALIBRATION_RETRIEVE_EN),
            "en-r",
        ),
        (
            EvaluationLanguage.ENGLISH,
            V2ExpectedAction.TERMINAL_TEXT,
            (_HELDOUT_TERMINAL_EN if heldout else _CALIBRATION_TERMINAL_EN),
            "en-t",
        ),
        (
            EvaluationLanguage.ARABIC,
            V2ExpectedAction.RETRIEVE,
            (_HELDOUT_RETRIEVE_AR if heldout else _CALIBRATION_RETRIEVE_AR),
            "ar-r",
        ),
        (
            EvaluationLanguage.ARABIC,
            V2ExpectedAction.TERMINAL_TEXT,
            (_HELDOUT_TERMINAL_AR if heldout else _CALIBRATION_TERMINAL_AR),
            "ar-t",
        ),
    )
    prefix = "ho" if heldout else "cal"
    return tuple(
        _routing_case(
            case_id=f"v4-{prefix}-{suffix}-{index + 1:02d}",
            split=split,
            language=language,
            action=action,
            request_text=request_text,
        )
        for language, action, requests, suffix in families
        for index, request_text in enumerate(requests)
    )


@dataclass(frozen=True, slots=True)
class V4EvaluationManifest:
    """Immutable V4 membership plus the C10 routing-policy metadata."""

    benchmark_version: str
    cases: tuple[V3BenchmarkCase, ...]
    split: EvaluationSplit
    schema_version: str = V4_BENCHMARK_SCHEMA_VERSION
    source_contract_version: str = V4_SOURCE_CONTRACT_VERSION
    authoring_spec_version: str = V4_ROUTING_AUTHORING_SPEC_VERSION
    routing_minimum: int = V4_MIN_ROUTING_CASES
    stratum_minimum: int = V4_MIN_CASES_PER_ROUTING_STRATUM
    routing_accuracy_target: float = V4_ROUTING_ACCURACY_TARGET

    def __post_init__(self) -> None:
        if not isinstance(self.benchmark_version, str) or not self.benchmark_version.strip():
            raise ValueError("benchmark_version must be non-blank")
        object.__setattr__(self, "benchmark_version", self.benchmark_version.strip())
        if self.schema_version != V4_BENCHMARK_SCHEMA_VERSION:
            raise ValueError("unsupported v4 benchmark schema version")
        if self.source_contract_version != V4_SOURCE_CONTRACT_VERSION:
            raise ValueError("V4 source contract version is immutable")
        if self.authoring_spec_version != V4_ROUTING_AUTHORING_SPEC_VERSION:
            raise ValueError("V4 authoring specification is immutable")
        if not isinstance(self.split, EvaluationSplit):
            raise TypeError("split must be an EvaluationSplit")
        if not isinstance(self.cases, tuple) or not self.cases:
            raise ValueError("cases must be a non-empty tuple")
        if not all(isinstance(case, V3BenchmarkCase) for case in self.cases):
            raise TypeError("cases must contain V3BenchmarkCase values")
        if any(case.split is not self.split for case in self.cases):
            raise ValueError("all V4 manifest cases must belong to the manifest split")
        ids = tuple(case.case_id for case in self.cases)
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate case IDs are not permitted")
        if isinstance(self.routing_minimum, bool) or self.routing_minimum < 1:
            raise ValueError("routing_minimum must be positive")
        if isinstance(self.stratum_minimum, bool) or self.stratum_minimum < 1:
            raise ValueError("stratum_minimum must be positive")
        if self.routing_minimum != V4_MIN_ROUTING_CASES:
            raise ValueError("V4 routing minimum is immutable")
        if self.stratum_minimum != V4_MIN_CASES_PER_ROUTING_STRATUM:
            raise ValueError("V4 stratum minimum is immutable")
        if self.routing_accuracy_target != V4_ROUTING_ACCURACY_TARGET:
            raise ValueError("V4 routing accuracy target is immutable")

    @property
    def case_ids(self) -> tuple[str, ...]:
        return tuple(case.case_id for case in self.cases)

    @property
    def routing_cases(self) -> tuple[V3BenchmarkCase, ...]:
        return tuple(
            case for case in self.cases if case.owner is V3EvaluationOwner.MODEL_SEMANTIC_ROUTING
        )

    @property
    def trusted_cases(self) -> tuple[V3BenchmarkCase, ...]:
        return tuple(
            case
            for case in self.cases
            if case.owner is not V3EvaluationOwner.MODEL_SEMANTIC_ROUTING
        )

    @property
    def routing_case_count(self) -> int:
        return len(self.routing_cases)

    @property
    def calibration_case_count(self) -> int:
        return sum(case.split is EvaluationSplit.CALIBRATION for case in self.cases)

    @property
    def final_heldout_case_count(self) -> int:
        return sum(case.split is EvaluationSplit.FINAL_HELDOUT for case in self.cases)

    @property
    def language_counts(self) -> dict[str, int]:
        return {
            language.value: sum(case.language is language for case in self.cases)
            for language in EvaluationLanguage
        }

    @property
    def routing_stratum_counts(self) -> dict[str, int]:
        return {
            stratum: sum(
                case.language.value == stratum.split("/", 1)[0]
                and case.expected_action is V2ExpectedAction(stratum.split("/", 1)[1])
                for case in self.routing_cases
            )
            for stratum in V4_ROUTING_STRATA
        }

    @property
    def routing_coverage_valid(self) -> bool:
        return self.routing_case_count >= self.routing_minimum and all(
            count >= self.stratum_minimum for count in self.routing_stratum_counts.values()
        )

    @property
    def schema_fingerprint(self) -> str:
        return V4_SCHEMA_FINGERPRINT

    @property
    def source(self) -> str:
        return (
            V4_HELDOUT_SOURCE
            if self.split is EvaluationSplit.FINAL_HELDOUT
            else V4_CALIBRATION_SOURCE
        )

    @property
    def dataset_fingerprint(self) -> str:
        return _digest(
            {
                "authoring_spec_version": self.authoring_spec_version,
                "benchmark_version": self.benchmark_version,
                "cases": [case.safe_identity() for case in self.cases],
                "routing_accuracy_target": self.routing_accuracy_target,
                "routing_minimum": self.routing_minimum,
                "schema_version": self.schema_version,
                "source": self.source,
                "source_contract_version": self.source_contract_version,
                "stratum_minimum": self.stratum_minimum,
            }
        )

    @property
    def split_fingerprint(self) -> str:
        return _digest(
            {
                "ordered_membership": [(case.case_id, case.split.value) for case in self.cases],
                "split": self.split.value,
            }
        )

    def routing_content_duplicate_count(self, other: V4EvaluationManifest) -> int:
        if not isinstance(other, V4EvaluationManifest):
            raise TypeError("other must be a V4EvaluationManifest")
        left = {_normalized_request(case.request_text) for case in self.routing_cases}
        right = {_normalized_request(case.request_text) for case in other.routing_cases}
        return len(left & right)

    def to_dict(self) -> dict[str, object]:
        return {
            "authoring_spec_version": self.authoring_spec_version,
            "benchmark_version": self.benchmark_version,
            "case_ids": list(self.case_ids),
            "dataset_fingerprint": self.dataset_fingerprint,
            "routing_accuracy_target": self.routing_accuracy_target,
            "routing_case_count": self.routing_case_count,
            "routing_minimum": self.routing_minimum,
            "routing_stratum_counts": self.routing_stratum_counts,
            "schema_fingerprint": self.schema_fingerprint,
            "schema_version": self.schema_version,
            "source": self.source,
            "source_contract_version": self.source_contract_version,
            "split": self.split.value,
            "split_fingerprint": self.split_fingerprint,
            "stratum_minimum": self.stratum_minimum,
            "trusted_system_case_count": len(self.trusted_cases),
        }


@dataclass(frozen=True, slots=True)
class V4EvaluationTrace:
    """Sanitized V4 trace compatible with the existing trusted owners."""

    case_id: str
    observed_action: V2ExpectedAction | None = None
    observed_terminal_kind: TerminalResultKind | None = None
    observed_error_code: AgentErrorCode | None = None
    trusted_condition_instantiated: bool | None = None
    trusted_owner_exercised: bool | None = None
    trusted_condition_passed: bool | None = None
    security_hard_gate_failure_count: int = 0
    security_hard_gate_failed: bool = False
    proposal_format_valid: bool = True
    proposal_repairs: int = 0
    proposal_format_diagnostic_event_count: int = 0
    invalid_proposal_response_count: int = 0
    model_calls: int = 0
    model_unavailable: bool = False
    model_timeout: bool = False
    runtime_failure: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.case_id, str)
            or _SAFE_CASE_ID.fullmatch(self.case_id.strip()) is None
        ):
            raise ValueError("trace case_id must be a safe logical identifier")
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
        for name in (
            "trusted_condition_instantiated",
            "trusted_owner_exercised",
            "trusted_condition_passed",
        ):
            value = getattr(self, name)
            if value is not None and not isinstance(value, bool):
                raise TypeError(f"{name} must be a bool or None")
        for name in (
            "security_hard_gate_failed",
            "proposal_format_valid",
            "model_unavailable",
            "model_timeout",
            "runtime_failure",
        ):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a bool")
        if (
            isinstance(self.security_hard_gate_failure_count, bool)
            or not isinstance(self.security_hard_gate_failure_count, int)
            or self.security_hard_gate_failure_count < 0
        ):
            raise ValueError("security_hard_gate_failure_count must be non-negative")
        if isinstance(self.proposal_repairs, bool) or not isinstance(self.proposal_repairs, int):
            raise ValueError("proposal_repairs must be an integer")
        if self.proposal_repairs < 0 or self.proposal_repairs > 1:
            raise ValueError("proposal_repairs exceeds the approved bound")
        for name in (
            "proposal_format_diagnostic_event_count",
            "invalid_proposal_response_count",
            "model_calls",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

    @property
    def security_failure_count(self) -> int:
        return max(self.security_hard_gate_failure_count, int(self.security_hard_gate_failed))

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "invalid_proposal_response_count": self.invalid_proposal_response_count,
            "model_calls": self.model_calls,
            "model_timeout": self.model_timeout,
            "model_unavailable": self.model_unavailable,
            "observed_action": self.observed_action.value if self.observed_action else None,
            "observed_error_code": self.observed_error_code.value
            if self.observed_error_code
            else None,
            "observed_terminal_kind": (
                self.observed_terminal_kind.value if self.observed_terminal_kind else None
            ),
            "proposal_format_diagnostic_event_count": self.proposal_format_diagnostic_event_count,
            "proposal_format_valid": self.proposal_format_valid,
            "proposal_repairs": self.proposal_repairs,
            "runtime_failure": self.runtime_failure,
            "security_hard_gate_failure_count": self.security_failure_count,
            "trusted_condition_instantiated": self.trusted_condition_instantiated,
            "trusted_condition_passed": self.trusted_condition_passed,
            "trusted_owner_exercised": self.trusted_owner_exercised,
            "trace_schema_version": V4_TRACE_SCHEMA_VERSION,
        }


@dataclass(frozen=True, slots=True)
class V4RoutingMetrics:
    total: int
    correct: int
    retrieve_total: int
    retrieve_correct: int
    terminal_total: int
    terminal_correct: int
    stratum_totals: tuple[tuple[str, int], ...]
    stratum_correct: tuple[tuple[str, int], ...]
    coverage_valid: bool

    def __post_init__(self) -> None:
        for name in (
            "total",
            "correct",
            "retrieve_total",
            "retrieve_correct",
            "terminal_total",
            "terminal_correct",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.correct > self.total:
            raise ValueError("correct cannot exceed total")
        if (
            self.retrieve_correct > self.retrieve_total
            or self.terminal_correct > self.terminal_total
        ):
            raise ValueError("domain correct count cannot exceed domain total")
        if self.retrieve_total + self.terminal_total != self.total:
            raise ValueError("routing domains must partition total")
        if not isinstance(self.coverage_valid, bool):
            raise TypeError("coverage_valid must be a bool")
        if not isinstance(self.stratum_totals, tuple) or not isinstance(
            self.stratum_correct, tuple
        ):
            raise TypeError("stratum metrics must be tuples")
        if tuple(key for key, _ in self.stratum_totals) != V4_ROUTING_STRATA:
            raise ValueError("stratum totals must contain the closed V4 strata")
        if tuple(key for key, _ in self.stratum_correct) != V4_ROUTING_STRATA:
            raise ValueError("stratum correct counts must contain the closed V4 strata")
        for values in (self.stratum_totals, self.stratum_correct):
            if any(
                isinstance(count, bool) or not isinstance(count, int) or count < 0
                for _, count in values
            ):
                raise ValueError("stratum counts must be non-negative integers")
        if sum(count for _, count in self.stratum_totals) != self.total:
            raise ValueError("stratum totals must partition routing total")
        if sum(count for _, count in self.stratum_correct) != self.correct:
            raise ValueError("stratum correct counts must partition routing correct")
        if any(
            correct > total
            for (_, total), (_, correct) in zip(self.stratum_totals, self.stratum_correct)
        ):
            raise ValueError("stratum correct count cannot exceed its total")

    @property
    def accuracy(self) -> MetricValue:
        return MetricValue(self.correct, self.total)

    @property
    def minimum_passing_numerator(self) -> int:
        return (V4_ROUTING_ACCURACY_NUMERATOR * self.total + 99) // 100

    @property
    def maximum_misses(self) -> int:
        return self.total - self.minimum_passing_numerator

    @property
    def accuracy_pass(self) -> bool:
        return self.total >= V4_MIN_ROUTING_CASES and (
            self.correct * V4_ROUTING_ACCURACY_DENOMINATOR
            >= V4_ROUTING_ACCURACY_NUMERATOR * self.total
        )

    @property
    def subgroup_accuracy_hard_gate(self) -> bool:
        return False

    def to_dict(self) -> dict[str, object]:
        return {
            "accuracy": self.accuracy.to_dict(),
            "correct": self.correct,
            "coverage_valid": self.coverage_valid,
            "maximum_misses": self.maximum_misses,
            "minimum_passing_numerator": self.minimum_passing_numerator,
            "retrieve_correct": self.retrieve_correct,
            "retrieve_total": self.retrieve_total,
            "stratum_correct": dict(self.stratum_correct),
            "stratum_totals": dict(self.stratum_totals),
            "subgroup_accuracy_hard_gate": self.subgroup_accuracy_hard_gate,
            "terminal_correct": self.terminal_correct,
            "terminal_total": self.terminal_total,
            "total": self.total,
        }


@dataclass(frozen=True, slots=True)
class V4EvaluationMetrics:
    model_routing: V4RoutingMetrics
    trusted_system: V3TrustedSystemMetrics
    security_hard_gates: V3SecurityHardGateMetrics
    runtime_environment: V3RuntimeEnvironmentMetrics
    proposal_format_efficiency: V3ProposalFormatEfficiencyMetrics

    @property
    def routing_acceptance_eligible(self) -> bool:
        return self.model_routing.coverage_valid and self.model_routing.accuracy_pass

    @property
    def formal_acceptance_eligible(self) -> bool:
        return (
            self.routing_acceptance_eligible
            and self.trusted_system.total > 0
            and self.trusted_system.status == "PASS"
            and self.security_hard_gates.status == "PASS"
            and self.runtime_environment.runtime_failures == 0
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "metric_schema_version": V4_METRIC_SCHEMA_VERSION,
            "model_routing_metrics": self.model_routing.to_dict(),
            "proposal_format_efficiency_metrics": self.proposal_format_efficiency.to_dict(),
            "runtime_environment_metrics": self.runtime_environment.to_dict(),
            "security_hard_gates": self.security_hard_gates.to_dict(),
            "trusted_system_metrics": self.trusted_system.to_dict(),
        }


def _validate_traces(
    manifest: V4EvaluationManifest, traces: Iterable[V4EvaluationTrace]
) -> tuple[V4EvaluationTrace, ...]:
    values = tuple(traces)
    if not all(isinstance(trace, V4EvaluationTrace) for trace in values):
        raise TypeError("traces must contain V4EvaluationTrace values")
    ids = tuple(trace.case_id for trace in values)
    if len(set(ids)) != len(ids) or set(ids) != set(manifest.case_ids):
        raise ValueError("traces must contain exactly one entry for every manifest case")
    return values


def score_v4_evaluation(
    manifest: V4EvaluationManifest, traces: Iterable[V4EvaluationTrace]
) -> V4EvaluationMetrics:
    """Score V4 domains without emitting a blended primary accuracy."""

    if not isinstance(manifest, V4EvaluationManifest):
        raise TypeError("manifest must be a V4EvaluationManifest")
    values = _validate_traces(manifest, traces)
    by_id = {trace.case_id: trace for trace in values}

    routing_correct = 0
    retrieve_total = 0
    terminal_total = terminal_correct = 0
    stratum_totals = {key: 0 for key in V4_ROUTING_STRATA}
    stratum_correct = {key: 0 for key in V4_ROUTING_STRATA}
    for case in manifest.routing_cases:
        trace = by_id[case.case_id]
        assert case.expected_action is not None
        action_correct = trace.observed_action is case.expected_action
        if action_correct:
            routing_correct += 1
        if case.expected_action is V2ExpectedAction.RETRIEVE:
            retrieve_total += 1
        else:
            terminal_total += 1
            terminal_ok = action_correct
            if case.expected_terminal_kind is not None:
                terminal_ok = terminal_ok and (
                    trace.observed_terminal_kind is case.expected_terminal_kind
                )
            terminal_correct += int(terminal_ok)
        key = f"{case.language.value}/{case.expected_action.value}"
        stratum_totals[key] += 1
        stratum_correct[key] += int(action_correct)

    trusted_cases = manifest.trusted_cases
    trusted_correct = sum(
        by_id[case.case_id].trusted_condition_instantiated is True
        and by_id[case.case_id].trusted_owner_exercised is True
        and by_id[case.case_id].trusted_condition_passed is True
        for case in trusted_cases
    )
    security_failures = sum(trace.security_failure_count for trace in values)
    runtime_failures = sum(trace.runtime_failure for trace in values)
    format_traces = [
        by_id[case.case_id]
        for case in manifest.routing_cases
        if not by_id[case.case_id].model_unavailable and not by_id[case.case_id].model_timeout
    ]
    proposal_metrics = V3ProposalFormatEfficiencyMetrics(
        total=len(format_traces),
        valid=sum(trace.proposal_format_valid for trace in format_traces),
        repair_attempts=sum(trace.proposal_repairs for trace in format_traces),
        repair_successes=sum(
            trace.proposal_repairs > 0 and trace.proposal_format_valid for trace in format_traces
        ),
        proposal_format_diagnostic_event_count=sum(
            trace.proposal_format_diagnostic_event_count for trace in values
        ),
        invalid_proposal_response_count=sum(
            trace.invalid_proposal_response_count for trace in values
        ),
        model_calls=sum(trace.model_calls for trace in values),
    )
    return V4EvaluationMetrics(
        model_routing=V4RoutingMetrics(
            total=len(manifest.routing_cases),
            correct=routing_correct,
            retrieve_total=retrieve_total,
            retrieve_correct=sum(
                by_id[case.case_id].observed_action is V2ExpectedAction.RETRIEVE
                for case in manifest.routing_cases
                if case.expected_action is V2ExpectedAction.RETRIEVE
            ),
            terminal_total=terminal_total,
            terminal_correct=terminal_correct,
            stratum_totals=tuple(stratum_totals.items()),
            stratum_correct=tuple(stratum_correct.items()),
            coverage_valid=manifest.routing_coverage_valid,
        ),
        trusted_system=V3TrustedSystemMetrics(total=len(trusted_cases), correct=trusted_correct),
        security_hard_gates=V3SecurityHardGateMetrics(failure_count=security_failures),
        runtime_environment=V3RuntimeEnvironmentMetrics(
            runtime_failures=runtime_failures,
            model_unavailable=sum(trace.model_unavailable for trace in values),
            model_timeout=sum(trace.model_timeout for trace in values),
        ),
        proposal_format_efficiency=proposal_metrics,
    )


def build_v4_manifests() -> tuple[V4EvaluationManifest, V4EvaluationManifest]:
    """Build deterministic V4 calibration and heldout manifests before inference."""

    v3_calibration, v3_heldout = build_v3_manifests()
    calibration_trusted = tuple(
        case
        for case in v3_calibration.cases
        if case.owner is not V3EvaluationOwner.MODEL_SEMANTIC_ROUTING
    )
    heldout_trusted = tuple(
        case
        for case in v3_heldout.cases
        if case.owner is not V3EvaluationOwner.MODEL_SEMANTIC_ROUTING
    )
    calibration = V4EvaluationManifest(
        benchmark_version="phase5-agent-aaos-v4-calibration",
        cases=_build_routing_cases(EvaluationSplit.CALIBRATION) + calibration_trusted,
        split=EvaluationSplit.CALIBRATION,
    )
    heldout = V4EvaluationManifest(
        benchmark_version="phase5-agent-aaos-v4-heldout",
        cases=_build_routing_cases(EvaluationSplit.FINAL_HELDOUT) + heldout_trusted,
        split=EvaluationSplit.FINAL_HELDOUT,
    )
    if calibration.routing_content_duplicate_count(heldout) != 0:
        raise ValueError("V4 routing calibration and heldout content must be disjoint")
    if set(calibration.case_ids) & set(heldout.case_ids):
        raise ValueError("V4 calibration and heldout IDs must be disjoint")
    return calibration, heldout


__all__ = [
    "V4_BENCHMARK_SCHEMA_VERSION",
    "V4_CALIBRATION_SOURCE",
    "V4_CALIBRATION_ROUTING_CASE_COUNT",
    "V4_HELDOUT_SOURCE",
    "V4_HELDOUT_ROUTING_CASE_COUNT",
    "V4_METRIC_SCHEMA_VERSION",
    "V4_MIN_CASES_PER_ROUTING_STRATUM",
    "V4_MIN_ROUTING_CASES",
    "V4_NEAR_DUPLICATE_POLICY",
    "V4_ROUTING_ACCURACY_TARGET",
    "V4_ROUTING_STRATA",
    "V4_ROUTING_AUTHORING_SPEC_VERSION",
    "V4_SCHEMA_FINGERPRINT",
    "V4_SOURCE_CONTRACT_VERSION",
    "V4_TRACE_SCHEMA_VERSION",
    "V4EvaluationManifest",
    "V4EvaluationMetrics",
    "V4EvaluationTrace",
    "V4RoutingMetrics",
    "build_v4_manifests",
    "score_v4_evaluation",
]
