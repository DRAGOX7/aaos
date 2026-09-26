from __future__ import annotations

import hashlib

from phase9.security_corpus import (
    PHASE9_SECURITY_CORPUS,
    canonical_corpus_bytes,
    corpus_sha256,
)


def test_corpus_is_deterministic_balanced_and_secret_free() -> None:
    assert PHASE9_SECURITY_CORPUS
    assert len(PHASE9_SECURITY_CORPUS) == 18
    assert {item.language for item in PHASE9_SECURITY_CORPUS} == {"EN", "AR", "NONE"}
    assert {item.channel for item in PHASE9_SECURITY_CORPUS} == {
        "TEXT",
        "VISUAL",
        "RETRIEVED",
        "TOOL_OBSERVATION",
        "COMPUTATION",
    }
    serialized = canonical_corpus_bytes(PHASE9_SECURITY_CORPUS)
    assert corpus_sha256(PHASE9_SECURITY_CORPUS) == hashlib.sha256(serialized).hexdigest()
    assert "real-secret" not in serialized.decode("utf-8")
    assert all(item.expected == "DATA_ONLY" for item in PHASE9_SECURITY_CORPUS)


def test_fixture_ids_and_payloads_are_unique_and_bounded() -> None:
    ids = [item.fixture_id for item in PHASE9_SECURITY_CORPUS]
    assert len(ids) == len(set(ids))
    assert all(len(item.payload.encode("utf-8")) <= 512 for item in PHASE9_SECURITY_CORPUS)
