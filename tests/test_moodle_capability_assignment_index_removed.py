from __future__ import annotations

from typing import get_args

from aaos.moodle.models import BrowserRoute


def test_capability_browser_routes_exclude_obsolete_assignment_list() -> None:
    assert "assignment_list" not in get_args(BrowserRoute)
