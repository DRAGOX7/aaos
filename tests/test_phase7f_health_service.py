from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from aaos.persistence.academic_models import Base
from aaos.web.health import HealthComponentName, HealthDetailCode, HealthStatus
from aaos.web.health_read import (
    RagIndexHealthSnapshot,
    SystemHealthService,
)


class FakeAcademicReader:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def list_courses(self):
        if self.fail:
            raise RuntimeError("private academic failure")
        return ()

    def list_assignments(self, course_id=None):
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


def _sessions(*, with_schema: bool = True):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    if with_schema:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(64))"))
    return sessions


def _service(*, sessions=None, academic=None, snapshot=None, registered=None):
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


def _component(response, name: HealthComponentName):
    return next(component for component in response.components if component.name is name)


def test_system_health_reports_healthy_empty_local_database() -> None:
    response = _service().check(now=datetime(2026, 9, 18, tzinfo=timezone.utc))

    assert response.overall is HealthStatus.HEALTHY
    assert _component(response, HealthComponentName.DATABASE).status is HealthStatus.HEALTHY
    assert _component(response, HealthComponentName.ACADEMIC_DATA).status is HealthStatus.HEALTHY
    assert _component(response, HealthComponentName.RAG_INDEX).status is HealthStatus.NOT_CONFIGURED


def test_system_health_maps_database_unavailable_without_leaking_exception() -> None:
    def unavailable_sessions():
        raise RuntimeError("sqlite:///private/path")

    response = _service(sessions=unavailable_sessions).check(
        now=datetime(2026, 9, 18, tzinfo=timezone.utc)
    )

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
    for coverage, expected in (
        ("FULL", HealthStatus.HEALTHY),
        ("PARTIAL", HealthStatus.DEGRADED),
        ("NONE", HealthStatus.DEGRADED),
    ):
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
