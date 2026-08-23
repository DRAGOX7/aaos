"""Bounded stdin/stdout bridge to the purpose-built WebView2 helper."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import cast

from aaos.moodle.bootstrap import helper_process_timeout_seconds
from aaos.moodle.embedded_auth import EmbeddedAuthResult

_ALLOWED_RESULTS = {"CALLBACK_DISPATCHED", "USER_CANCELLED", "TIMED_OUT", "HOST_ERROR"}


class NativeWebView2Runtime:
    """Run the native helper without putting sensitive launch data in argv."""

    def __init__(self, helper_path: Path) -> None:
        if not helper_path.is_absolute():
            raise ValueError("WebView2 helper path must be absolute")
        self._helper_path = helper_path
        self.cleanup_pass = True
        self.pre_initialization_failure = False

    def run(self, launch_url: str, profile: Path, timeout_seconds: int) -> EmbeddedAuthResult:
        self.pre_initialization_failure = False
        payload = json.dumps(
            {
                "launchUrl": launch_url,
                "profilePath": str(profile),
                "timeoutSeconds": timeout_seconds,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            completed = subprocess.run(
                [str(self._helper_path)],
                input=payload,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=helper_process_timeout_seconds(timeout_seconds),
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            self.cleanup_pass = False
            return "HOST_ERROR"
        returncode = getattr(completed, "returncode", 0)
        self.cleanup_pass = returncode == 0
        self.pre_initialization_failure = returncode == 2
        try:
            result = completed.stdout.decode("ascii").strip()
        except UnicodeDecodeError:
            return "HOST_ERROR"
        return cast(EmbeddedAuthResult, result) if result in _ALLOWED_RESULTS else "HOST_ERROR"
