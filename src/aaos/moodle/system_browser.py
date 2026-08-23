"""Narrow Windows system-browser launcher for one Moodle mobile login URL."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

from aaos.moodle.embedded_auth import build_embedded_launch_url


class SystemBrowserLaunchError(RuntimeError):
    """Raised with a fixed message when the normal browser cannot be launched."""

    def __init__(self) -> None:
        super().__init__("System browser authentication launch failed")


class SystemBrowserAuthHost:
    """Launch one validated Moodle mobile URL without controlling browser state."""

    def __init__(self, opener: Callable[[str], None] | None = None) -> None:
        self._opener = opener or _open_with_windows_shell

    def start(self, launch_url: str, passport: str) -> None:
        """Launch the fixed URL once through the user's default Windows browser."""
        fixed_url = build_embedded_launch_url(launch_url, passport)
        try:
            self._opener(fixed_url)
        except OSError:
            raise SystemBrowserLaunchError() from None


def _open_with_windows_shell(url: str) -> None:
    """Delegate one already-validated URL to the current user's normal browser."""
    if sys.platform != "win32":
        raise SystemBrowserLaunchError()
    os.startfile(url)
