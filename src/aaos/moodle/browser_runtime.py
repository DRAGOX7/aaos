"""Safe Microsoft Edge capability check for the restricted browser connector."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class EdgeCapability:
    channel: Literal["msedge"]
    available: bool
    version: str | None
    code: Literal["READY", "EDGE_UNAVAILABLE", "EDGE_POLICY_BLOCKED"]


class EdgeRuntime:
    """Inspect an injected Edge launcher without opening a page or profile."""

    def __init__(self, inspect: Callable[[], str]) -> None:
        self._inspect = inspect

    def inspect_msedge(self) -> EdgeCapability:
        try:
            version = self._inspect()
        except PermissionError:
            return EdgeCapability("msedge", False, None, "EDGE_POLICY_BLOCKED")
        except OSError:
            return EdgeCapability("msedge", False, None, "EDGE_UNAVAILABLE")
        if not isinstance(version, str) or not version.strip():
            return EdgeCapability("msedge", False, None, "EDGE_UNAVAILABLE")
        return EdgeCapability("msedge", True, version.strip(), "READY")
