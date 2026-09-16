"""FastAPI application foundation for AAOS."""

from __future__ import annotations

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from aaos.config import DEFAULT_SETTINGS, AppSettings
from aaos.persistence.database import (
    create_database_engine,
    create_session_factory,
)
from aaos.web.errors import install_error_boundary
from aaos.web.routes import create_api_v1_router


class HealthResponse(BaseModel):
    """Stable response contract for process-level health checks."""

    status: Literal["ok"]
    service: str
    version: str


def create_app(
    settings: AppSettings = DEFAULT_SETTINGS,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> FastAPI:
    """Create the AAOS API using explicit, non-secret configuration."""
    application = FastAPI(
        title=settings.application_name,
        version=settings.application_version,
    )
    install_error_boundary(application)

    if session_factory is None:
        engine = create_database_engine()
        session_factory = create_session_factory(engine)
        application.state.phase7_database_engine = engine
        application.router.add_event_handler("shutdown", engine.dispose)

    application.include_router(create_api_v1_router(session_factory))

    @application.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """Report that the AAOS API process is alive."""
        return HealthResponse(
            status="ok",
            service=settings.application_name,
            version=settings.application_version,
        )

    return application


app = create_app()
