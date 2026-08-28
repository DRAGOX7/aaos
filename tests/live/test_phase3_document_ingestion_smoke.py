"""Human-authorized, default-skipped Phase 3 live-ingestion boundary."""

from __future__ import annotations

import os
import re

import pytest

_RUN_LIVE = "AAOS_RUN_LIVE_PHASE3_SMOKE"
_RESOURCE_CMID = "AAOS_PHASE3_RESOURCE_CMID"
_POSITIVE_DECIMAL = re.compile(r"[1-9][0-9]*\Z", re.ASCII)


def test_phase3_document_ingestion_smoke_is_explicitly_opt_in() -> None:
    """Keep real Moodle ingestion out of normal and accidental test runs."""
    run_live = os.environ.get(_RUN_LIVE)
    resource_cmid = os.environ.get(_RESOURCE_CMID)
    if run_live != "1" or _POSITIVE_DECIMAL.fullmatch(resource_cmid or "") is None:
        pytest.skip(
            "set AAOS_RUN_LIVE_PHASE3_SMOKE=1 and a positive "
            "AAOS_PHASE3_RESOURCE_CMID for the human-authorized live gate"
        )
    pytest.fail(
        "the live Phase 3 acceptance runner is created and executed only after "
        "explicit human authorization"
    )
