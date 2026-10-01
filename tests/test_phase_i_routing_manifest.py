"""The Phase I bilingual routing manifest is frozen before the router exists."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

MANIFEST = Path(__file__).parent / "evaluation" / "phase_i_routing_cases_v1.json"
FROZEN_CASE_SET_SHA256 = "251e9a6a427058ce8650ba33a053674c9587bd6356d9c01a4211cf12c727c345"
INTENTS = {
    "DOCUMENT_QUESTION",
    "DOCUMENT_SUMMARY",
    "COURSE_SUMMARY",
    "ANNOUNCEMENTS",
    "MOODLE_CHANGES",
    "COURSE_FACTS",
    "ASSIGNMENT_FACTS",
    "UNROUTED",
}


def load_routing_cases() -> list[dict[str, str]]:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cases: list[dict[str, str]] = payload["cases"]
    return cases


def test_routing_manifest_case_set_is_frozen() -> None:
    canonical = json.dumps(
        load_routing_cases(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")

    assert hashlib.sha256(canonical).hexdigest() == FROZEN_CASE_SET_SHA256


def test_routing_manifest_is_balanced_and_closed() -> None:
    cases = load_routing_cases()
    counts = Counter((case["expected_intent"], case["language"]) for case in cases)

    assert len({case["id"] for case in cases}) == len(cases) == 128
    assert {case["expected_intent"] for case in cases} == INTENTS
    assert all(counts[(intent, language)] == 8 for intent in INTENTS for language in ("en", "ar"))
    assert all(set(case) == {"id", "language", "question", "expected_intent"} for case in cases)
