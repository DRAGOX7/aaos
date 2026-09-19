from __future__ import annotations

from pathlib import Path

from scripts.phase9.release_manifest import (
    EXPECTED_PHASE8_TARGET,
    collect_runtime_facts,
    verify_phase8_freeze,
)


def test_frozen_repository_has_expected_phase8_target() -> None:
    root = Path(__file__).resolve().parents[2]
    verify_phase8_freeze(repo_root=root, expected_target=EXPECTED_PHASE8_TARGET)


def test_runtime_facts_are_classified_without_secret_values() -> None:
    facts = dict(collect_runtime_facts())
    assert facts["python"].startswith("3.")
    assert facts["uv"]
    assert facts["node"]
    assert facts["npm"]
    assert facts["docker"]
    assert "TOKEN" not in " ".join(facts)
