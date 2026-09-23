"""Trusted, bounded persistence contracts for Phase 8 visual evidence."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from aaos.persistence.document_models import ContentBlobORM, DocumentVersionORM
from aaos.persistence.visual_models import VisualAnalysisRunORM, VisualElementORM

MAX_VISUAL_TEXT_BYTES = 8_192
MAX_VISUAL_PAYLOAD_BYTES = 65_536
MAX_VISUAL_IDENTIFIER_BYTES = 128
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")


class VisualStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    UNAVAILABLE = "UNAVAILABLE"


class VisualKind(StrEnum):
    TABLE = "TABLE"
    EQUATION = "EQUATION"
    CHART = "CHART"
    DIAGRAM = "DIAGRAM"
    TEXT_REGION = "TEXT_REGION"
    OTHER = "OTHER"


class VisualPersistenceError(RuntimeError):
    """Safe persistence-boundary failure without provider/database details."""


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _positive(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be positive")
    return value


def _digest(value: object, field_name: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be lowercase SHA-256")
    return value


def _identifier(value: object, field_name: str, *, model: bool = False) -> str:
    pattern = SAFE_IDENTIFIER_RE if model else re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a safe bounded identifier")
    if len(value.encode("utf-8")) > MAX_VISUAL_IDENTIFIER_BYTES:
        raise ValueError(f"{field_name} exceeds the identifier bound")
    return value


def _bounded_text(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-blank when present")
    if len(value.encode("utf-8")) > MAX_VISUAL_TEXT_BYTES:
        raise ValueError(f"{field_name} exceeds the visual text bound")
    return value


def _parse_payload(kind: VisualKind, payload: str | None) -> str | None:
    if payload is None:
        return None
    if not isinstance(payload, str) or len(payload.encode("utf-8")) > MAX_VISUAL_PAYLOAD_BYTES:
        raise ValueError("structured_payload exceeds the visual payload bound")
    try:
        value = json.loads(payload, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("structured_payload must be valid JSON") from error
    if not isinstance(value, dict) or set(value) != {"observations", "interpretations"}:
        raise ValueError("structured_payload fields are closed")
    observations = value["observations"]
    interpretations = value["interpretations"]
    if not isinstance(observations, dict) or (
        interpretations is not None and not isinstance(interpretations, dict)
    ):
        raise ValueError("structured_payload sections are invalid")
    if kind is VisualKind.TEXT_REGION and set(observations) != {"text"}:
        raise ValueError("TEXT_REGION observations are invalid")
    canonical = _canonical_json(value)
    if len(canonical.encode("utf-8")) > MAX_VISUAL_PAYLOAD_BYTES:
        raise ValueError("structured_payload exceeds the visual payload bound")
    return canonical


@dataclass(frozen=True, slots=True)
class VisualElement:
    kind: VisualKind
    observed_text: str | None
    interpretation_text: str | None
    structured_payload: str | None
    confidence: float | None
    payload_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        kind = self.kind if isinstance(self.kind, VisualKind) else VisualKind(self.kind)
        object.__setattr__(self, "kind", kind)
        observed = _bounded_text(self.observed_text, "observed_text")
        interpretation = _bounded_text(self.interpretation_text, "interpretation_text")
        payload = _parse_payload(kind, self.structured_payload)
        if observed is None and interpretation is None and payload is None:
            raise ValueError("visual element must contain a bounded representation")
        if self.confidence is not None and (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not math.isfinite(float(self.confidence))
            or not 0 <= float(self.confidence) <= 1
        ):
            raise ValueError("confidence must be finite and between zero and one")
        object.__setattr__(self, "observed_text", observed)
        object.__setattr__(self, "interpretation_text", interpretation)
        object.__setattr__(self, "structured_payload", payload)
        envelope = {
            "kind": kind.value,
            "observed_text": observed,
            "interpretation_text": interpretation,
            "structured_payload": json.loads(payload) if payload is not None else None,
            "confidence": self.confidence,
        }
        object.__setattr__(self, "payload_sha256", _hash(_canonical_json(envelope).encode("utf-8")))


@dataclass(frozen=True, slots=True)
class VisualAnalysis:
    document_version_id: int
    page_number: int
    source_content_sha256: str
    render_sha256: str
    model_id: str
    model_revision: str
    prompt_revision: str
    selector_revision: str
    renderer_revision: str
    analysis_schema_revision: str
    status: VisualStatus
    elements: tuple[VisualElement, ...]
    attempt_number: int = 1
    analysis_sha256: str | None = None
    analysis_key: str = field(init=False)

    def __post_init__(self) -> None:
        _positive(self.document_version_id, "document_version_id")
        _positive(self.page_number, "page_number")
        _positive(self.attempt_number, "attempt_number")
        _digest(self.source_content_sha256, "source_content_sha256")
        _digest(self.render_sha256, "render_sha256")
        status = self.status if isinstance(self.status, VisualStatus) else VisualStatus(self.status)
        object.__setattr__(self, "status", status)
        _identifier(self.model_id, "model_id", model=True)
        for name in (
            "model_revision",
            "prompt_revision",
            "selector_revision",
            "renderer_revision",
            "analysis_schema_revision",
        ):
            _identifier(getattr(self, name), name)
        if not isinstance(self.elements, tuple) or not all(
            isinstance(item, VisualElement) for item in self.elements
        ):
            raise TypeError("elements must be a tuple of VisualElement")
        if status is VisualStatus.PARTIAL and not self.elements:
            raise ValueError("PARTIAL analysis requires at least one visual element")
        if (
            status in {VisualStatus.FAILED, VisualStatus.TIMED_OUT, VisualStatus.UNAVAILABLE}
            and self.elements
        ):
            raise ValueError("failure analysis cannot contain visual elements")
        identity = {
            "analysis_schema_revision": self.analysis_schema_revision,
            "document_version_id": self.document_version_id,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "page_number": self.page_number,
            "prompt_revision": self.prompt_revision,
            "render_sha256": self.render_sha256,
            "renderer_revision": self.renderer_revision,
            "selector_revision": self.selector_revision,
            "source_content_sha256": self.source_content_sha256,
        }
        key = _hash(_canonical_json(identity).encode("utf-8"))
        object.__setattr__(self, "analysis_key", key)
        if status in {VisualStatus.SUCCEEDED, VisualStatus.PARTIAL}:
            representation = {
                "analysis_key": key,
                "elements": [item.payload_sha256 for item in self.elements],
                "status": status.value,
            }
            expected = _hash(_canonical_json(representation).encode("utf-8"))
            if self.analysis_sha256 is not None and self.analysis_sha256 != expected:
                raise ValueError("analysis_sha256 does not match canonical analysis")
            object.__setattr__(self, "analysis_sha256", expected)
        elif self.analysis_sha256 is not None:
            raise ValueError("failure analysis must not carry analysis_sha256")


@dataclass(frozen=True, slots=True)
class VisualCitationProjection:
    """Derived visual provenance, intentionally not a text source span."""

    document_version_id: int
    page_number: int
    analysis_run_id: int
    visual_element_id: int
    source_content_sha256: str
    render_sha256: str
    model_id: str
    model_revision: str
    prompt_revision: str
    analysis_schema_revision: str
    derived_text: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class VisualProvenanceRepository:
    """Persist immutable visual attempts through one trusted repository boundary."""

    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    def save_analysis(self, analysis: VisualAnalysis, *, explicit_rerun: bool = False) -> int:
        if not isinstance(analysis, VisualAnalysis):
            raise TypeError("analysis must be a VisualAnalysis")
        session = self._session_factory()
        try:
            with session.begin():
                version = session.scalar(
                    select(DocumentVersionORM)
                    .join(ContentBlobORM, ContentBlobORM.id == DocumentVersionORM.content_blob_id)
                    .where(DocumentVersionORM.id == analysis.document_version_id)
                )
                if version is None:
                    raise VisualPersistenceError("document version is unavailable")
                if version.provenance_mode != "PAGE" or version.page_count is None:
                    raise VisualPersistenceError("document version is not page-provenanced")
                if analysis.page_number > version.page_count:
                    raise VisualPersistenceError("page is outside the document version")
                blob = session.scalar(
                    select(ContentBlobORM).where(ContentBlobORM.id == version.content_blob_id)
                )
                if blob is None or blob.sha256 != analysis.source_content_sha256:
                    raise VisualPersistenceError("source content binding is invalid")
                existing = tuple(
                    session.scalars(
                        select(VisualAnalysisRunORM)
                        .where(VisualAnalysisRunORM.analysis_key == analysis.analysis_key)
                        .order_by(VisualAnalysisRunORM.attempt_number)
                    ).all()
                )
                succeeded = next(
                    (row for row in existing if row.status == VisualStatus.SUCCEEDED.value), None
                )
                if succeeded is not None:
                    return int(succeeded.id)
                if existing and not explicit_rerun:
                    return int(existing[-1].id)
                next_attempt = max((int(row.attempt_number) for row in existing), default=0) + 1
                if analysis.attempt_number != next_attempt:
                    raise VisualPersistenceError("attempt number is not the next trusted attempt")
                row = VisualAnalysisRunORM(
                    analysis_key=analysis.analysis_key,
                    attempt_number=next_attempt,
                    document_version_id=analysis.document_version_id,
                    page_number=analysis.page_number,
                    source_content_sha256=analysis.source_content_sha256,
                    render_sha256=analysis.render_sha256,
                    model_id=analysis.model_id,
                    model_revision=analysis.model_revision,
                    prompt_revision=analysis.prompt_revision,
                    selector_revision=analysis.selector_revision,
                    renderer_revision=analysis.renderer_revision,
                    analysis_schema_revision=analysis.analysis_schema_revision,
                    status=analysis.status.value,
                    analysis_sha256=analysis.analysis_sha256,
                    created_at=_utc_now(),
                )
                session.add(row)
                session.flush()
                for sequence, element in enumerate(analysis.elements, start=1):
                    session.add(
                        VisualElementORM(
                            analysis_run_id=int(row.id),
                            sequence=sequence,
                            visual_kind=element.kind.value,
                            observed_text=element.observed_text,
                            interpretation_text=element.interpretation_text,
                            structured_payload=element.structured_payload,
                            payload_sha256=element.payload_sha256,
                            confidence=element.confidence,
                            created_at=row.created_at,
                        )
                    )
                session.flush()
                return int(row.id)
        except VisualPersistenceError:
            raise
        except IntegrityError as error:
            raise VisualPersistenceError("visual attempt persistence conflicted") from error
        except SQLAlchemyError as error:
            raise VisualPersistenceError("visual provenance persistence failed") from error
        finally:
            session.close()

    def get_analysis(
        self, document_version_id: int, page_number: int, source_content_sha256: str
    ) -> VisualAnalysis | None:
        _positive(document_version_id, "document_version_id")
        _positive(page_number, "page_number")
        _digest(source_content_sha256, "source_content_sha256")
        session = self._session_factory()
        try:
            with session.begin():
                rows = tuple(
                    session.scalars(
                        select(VisualAnalysisRunORM)
                        .where(
                            VisualAnalysisRunORM.document_version_id == document_version_id,
                            VisualAnalysisRunORM.page_number == page_number,
                            VisualAnalysisRunORM.source_content_sha256 == source_content_sha256,
                        )
                        .order_by(
                            VisualAnalysisRunORM.status == VisualStatus.SUCCEEDED.value,
                            VisualAnalysisRunORM.status == VisualStatus.PARTIAL.value,
                            VisualAnalysisRunORM.attempt_number,
                        )
                    ).all()
                )
                if not rows:
                    return None
                row = rows[-1]
                elements = tuple(
                    VisualElement(
                        kind=VisualKind(element.visual_kind),
                        observed_text=element.observed_text,
                        interpretation_text=element.interpretation_text,
                        structured_payload=element.structured_payload,
                        confidence=element.confidence,
                    )
                    for element in session.scalars(
                        select(VisualElementORM)
                        .where(VisualElementORM.analysis_run_id == row.id)
                        .order_by(VisualElementORM.sequence)
                    ).all()
                )
                return VisualAnalysis(
                    document_version_id=int(row.document_version_id),
                    page_number=int(row.page_number),
                    source_content_sha256=row.source_content_sha256,
                    render_sha256=row.render_sha256,
                    model_id=row.model_id,
                    model_revision=row.model_revision,
                    prompt_revision=row.prompt_revision,
                    selector_revision=row.selector_revision,
                    renderer_revision=row.renderer_revision,
                    analysis_schema_revision=row.analysis_schema_revision,
                    status=VisualStatus(row.status),
                    elements=elements,
                    attempt_number=int(row.attempt_number),
                    analysis_sha256=row.analysis_sha256,
                )
        except VisualPersistenceError:
            raise
        except SQLAlchemyError as error:
            raise VisualPersistenceError("visual provenance read failed") from error
        finally:
            session.close()


__all__ = [
    "VisualAnalysis",
    "VisualCitationProjection",
    "VisualElement",
    "VisualKind",
    "VisualPersistenceError",
    "VisualProvenanceRepository",
    "VisualStatus",
]
