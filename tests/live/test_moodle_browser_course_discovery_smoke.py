"""Human-authorized, default-skipped COURSE_INDEX-only browser smoke boundary."""

from __future__ import annotations

import os

import pytest


def test_browser_course_discovery_smoke_is_explicit_opt_in() -> None:
    if os.environ.get("AAOS_RUN_LIVE_MOODLE_BROWSER_SMOKE") != "1":
        pytest.skip("set AAOS_RUN_LIVE_MOODLE_BROWSER_SMOKE=1 for human-authorized smoke")
    pytest.fail("live Moodle browser smoke requires separate human execution authorization")
