from __future__ import annotations

from typing import cast

import pytest

from aaos.moodle.browser_models import BrowserPolicyError, BrowserRoute
from aaos.moodle.browser_navigation import PlaywrightRestrictedNavigator
from aaos.moodle.browser_policy import build_browser_url, classify_browser_request

_ORIGIN = "https://moodle.example.edu"


def test_obsolete_assignment_index_capability_is_not_authorized() -> None:
    obsolete_route = cast(BrowserRoute, "ASSIGNMENT_LIST")

    with pytest.raises(BrowserPolicyError):
        build_browser_url(
            obsolete_route,
            base_origin=_ORIGIN,
            identifier=42,
        )

    decision = classify_browser_request(
        "GET",
        _ORIGIN + "/current/mod/assign/index.php?id=42",
        base_origin=_ORIGIN,
        canonical_wwwroot=_ORIGIN + "/current",
        resource_type="document",
        has_body=False,
    )
    assert decision.code == "BLOCK"

    assert not hasattr(PlaywrightRestrictedNavigator, "open_assignment_list")
