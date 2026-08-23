"""Safe Microsoft Edge capability check for the restricted browser connector."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
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


class _OwnedPersistentContext:
    """Close only the persistent context and the Playwright manager that created it."""

    def __init__(self, context: object, playwright: object) -> None:
        self._context = context
        self._playwright = playwright
        self._closed = False

    def __getattr__(self, name: str) -> object:
        return getattr(self._context, name)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            getattr(self._context, "close")()
        finally:
            getattr(self._playwright, "stop")()


def _start_playwright() -> object:
    """Import Playwright only when an explicit browser operation requests it."""
    from playwright.sync_api import sync_playwright

    return sync_playwright().start()


def launch_msedge_persistent_context(
    *,
    channel: Literal["msedge"],
    headless: bool,
    user_data_dir: Path | str,
    accept_downloads: bool,
    service_workers: Literal["block"],
    start: Callable[[], object] = _start_playwright,
) -> _OwnedPersistentContext:
    """Launch only a caller-confined Edge profile; no browser page is created here."""
    if channel != "msedge" or headless or accept_downloads or service_workers != "block":
        raise ValueError("restricted browser runtime unavailable")
    playwright = start()
    try:
        chromium = getattr(playwright, "chromium")
        context = chromium.launch_persistent_context(
            str(user_data_dir),
            channel="msedge",
            headless=False,
            accept_downloads=False,
            service_workers="block",
        )
    except (AttributeError, OSError, TypeError, ValueError):
        try:
            getattr(playwright, "stop")()
        except (AttributeError, OSError, TypeError, ValueError):
            pass
        raise OSError("restricted browser runtime unavailable") from None
    return _OwnedPersistentContext(context, playwright)
