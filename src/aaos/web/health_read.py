"""Passive, local-only System Health probes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Mapping, Protocol

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from aaos.persistence.rag_models import RagDocumentIndexORM, RagIndexGenerationORM
from aaos.rag.contracts import DocumentIndexStatus, GenerationStatus
from aaos.web.health import (
    HealthComponent,
    HealthComponentName,
    HealthDetailCode,
    HealthStatus,
    SystemHealthResponse,
)

RagCoverage = Literal["FULL", "PARTIAL", "NONE"]


@dataclass(frozen=True, slots=True)
class RagIndexHealthSnapshot:
    configured: bool
    active: bool
    coverage: RagCoverage
    failed: bool


class PassiveRagIndexReader(Protocol):
    def snapshot(self) -> RagIndexHealthSnapshot: ...


class AcademicHealthReader(Protocol):
    def list_courses(self) -> tuple[object, ...]: ...

    def list_assignments(self, course_id: int | None = None) -> tuple[object, ...]: ...


class SqlitePassiveRagIndexReader:
    """Read persisted RAG generation/index state without opening the runtime."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def snapshot(self) -> RagIndexHealthSnapshot:
        with self._sessions() as session:
            table_names = set(inspect(session.get_bind()).get_table_names())
            required = {"rag_index_generations", "rag_document_indexes"}
            if not required.issubset(table_names):
                return RagIndexHealthSnapshot(
                    configured=False,
                    active=False,
                    coverage="NONE",
                    failed=False,
                )
            generation = session.scalar(
                select(RagIndexGenerationORM)
                .where(RagIndexGenerationORM.status == GenerationStatus.ACTIVE.value)
                .order_by(RagIndexGenerationORM.id)
            )
            if generation is None:
                return RagIndexHealthSnapshot(
                    configured=True,
                    active=False,
                    coverage="NONE",
                    failed=False,
                )
            indexes = session.scalars(
                select(RagDocumentIndexORM).where(
                    RagDocumentIndexORM.generation_id == generation.id
                )
            ).all()
            failed = any(index.status == DocumentIndexStatus.FAILED.value for index in indexes)
            if not indexes:
                coverage: RagCoverage = "NONE"
            elif failed or any(index.coverage_status == "NONE" for index in indexes):
                coverage = "NONE"
            elif all(
                index.status == DocumentIndexStatus.READY.value and index.coverage_status == "FULL"
                for index in indexes
            ):
                coverage = "FULL"
            else:
                coverage = "PARTIAL"
            return RagIndexHealthSnapshot(
                configured=True,
                active=True,
                coverage=coverage,
                failed=failed,
            )


class _SchemaNotReady(RuntimeError):
    pass


class SystemHealthService:
    """Aggregate bounded local read evidence into the browser health contract."""

    _REQUIRED_TABLES = frozenset({"alembic_version", "courses", "assignments"})
    _OPTIONAL_COMPONENTS = (
        HealthComponentName.ACADEMIC_SYNC,
        HealthComponentName.DOCUMENT_PIPELINE,
    )
    _TRAILING_OPTIONAL_COMPONENTS = (
        HealthComponentName.INFERENCE,
        HealthComponentName.GOOGLE_CALENDAR,
    )

    def __init__(
        self,
        sessions: sessionmaker[Session],
        academic_reader: AcademicHealthReader,
        rag_reader: PassiveRagIndexReader,
        *,
        registered_components: Mapping[HealthComponentName, bool] | None = None,
    ) -> None:
        self._sessions = sessions
        self._academic_reader = academic_reader
        self._rag_reader = rag_reader
        self._registered_components = dict(registered_components or {})

    @staticmethod
    def _component(
        name: HealthComponentName,
        status: HealthStatus,
        required: bool,
        detail_code: HealthDetailCode,
    ) -> HealthComponent:
        return HealthComponent(
            name=name,
            status=status,
            required=required,
            detail_code=detail_code,
        )

    def _database(self) -> HealthComponent:
        try:
            with self._sessions() as session:
                session.execute(text("SELECT 1")).scalar_one()
                tables = set(inspect(session.get_bind()).get_table_names())
                if not self._REQUIRED_TABLES.issubset(tables):
                    raise _SchemaNotReady
        except _SchemaNotReady:
            return self._component(
                HealthComponentName.DATABASE,
                HealthStatus.UNAVAILABLE,
                True,
                HealthDetailCode.SCHEMA_NOT_READY,
            )
        except Exception:
            return self._component(
                HealthComponentName.DATABASE,
                HealthStatus.UNAVAILABLE,
                True,
                HealthDetailCode.DATABASE_UNAVAILABLE,
            )
        return self._component(
            HealthComponentName.DATABASE,
            HealthStatus.HEALTHY,
            True,
            HealthDetailCode.RAG_INDEX_READY,
        )

    def _academic(self, database: HealthComponent) -> HealthComponent:
        if database.status is not HealthStatus.HEALTHY:
            return self._component(
                HealthComponentName.ACADEMIC_DATA,
                HealthStatus.UNAVAILABLE,
                True,
                HealthDetailCode.ACADEMIC_READ_UNAVAILABLE,
            )
        try:
            self._academic_reader.list_courses()
            self._academic_reader.list_assignments()
        except Exception:
            return self._component(
                HealthComponentName.ACADEMIC_DATA,
                HealthStatus.UNAVAILABLE,
                True,
                HealthDetailCode.ACADEMIC_READ_UNAVAILABLE,
            )
        return self._component(
            HealthComponentName.ACADEMIC_DATA,
            HealthStatus.HEALTHY,
            True,
            HealthDetailCode.RAG_INDEX_READY,
        )

    def _rag(self) -> HealthComponent:
        try:
            snapshot = self._rag_reader.snapshot()
            if not snapshot.configured or not snapshot.active:
                return self._component(
                    HealthComponentName.RAG_INDEX,
                    HealthStatus.NOT_CONFIGURED,
                    False,
                    HealthDetailCode.RAG_INDEX_NOT_CONFIGURED,
                )
            if snapshot.failed or snapshot.coverage in {"PARTIAL", "NONE"}:
                return self._component(
                    HealthComponentName.RAG_INDEX,
                    HealthStatus.DEGRADED,
                    False,
                    HealthDetailCode.RAG_INDEX_PARTIAL,
                )
            return self._component(
                HealthComponentName.RAG_INDEX,
                HealthStatus.HEALTHY,
                False,
                HealthDetailCode.RAG_INDEX_READY,
            )
        except Exception:
            return self._component(
                HealthComponentName.RAG_INDEX,
                HealthStatus.DEGRADED,
                False,
                HealthDetailCode.INTERNAL_STATUS_UNAVAILABLE,
            )

    def _optional(self, name: HealthComponentName) -> HealthComponent:
        if not self._registered_components.get(name, False):
            return self._component(
                name,
                HealthStatus.NOT_CONFIGURED,
                False,
                HealthDetailCode.PASSIVE_STATUS_UNAVAILABLE,
            )
        return self._component(
            name,
            HealthStatus.UNKNOWN,
            False,
            HealthDetailCode.PASSIVE_STATUS_UNAVAILABLE,
        )

    def check(self, *, now: datetime) -> SystemHealthResponse:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        checked_at = now.astimezone(timezone.utc)
        database = self._database()
        academic = self._academic(database)
        components = (
            database,
            academic,
            *(self._optional(name) for name in self._OPTIONAL_COMPONENTS),
            self._rag(),
            *(self._optional(name) for name in self._TRAILING_OPTIONAL_COMPONENTS),
        )
        if (
            database.status is not HealthStatus.HEALTHY
            or academic.status is not HealthStatus.HEALTHY
        ):
            overall = HealthStatus.UNAVAILABLE
        elif any(
            component.status
            in {HealthStatus.DEGRADED, HealthStatus.UNAVAILABLE, HealthStatus.UNKNOWN}
            for component in components
            if not component.required
        ):
            overall = HealthStatus.DEGRADED
        else:
            overall = HealthStatus.HEALTHY
        return SystemHealthResponse(
            schema_version="phase7-health-v1",
            checked_at=checked_at,
            overall=overall,
            components=components,
        )


__all__ = [
    "AcademicHealthReader",
    "PassiveRagIndexReader",
    "RagIndexHealthSnapshot",
    "SqlitePassiveRagIndexReader",
    "SystemHealthService",
]
