from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from aaos.persistence.academic_models import Base
from aaos.web.health import (
    HealthComponent,
    HealthComponentName,
    HealthDetailCode,
    HealthStatus,
    SystemHealthResponse,
)
from aaos.web.health_read import (
    AcademicHealthReader,
    RagCoverage,
    RagIndexHealthSnapshot,
    SystemHealthService,
)


class FakeAcademicReader:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def list_courses(self) -> tuple[object, ...]:
        if self.fail:
            raise RuntimeError("private academic failure")
        return ()

    def list_assignments(self, course_id: int | None = None) -> tuple[object, ...]:
        del course_id
        if self.fail:
            raise RuntimeError("private academic failure")
        return ()


class FakeRagReader:
    def __init__(self, snapshot: RagIndexHealthSnapshot) -> None:
        self.snapshot_value = snapshot
        self.calls = 0

    def snapshot(self) -> RagIndexHealthSnapshot:
        self.calls += 1
        return self.snapshot_value


def _sessions(*, with_schema: bool = True) -> sessionmaker[Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    if with_schema:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(64))"))
    return sessions


def _service(
    *,
    sessions: sessionmaker[Session] | None = None,
    academic: AcademicHealthReader | None = None,
    snapshot: RagIndexHealthSnapshot | None = None,
    registered: Mapping[HealthComponentName, bool] | None = None,
) -> SystemHealthService:
    return SystemHealthService(
        sessions or _sessions(),
        academic or FakeAcademicReader(),
        FakeRagReader(
            snapshot
            or RagIndexHealthSnapshot(
                configured=False,
                active=False,
                coverage="NONE",
                failed=False,
            )
        ),
        registered_components=registered,
    )


def _component(response: SystemHealthResponse, name: HealthComponentName) -> HealthComponent:
    return next(component for component in response.components if component.name is name)


def test_system_health_reports_healthy_empty_local_database() -> None:
    response = _service().check(now=datetime(2026, 9, 18, tzinfo=timezone.utc))

    assert response.overall is HealthStatus.HEALTHY
    assert _component(response, HealthComponentName.DATABASE).status is HealthStatus.HEALTHY
    assert _component(response, HealthComponentName.ACADEMIC_DATA).status is HealthStatus.HEALTHY
    assert _component(response, HealthComponentName.RAG_INDEX).status is HealthStatus.NOT_CONFIGURED


def test_system_health_maps_database_unavailable_without_leaking_exception(
    tmp_path: Path,
) -> None:
    engine = create_engine(
        URL.create("sqlite", database=str(tmp_path / "missing" / "unavailable.db"))
    )
    try:
        response = _service(sessions=sessionmaker(bind=engine)).check(
            now=datetime(2026, 9, 18, tzinfo=timezone.utc)
        )
    finally:
        engine.dispose()

    database = _component(response, HealthComponentName.DATABASE)
    assert response.overall is HealthStatus.UNAVAILABLE
    assert database.status is HealthStatus.UNAVAILABLE
    assert database.detail_code is HealthDetailCode.DATABASE_UNAVAILABLE
    assert "private" not in response.model_dump_json()


def test_system_health_maps_schema_not_ready() -> None:
    response = _service(sessions=_sessions(with_schema=False)).check(
        now=datetime(2026, 9, 18, tzinfo=timezone.utc)
    )

    database = _component(response, HealthComponentName.DATABASE)
    assert response.overall is HealthStatus.UNAVAILABLE
    assert database.status is HealthStatus.UNAVAILABLE
    assert database.detail_code is HealthDetailCode.SCHEMA_NOT_READY


def test_system_health_maps_academic_failure_without_raw_exception() -> None:
    response = _service(academic=FakeAcademicReader(fail=True)).check(
        now=datetime(2026, 9, 18, tzinfo=timezone.utc)
    )

    academic = _component(response, HealthComponentName.ACADEMIC_DATA)
    assert response.overall is HealthStatus.UNAVAILABLE
    assert academic.status is HealthStatus.UNAVAILABLE
    assert academic.detail_code is HealthDetailCode.ACADEMIC_READ_UNAVAILABLE
    assert "private" not in response.model_dump_json()


def test_system_health_maps_persisted_rag_coverage_without_runtime_calls() -> None:
    coverage_cases: tuple[tuple[RagCoverage, HealthStatus], ...] = (
        ("FULL", HealthStatus.HEALTHY),
        ("PARTIAL", HealthStatus.DEGRADED),
        ("NONE", HealthStatus.DEGRADED),
    )
    for coverage, expected in coverage_cases:
        rag = FakeRagReader(
            RagIndexHealthSnapshot(
                configured=True,
                active=True,
                coverage=coverage,
                failed=coverage == "NONE",
            )
        )
        service = SystemHealthService(
            _sessions(),
            FakeAcademicReader(),
            rag,
        )

        response = service.check(now=datetime(2026, 9, 18, tzinfo=timezone.utc))

        assert _component(response, HealthComponentName.RAG_INDEX).status is expected
        assert rag.calls == 1


def test_system_health_optional_registered_components_are_truthfully_unknown() -> None:
    response = _service(
        registered={HealthComponentName.INFERENCE: True},
    ).check(now=datetime(2026, 9, 18, tzinfo=timezone.utc))

    inference = _component(response, HealthComponentName.INFERENCE)
    calendar = _component(response, HealthComponentName.GOOGLE_CALENDAR)
    assert inference.status is HealthStatus.UNKNOWN
    assert inference.detail_code is HealthDetailCode.PASSIVE_STATUS_UNAVAILABLE
    assert calendar.status is HealthStatus.NOT_CONFIGURED


def test_healthy_components_report_their_own_ready_detail() -> None:
    """Regression: a healthy database and academic data reported "RAG index is ready"."""
    response = _service().check(now=datetime(2026, 10, 2, tzinfo=timezone.utc))

    database = _component(response, HealthComponentName.DATABASE)
    academic = _component(response, HealthComponentName.ACADEMIC_DATA)
    assert database.detail_code is HealthDetailCode.DATABASE_READY
    assert academic.detail_code is HealthDetailCode.ACADEMIC_DATA_READY
