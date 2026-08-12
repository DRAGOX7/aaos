"""Non-secret application configuration for the AAOS foundation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AppSettings:
    """Basic process metadata with safe local-development defaults."""

    application_name: str = "aaos"
    application_version: str = "0.1.0"
    environment_name: str = "development"


DEFAULT_SETTINGS = AppSettings()
