"""Task 16 deterministic evidence selection and sufficiency tests."""

from __future__ import annotations

from dataclasses import replace

import pytest

from aaos.rag.config import RagIndexConfig
from aaos.rag.contracts import (
    CourseScope,
    GlobalScope,
    RagErrorCode,
    RagGenerationIdentity,
    RagOperationalError,
    RetrievedEvidence,
)
from aaos.rag.repository import (
    AuthoritativeRetrievalChunk,
    ChunkRecord,
    DocumentVersionRecord,
    QuerySnapshot,
    RagRepository,
    SourceSpanRecord,
)
from aaos.rag.retrieval import (
    EvidenceSelectionConfig,
    EvidenceSelector,
    EvidenceSufficiencyGate,
    FusedCandidate,
    SufficiencyStatus,
)

GENERATION = RagGenerationIdentity(3, "a" * 64, 7, 1024)
SNAPSHOT = QuerySnapshot(
    generation=GENERATION,
    scope=GlobalScope(),
    document_version_ids=(101, 102, 103, 104, 105),
    normalized_query="deadline",
)


def _authority(
    key: str,
    *,
    version_id: int,
    course_id: int,
    ordinal: int,
    text: str,
    token_count: int,
    start: int,
    end: int,
    title: str = "source.pdf",
    document_id: int | None = None,
) -> AuthoritativeRetrievalChunk:
    chunk_id = ordinal + version_id * 10
    chunk = ChunkRecord(
        chunk_id=chunk_id,
        chunk_key=key,
        generation_id=GENERATION.generation_id,
        document_version_id=version_id,
        ordinal=ordinal,
        heading=None,
        exact_text=text,
        token_count=token_count,
        text_sha256="b" * 64,
        provenance_mode="PAGE",
        page_start=1,
        page_end=1,
    )
    span = SourceSpanRecord(
        source_span_id=chunk_id,
        rag_chunk_id=chunk_id,
        document_page_id=version_id,
        ordinal=1,
        page_number=1,
        page_char_start=start,
        page_char_end=end,
        chunk_char_start=0,
        chunk_char_end=len(text),
    )
    version = DocumentVersionRecord(
        document_version_id=version_id,
        document_id=version_id if document_id is None else document_id,
        remote_file_id=version_id,
        course_id=course_id,
        version_number=1,
        content_blob_id=version_id,
        content_sha256="c" * 64,
        source_filename=title,
        provenance_mode="PAGE",
        page_count=1,
    )
    return AuthoritativeRetrievalChunk(chunk, version, (span,))


def _candidate(key: str, rank: int) -> FusedCandidate:
    return FusedCandidate(
        chunk_key=key,
        dense_rank=rank,
        lexical_rank=None,
        lexical_document_version_id=None,
        fused_score=1.0 / rank,
        fused_rank=rank,
    )


def _lexical_candidate(key: str, fused_rank: int, lexical_rank: int) -> FusedCandidate:
    return FusedCandidate(
        chunk_key=key,
        dense_rank=None,
        lexical_rank=lexical_rank,
        lexical_document_version_id=101,
        fused_score=1.0 / fused_rank,
        fused_rank=fused_rank,
    )


class _MemoryRepository(RagRepository):
    """Test-only stand-in for repository-owned authoritative materialization."""

    def __init__(self, authorities: tuple[AuthoritativeRetrievalChunk, ...]) -> None:
        self._authorities = {item.chunk.chunk_key: item for item in authorities}

    def materialize_snapshot_chunks(
        self, snapshot: QuerySnapshot, chunk_keys: tuple[str, ...]
    ) -> tuple[AuthoritativeRetrievalChunk, ...]:
        return tuple(self._authorities[key] for key in chunk_keys)


def _select(
    authorities: tuple[AuthoritativeRetrievalChunk, ...],
    candidates: tuple[FusedCandidate, ...],
    config: EvidenceSelectionConfig | None = None,
) -> tuple[RetrievedEvidence, ...]:
    return EvidenceSelector(config, repository=_MemoryRepository(authorities)).select(
        SNAPSHOT, candidates
    )


def _selected() -> tuple[RetrievedEvidence, ...]:
    authorities = (
        _authority(
            "a",
            version_id=101,
            course_id=11,
            ordinal=1,
            text="alpha one",
            token_count=2,
            start=0,
            end=10,
        ),
        _authority(
            "b",
            version_id=101,
            course_id=11,
            ordinal=2,
            text="alpha one repeated",
            token_count=3,
            start=2,
            end=19,
        ),
        _authority(
            "c",
            version_id=102,
            course_id=12,
            ordinal=1,
            text="beta two",
            token_count=2,
            start=0,
            end=8,
        ),
        _authority(
            "d",
            version_id=103,
            course_id=13,
            ordinal=1,
            text="gamma three",
            token_count=2,
            start=0,
            end=11,
        ),
    )
    candidates = tuple(_candidate(key, index) for index, key in enumerate(("a", "b", "c", "d"), 1))
    return _select(authorities, candidates, EvidenceSelectionConfig(max_chunks=4, max_tokens=7))


def test_selector_deduplicates_overlap_preserves_diversity_and_budgets() -> None:
    selected = _selected()

    assert tuple(item.chunk_key for item in selected) == ("a", "c", "d")
    assert {item.document_version_id for item in selected} == {101, 102, 103}
    assert sum(item.token_count for item in selected) <= 7


def test_selector_returns_authoritative_provenance_and_pinned_identity() -> None:
    selected = _selected()

    assert all(item.generation_id == GENERATION.generation_id for item in selected)
    assert all(item.content_revision == GENERATION.content_revision for item in selected)
    assert all(item.source_spans for item in selected)
    assert all(item.exact_text for item in selected)


def test_selector_ids_are_request_local_and_not_chunk_keys() -> None:
    first = _selected()
    second = _selected()

    assert [item.evidence_id for item in first] != [item.evidence_id for item in second]
    assert all(item.evidence_id != item.chunk_key for item in first)


def test_selector_retains_conflicting_valid_sources() -> None:
    authorities = (
        _authority(
            "left",
            version_id=101,
            course_id=11,
            ordinal=1,
            text="deadline Friday",
            token_count=2,
            start=0,
            end=15,
        ),
        _authority(
            "right",
            version_id=102,
            course_id=12,
            ordinal=1,
            text="deadline Monday",
            token_count=2,
            start=0,
            end=14,
        ),
    )
    candidates = (_candidate("left", 1), _candidate("right", 2))

    selected = _select(authorities, candidates, EvidenceSelectionConfig(max_chunks=4, max_tokens=8))

    assert tuple(item.chunk_key for item in selected) == ("left", "right")


def test_selector_replaces_a_single_document_monopoly_when_budget_allows() -> None:
    authorities = tuple(
        _authority(
            key,
            version_id=101,
            course_id=11,
            ordinal=index,
            text=f"same source {index}",
            token_count=1,
            start=index * 10,
            end=index * 10 + 14,
        )
        for index, key in enumerate(("a", "b", "c", "d"), 1)
    ) + (
        _authority(
            "other",
            version_id=102,
            course_id=12,
            ordinal=1,
            text="other source",
            token_count=1,
            start=0,
            end=12,
        ),
    )
    candidates = tuple(
        _candidate(key, index) for index, key in enumerate(("a", "b", "c", "d", "other"), 1)
    )

    selected = _select(authorities, candidates, EvidenceSelectionConfig(max_chunks=4, max_tokens=8))

    assert "other" in tuple(item.chunk_key for item in selected)
    assert tuple(item.fused_rank for item in selected) == tuple(
        sorted(item.fused_rank for item in selected)
    )


def test_selector_rescues_best_lexical_rank_two_candidate_into_final_slot() -> None:
    authorities = tuple(
        _authority(
            f"candidate-{ordinal}",
            version_id=101,
            course_id=11,
            ordinal=ordinal,
            text=f"candidate {ordinal}",
            token_count=1,
            start=ordinal * 10,
            end=ordinal * 10 + 11,
        )
        for ordinal in (1, 3, 5, 7, 9, 11)
    ) + (
        _authority(
            "lexical-rescue",
            version_id=101,
            course_id=11,
            ordinal=13,
            text="lexical rescue",
            token_count=1,
            start=130,
            end=143,
        ),
    )
    candidates = tuple(
        _candidate(f"candidate-{ordinal}", rank)
        for rank, ordinal in enumerate((1, 3, 5, 7, 9, 11), 1)
    ) + (_lexical_candidate("lexical-rescue", 7, 2),)

    selected = _select(
        authorities, candidates, EvidenceSelectionConfig(max_chunks=6, max_tokens=20)
    )

    assert tuple(item.chunk_key for item in selected) == (
        "candidate-1",
        "candidate-3",
        "candidate-5",
        "candidate-7",
        "candidate-9",
        "lexical-rescue",
    )
    assert len(selected) == 6


def test_selector_lexical_rescue_ties_use_lexical_then_fused_then_key_order() -> None:
    authorities = tuple(
        _authority(
            key,
            version_id=101,
            course_id=11,
            ordinal=ordinal,
            text=key,
            token_count=1,
            start=ordinal * 10,
            end=ordinal * 10 + len(key),
        )
        for ordinal, key in enumerate(
            (
                "candidate-1",
                "candidate-3",
                "candidate-5",
                "candidate-7",
                "candidate-9",
                "candidate-11",
            ),
            1,
        )
    ) + (
        _authority(
            "rescue-b",
            version_id=101,
            course_id=11,
            ordinal=13,
            text="rescue b",
            token_count=1,
            start=130,
            end=138,
        ),
        _authority(
            "rescue-a",
            version_id=101,
            course_id=11,
            ordinal=15,
            text="rescue a",
            token_count=1,
            start=150,
            end=158,
        ),
    )
    candidates = tuple(
        _candidate(key, rank)
        for rank, key in enumerate(
            (
                "candidate-1",
                "candidate-3",
                "candidate-5",
                "candidate-7",
                "candidate-9",
                "candidate-11",
            ),
            1,
        )
    ) + (
        _lexical_candidate("rescue-b", 7, 1),
        _lexical_candidate("rescue-a", 8, 1),
    )

    selected = _select(
        authorities, candidates, EvidenceSelectionConfig(max_chunks=6, max_tokens=20)
    )

    assert selected[-1].chunk_key == "rescue-b"
    assert len({item.chunk_key for item in selected}) == 6


def test_selector_lexical_rescue_does_not_duplicate_retained_prefix_candidate() -> None:
    keys = tuple(f"candidate-{ordinal}" for ordinal in (1, 3, 5, 7, 9, 11))
    authorities = tuple(
        _authority(
            key,
            version_id=101,
            course_id=11,
            ordinal=index,
            text=key,
            token_count=1,
            start=index * 10,
            end=index * 10 + len(key),
        )
        for index, key in enumerate(keys, 1)
    )
    candidates = (_lexical_candidate("candidate-1", 1, 1),) + tuple(
        _candidate(key, rank) for rank, key in enumerate(keys[1:], 2)
    )

    selected = _select(
        authorities,
        candidates,
        EvidenceSelectionConfig(max_chunks=6, max_tokens=20),
    )

    assert tuple(item.chunk_key for item in selected) == keys
    assert len({item.chunk_key for item in selected}) == 6


def test_selector_without_eligible_lexical_rescue_preserves_normal_selection() -> None:
    authorities = tuple(
        _authority(
            key,
            version_id=101,
            course_id=11,
            ordinal=ordinal,
            text=key,
            token_count=1,
            start=ordinal * 10,
            end=ordinal * 10 + len(key),
        )
        for ordinal, key in enumerate(
            (
                "candidate-1",
                "candidate-3",
                "candidate-5",
                "candidate-7",
                "candidate-9",
                "candidate-11",
            ),
            1,
        )
    ) + (
        _authority(
            "not-rescue",
            version_id=101,
            course_id=11,
            ordinal=13,
            text="not rescue",
            token_count=1,
            start=130,
            end=140,
        ),
    )
    candidates = tuple(
        _candidate(key, rank)
        for rank, key in enumerate(
            (
                "candidate-1",
                "candidate-3",
                "candidate-5",
                "candidate-7",
                "candidate-9",
                "candidate-11",
            ),
            1,
        )
    ) + (_lexical_candidate("not-rescue", 7, 3),)

    selected = _select(
        authorities, candidates, EvidenceSelectionConfig(max_chunks=6, max_tokens=20)
    )

    assert tuple(item.chunk_key for item in selected) == (
        "candidate-1",
        "candidate-3",
        "candidate-5",
        "candidate-7",
        "candidate-9",
        "candidate-11",
    )


def test_selector_lexical_rescue_respects_existing_token_guard() -> None:
    authorities = tuple(
        _authority(
            key,
            version_id=101,
            course_id=11,
            ordinal=ordinal,
            text=key,
            token_count=1,
            start=ordinal * 10,
            end=ordinal * 10 + len(key),
        )
        for ordinal, key in enumerate(
            (
                "candidate-1",
                "candidate-3",
                "candidate-5",
                "candidate-7",
                "candidate-9",
                "candidate-11",
            ),
            1,
        )
    ) + (
        _authority(
            "too-large-rescue",
            version_id=101,
            course_id=11,
            ordinal=13,
            text="too large rescue",
            token_count=2,
            start=130,
            end=146,
        ),
    )
    candidates = tuple(
        _candidate(key, rank)
        for rank, key in enumerate(
            (
                "candidate-1",
                "candidate-3",
                "candidate-5",
                "candidate-7",
                "candidate-9",
                "candidate-11",
            ),
            1,
        )
    ) + (_lexical_candidate("too-large-rescue", 7, 2),)

    selected = _select(authorities, candidates, EvidenceSelectionConfig(max_chunks=6, max_tokens=6))

    assert selected[-1].chunk_key == "candidate-11"


def test_selector_lexical_rescue_uses_final_slot_for_bounds_below_six() -> None:
    authorities = tuple(
        _authority(
            f"candidate-{ordinal}",
            version_id=101,
            course_id=11,
            ordinal=ordinal,
            text=f"candidate {ordinal}",
            token_count=1,
            start=ordinal * 10,
            end=ordinal * 10 + 11,
        )
        for ordinal in (1, 3, 5, 7)
    ) + (
        _authority(
            "small-bound-rescue",
            version_id=101,
            course_id=11,
            ordinal=9,
            text="small bound rescue",
            token_count=1,
            start=90,
            end=108,
        ),
    )
    candidates = tuple(
        _candidate(f"candidate-{ordinal}", rank) for rank, ordinal in enumerate((1, 3, 5, 7), 1)
    ) + (_lexical_candidate("small-bound-rescue", 5, 2),)

    selected = _select(
        authorities, candidates, EvidenceSelectionConfig(max_chunks=4, max_tokens=20)
    )

    assert tuple(item.chunk_key for item in selected) == (
        "candidate-1",
        "candidate-3",
        "candidate-5",
        "small-bound-rescue",
    )


def test_selector_deduplicates_same_version_text_but_keeps_other_versions() -> None:
    authorities = (
        _authority(
            "one",
            version_id=101,
            course_id=11,
            ordinal=1,
            text="same",
            token_count=1,
            start=0,
            end=4,
        ),
        _authority(
            "two",
            version_id=101,
            course_id=11,
            ordinal=3,
            text="same",
            token_count=1,
            start=20,
            end=24,
        ),
        _authority(
            "three",
            version_id=102,
            course_id=12,
            ordinal=1,
            text="same",
            token_count=1,
            start=0,
            end=4,
        ),
    )
    candidates = tuple(
        _candidate(key, index) for index, key in enumerate(("one", "two", "three"), 1)
    )

    selected = _select(authorities, candidates, EvidenceSelectionConfig(max_chunks=4, max_tokens=8))

    assert tuple(item.chunk_key for item in selected) == ("one", "three")


def test_selector_rejects_unverified_or_mismatched_authority() -> None:
    authority = _authority(
        "known",
        version_id=101,
        course_id=11,
        ordinal=1,
        text="known",
        token_count=1,
        start=0,
        end=5,
    )
    with pytest.raises(RagOperationalError) as error:
        _select(
            (authority,),
            (_candidate("unknown", 1),),
            EvidenceSelectionConfig(max_chunks=4, max_tokens=8),
        )
    assert error.value.code is RagErrorCode.RETRIEVAL_INCONSISTENCY


def test_selector_rejects_course_scope_leak() -> None:
    course_snapshot = replace(SNAPSHOT, scope=CourseScope(11))
    authority = _authority(
        "leak", version_id=102, course_id=12, ordinal=1, text="leak", token_count=1, start=0, end=4
    )

    with pytest.raises(RagOperationalError) as error:
        EvidenceSelector(
            EvidenceSelectionConfig(max_chunks=4, max_tokens=8),
            repository=_MemoryRepository((authority,)),
        ).select(course_snapshot, (_candidate("leak", 1),))
    assert error.value.code is RagErrorCode.RETRIEVAL_INCONSISTENCY


@pytest.mark.parametrize("max_chunks", [3, 9])
def test_selection_chunk_target_is_bounded_to_four_through_eight(max_chunks: int) -> None:
    with pytest.raises(ValueError, match="max_chunks"):
        EvidenceSelectionConfig(max_chunks=max_chunks, max_tokens=100)


def test_sufficiency_gate_is_conservative_and_versioned() -> None:
    evidence = _selected()
    gate = EvidenceSufficiencyGate(policy_version="conservative-v1", min_chunks=2, min_tokens=4)

    decision = gate.evaluate(evidence, snapshot=SNAPSHOT)

    assert decision.status is SufficiencyStatus.SUPPORTED
    assert decision.policy_version == "conservative-v1"
    assert decision.evidence_count == len(evidence)
    assert decision.token_count == sum(item.token_count for item in evidence)


def test_sufficiency_gate_distinguishes_partial_and_insufficient() -> None:
    evidence = _selected()
    gate = EvidenceSufficiencyGate(policy_version="conservative-v1", min_chunks=2, min_tokens=4)

    partial = gate.evaluate(evidence[:1], snapshot=SNAPSHOT)
    insufficient = gate.evaluate((), snapshot=SNAPSHOT)

    assert partial.status is SufficiencyStatus.PARTIALLY_SUPPORTED
    assert insufficient.status is SufficiencyStatus.UNSUPPORTED


def test_default_sufficiency_policy_does_not_accept_tiny_evidence() -> None:
    evidence = _selected()[:2]
    decision = EvidenceSufficiencyGate().evaluate(evidence, snapshot=SNAPSHOT)
    assert decision.status is SufficiencyStatus.PARTIALLY_SUPPORTED


def test_sufficiency_gate_rejects_false_acceptance_from_invalid_input() -> None:
    gate = EvidenceSufficiencyGate(policy_version="conservative-v1", min_chunks=2, min_tokens=1)

    with pytest.raises(RagOperationalError) as error:
        gate.evaluate((object(),), snapshot=SNAPSHOT)  # type: ignore[arg-type]
    assert error.value.code is RagErrorCode.RETRIEVAL_INCONSISTENCY


def test_sufficiency_gate_rejects_duplicate_request_capabilities() -> None:
    evidence = _selected()
    duplicate = replace(evidence[1], evidence_id=evidence[0].evidence_id)
    gate = EvidenceSufficiencyGate(policy_version="conservative-v1", min_chunks=2, min_tokens=1)

    with pytest.raises(RagOperationalError) as error:
        gate.evaluate((evidence[0], duplicate), snapshot=SNAPSHOT)
    assert error.value.code is RagErrorCode.RETRIEVAL_INCONSISTENCY


def test_selector_cannot_accept_caller_supplied_authority_override() -> None:
    authority = _authority(
        "known",
        version_id=101,
        course_id=11,
        ordinal=1,
        text="known",
        token_count=1,
        start=0,
        end=5,
    )
    selector = EvidenceSelector(
        EvidenceSelectionConfig(max_chunks=4, max_tokens=8),
        repository=_MemoryRepository((authority,)),
    )
    with pytest.raises(TypeError):
        selector.select(SNAPSHOT, (_candidate("known", 1),), (authority,))  # type: ignore[call-arg]


def test_selector_rejects_normalized_duplicate_evidence_ids() -> None:
    authorities = (
        _authority(
            "one",
            version_id=101,
            course_id=11,
            ordinal=1,
            text="one",
            token_count=1,
            start=0,
            end=3,
        ),
        _authority(
            "two",
            version_id=102,
            course_id=12,
            ordinal=1,
            text="two",
            token_count=1,
            start=0,
            end=3,
        ),
    )
    calls = iter(("id", " id "))
    selector = EvidenceSelector(
        EvidenceSelectionConfig(max_chunks=4, max_tokens=8),
        repository=_MemoryRepository(authorities),
        evidence_id_factory=lambda: next(calls),
    )
    with pytest.raises(RagOperationalError) as error:
        selector.select(SNAPSHOT, (_candidate("one", 1), _candidate("two", 2)))
    assert error.value.code is RagErrorCode.RETRIEVAL_INCONSISTENCY


def test_selector_translates_evidence_id_factory_failure() -> None:
    authority = _authority(
        "known",
        version_id=101,
        course_id=11,
        ordinal=1,
        text="known",
        token_count=1,
        start=0,
        end=5,
    )

    def raise_factory() -> str:
        raise RuntimeError("secret")

    selector = EvidenceSelector(
        EvidenceSelectionConfig(max_chunks=4, max_tokens=8),
        repository=_MemoryRepository((authority,)),
        evidence_id_factory=raise_factory,
    )
    with pytest.raises(RagOperationalError) as error:
        selector.select(SNAPSHOT, (_candidate("known", 1),))
    assert error.value.code is RagErrorCode.RETRIEVAL_INCONSISTENCY


def test_selector_diversity_uses_logical_document_not_version() -> None:
    authorities = tuple(
        _authority(
            key,
            version_id=version_id,
            course_id=11,
            ordinal=ordinal,
            text=f"version {ordinal}",
            token_count=1,
            start=ordinal * 10,
            end=ordinal * 10 + 10,
            document_id=500,
        )
        for ordinal, (key, version_id) in enumerate(
            (("first", 101), ("second", 102), ("third", 103), ("fourth", 104)), 1
        )
    ) + (
        _authority(
            "other",
            version_id=105,
            course_id=12,
            ordinal=1,
            text="other source",
            token_count=1,
            start=0,
            end=12,
        ),
    )
    selected = _select(
        authorities,
        tuple(
            _candidate(key, rank)
            for rank, key in enumerate(("first", "second", "third", "fourth", "other"), 1)
        ),
        EvidenceSelectionConfig(max_chunks=4, max_tokens=8),
    )
    assert "other" in tuple(item.chunk_key for item in selected)


def test_retrieved_evidence_rejects_unknown_zero_token_count() -> None:
    with pytest.raises(ValueError, match="token_count"):
        replace(_selected()[0], token_count=0)


def test_sufficiency_gate_rejects_unranked_evidence() -> None:
    evidence = _selected()
    with pytest.raises(ValueError, match="retrieval rank"):
        replace(evidence[0], dense_rank=None, lexical_rank=None)


def test_sufficiency_gate_checks_pinned_snapshot_scope_when_supplied() -> None:
    evidence = _selected()
    other_snapshot = replace(SNAPSHOT, generation=replace(GENERATION, generation_id=4))
    gate = EvidenceSufficiencyGate(policy_version="conservative-v1", min_chunks=2, min_tokens=1)
    with pytest.raises(RagOperationalError) as error:
        gate.evaluate(evidence, snapshot=other_snapshot)
    assert error.value.code is RagErrorCode.RETRIEVAL_INCONSISTENCY


def test_sufficiency_gate_rejects_course_scope_leak() -> None:
    evidence = _selected()
    course_snapshot = replace(SNAPSHOT, scope=CourseScope(11))
    gate = EvidenceSufficiencyGate(policy_version="conservative-v1", min_chunks=2, min_tokens=1)
    with pytest.raises(RagOperationalError) as error:
        gate.evaluate(evidence, snapshot=course_snapshot)
    assert error.value.code is RagErrorCode.RETRIEVAL_INCONSISTENCY


def test_sufficiency_thresholds_are_loaded_from_index_config() -> None:
    config = RagIndexConfig(
        chunk_target_tokens=400,
        chunk_preferred_min_tokens=200,
        chunk_preferred_max_tokens=600,
        chunk_hard_max_tokens=800,
        chunk_overlap_tokens=80,
        dense_candidate_limit=30,
        lexical_candidate_limit=30,
        rrf_k=60,
        max_evidence_chunks=6,
        max_evidence_tokens=2400,
        chunker_version="heading-page-v1",
        lexical_normalizer_version="unicode-v1",
        sufficiency_policy_version="conservative-v1",
        embedding_model_identifier="BAAI/bge-m3",
        embedding_model_revision="v1",
        embedding_dimensions=1024,
        sufficiency_min_chunks=3,
        sufficiency_min_tokens=6,
        sufficiency_max_fused_rank=4,
    )
    evidence = _selected()
    decision = EvidenceSufficiencyGate(config).evaluate(evidence[:2], snapshot=SNAPSHOT)
    assert decision.status is SufficiencyStatus.PARTIALLY_SUPPORTED
    assert (
        EvidenceSufficiencyGate(config).evaluate(evidence[:3], snapshot=SNAPSHOT).status
        is SufficiencyStatus.SUPPORTED
    )


def _bounded_index_config(**overrides: object) -> RagIndexConfig:
    values: dict[str, object] = {
        "chunk_target_tokens": 400,
        "chunk_preferred_min_tokens": 200,
        "chunk_preferred_max_tokens": 600,
        "chunk_hard_max_tokens": 800,
        "chunk_overlap_tokens": 80,
        "dense_candidate_limit": 30,
        "lexical_candidate_limit": 30,
        "rrf_k": 60,
        "max_evidence_chunks": 4,
        "max_evidence_tokens": 8,
        "chunker_version": "heading-page-v1",
        "lexical_normalizer_version": "unicode-v1",
        "sufficiency_policy_version": "conservative-v1",
        "embedding_model_identifier": "BAAI/bge-m3",
        "embedding_model_revision": "v1",
        "embedding_dimensions": 1024,
        "sufficiency_min_chunks": 2,
        "sufficiency_min_tokens": 1,
    }
    values.update(overrides)
    return RagIndexConfig(**values)  # type: ignore[arg-type]


def _single_document_monopoly_with_late_other(
    other_rank: int,
) -> tuple[tuple[AuthoritativeRetrievalChunk, ...], tuple[FusedCandidate, ...]]:
    # Fused ranks must stay contiguous, so same-document duplicates fill the
    # gap between the monopolizing prefix and the only other-document chunk.
    filler_keys = tuple(f"dup{rank}" for rank in range(5, other_rank))
    fillers = tuple(
        _authority(
            key,
            version_id=101,
            course_id=11,
            ordinal=1,
            text="same source 1",
            token_count=1,
            start=10,
            end=24,
        )
        for key in filler_keys
    )
    authorities = (
        fillers
        + tuple(
            _authority(
                key,
                version_id=101,
                course_id=11,
                ordinal=index,
                text=f"same source {index}",
                token_count=1,
                start=index * 10,
                end=index * 10 + 14,
            )
            for index, key in enumerate(("a", "b", "c", "d"), 1)
        )
        + (
            _authority(
                "other",
                version_id=102,
                course_id=12,
                ordinal=1,
                text="other source",
                token_count=1,
                start=0,
                end=12,
            ),
        )
    )
    candidates = tuple(
        _candidate(key, index) for index, key in enumerate(("a", "b", "c", "d", *filler_keys), 1)
    ) + (_candidate("other", other_rank),)
    return authorities, candidates


def test_selector_diversity_replacement_never_exceeds_gate_fused_rank_bound() -> None:
    """Regression: a rank-40 cross-document replacement made the gate fail closed."""
    authorities, candidates = _single_document_monopoly_with_late_other(31)

    selected = _select(authorities, candidates, EvidenceSelectionConfig(max_chunks=4, max_tokens=8))

    assert "other" not in tuple(item.chunk_key for item in selected)
    assert all(item.fused_rank <= 30 for item in selected)
    decision = EvidenceSufficiencyGate(min_chunks=2, min_tokens=1).evaluate(
        selected, snapshot=SNAPSHOT
    )
    assert decision.status is SufficiencyStatus.SUPPORTED


def test_selector_primary_pass_never_exceeds_gate_fused_rank_bound() -> None:
    duplicate_keys = tuple(f"dup{rank}" for rank in range(2, 31))
    authorities = tuple(
        _authority(
            key,
            version_id=101,
            course_id=11,
            ordinal=1,
            text="alpha",
            token_count=1,
            start=0,
            end=5,
        )
        for key in ("a", *duplicate_keys)
    ) + (
        _authority(
            "late",
            version_id=102,
            course_id=12,
            ordinal=1,
            text="late distinct source",
            token_count=1,
            start=0,
            end=20,
        ),
    )
    candidates = tuple(
        _candidate(key, rank) for rank, key in enumerate(("a", *duplicate_keys), 1)
    ) + (_candidate("late", 31),)

    selected = _select(authorities, candidates)

    assert tuple(item.chunk_key for item in selected) == ("a",)
    EvidenceSufficiencyGate(min_chunks=2, min_tokens=1).evaluate(selected, snapshot=SNAPSHOT)


def test_selector_fused_rank_bound_is_loaded_from_index_config() -> None:
    config = _bounded_index_config(sufficiency_max_fused_rank=4)
    authorities, candidates = _single_document_monopoly_with_late_other(5)

    selected = _select(authorities, candidates, EvidenceSelectionConfig.from_index_config(config))

    assert "other" not in tuple(item.chunk_key for item in selected)
    assert all(item.fused_rank <= 4 for item in selected)
    EvidenceSufficiencyGate(config).evaluate(selected, snapshot=SNAPSHOT)
