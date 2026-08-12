"""FastAPI application foundation for AAOS."""

from __future__ import annotations

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel

from aaos.config import DEFAULT_SETTINGS, AppSettings


class HealthResponse(BaseModel):
    """Stable response contract for process-level health checks."""

    status: Literal["ok"]
    service: str
    version: str


def create_app(settings: AppSettings = DEFAULT_SETTINGS) -> FastAPI:
    """Create the AAOS API using explicit, non-secret configuration."""
    application = FastAPI(title=settings.application_name, version=settings.application_version)

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
