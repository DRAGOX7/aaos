"""RED tests for deterministic, no-real-write Phase 7E acceptance."""

from __future__ import annotations

import sys


def test_phase7e_acceptance_does_not_import_real_provider_or_model_modules() -> None:
    forbidden = {
        "google.auth",
        "googleapiclient.discovery",
        "lmstudio",
        "transformers",
    }
    assert forbidden.isdisjoint(sys.modules)
