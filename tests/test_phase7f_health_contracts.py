from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from aaos.web.health import (
    HealthComponent,
    HealthComponentName,
    HealthDetailCode,
    HealthStatus,
    SystemHealthResponse,
)


def test_health_contract_is_closed_and_immutable() -> None:
    response = SystemHealthResponse(
        schema_version="phase7-health-v1",
        checked_at=datetime(2026, 9, 18, tzinfo=timezone.utc),
        overall=HealthStatus.HEALTHY,
        components=(
            HealthComponent(
                name=HealthComponentName.DATABASE,
                status=HealthStatus.HEALTHY,
                required=True,
                detail_code=HealthDetailCode.RAG_INDEX_READY,
            ),
        ),
    )
    assert isinstance(response.components, tuple)
    with pytest.raises(ValidationError):
        response.components = ()
    with pytest.raises(ValidationError):
        HealthComponent.model_validate(
            {
                "name": HealthComponentName.DATABASE,
                "status": HealthStatus.HEALTHY,
                "required": True,
                "detail_code": HealthDetailCode.RAG_INDEX_READY,
                "database_url": "secret",
            }
        )


def test_health_enums_reject_unknown_values() -> None:
    with pytest.raises(ValueError):
        HealthStatus("GREEN_WITH_LATENCY")
    with pytest.raises(ValueError):
        HealthComponentName("CPU")
    with pytest.raises(ValueError):
        HealthDetailCode("RAW_EXCEPTION")
