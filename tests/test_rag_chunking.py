from __future__ import annotations

import pytest

from aaos.rag.chunking import (
    ChunkCoverage,
    ChunkDraft,
    ChunkingResult,
    ChunkSourceSpanDraft,
    DocumentPage,
    count_tokens,
)
from aaos.rag.chunking import (
    chunk_document as _chunk_document,
)
from aaos.rag.chunking import (
    chunk_document_result as _chunk_document_result,
)
from aaos.rag.config import RagIndexConfig

SOURCE_SHA = "a" * 64


def chunk_document(
    document_version_id: int,
    pages: tuple[DocumentPage, ...],
    config: RagIndexConfig,
    *,
    source_version_sha256: str = SOURCE_SHA,
) -> tuple[ChunkDraft, ...]:
    return _chunk_document(
        document_version_id,
        pages,
        config,
        source_version_sha256=source_version_sha256,
    )


def chunk_document_result(
    document_version_id: int,
    pages: tuple[DocumentPage, ...],
    config: RagIndexConfig,
) -> ChunkingResult:
    return _chunk_document_result(
        document_version_id,
        pages,
        config,
        source_version_sha256=SOURCE_SHA,
    )


def make_config(**changes: object) -> RagIndexConfig:
    values: dict[str, object] = {
        "chunk_target_tokens": 5,
        "chunk_preferred_min_tokens": 3,
        "chunk_preferred_max_tokens": 6,
        "chunk_hard_max_tokens": 8,
        "chunk_overlap_tokens": 1,
        "dense_candidate_limit": 30,
        "lexical_candidate_limit": 30,
        "rrf_k": 60,
        "max_evidence_chunks": 6,
        "max_evidence_tokens": 2400,
        "chunker_version": "deterministic-v1",
        "lexical_normalizer_version": "lexical-v1",
        "sufficiency_policy_version": "conservative-v1",
        "embedding_model_identifier": "BAAI/bge-m3",
        "embedding_model_revision": "v1",
        "embedding_dimensions": 1024,
    }
    values.update(changes)
    return RagIndexConfig(**values)  # type: ignore[arg-type]


def page(page_id: int, version_id: int, number: int, text: str) -> DocumentPage:
    return DocumentPage(
        document_page_id=page_id,
        document_version_id=version_id,
        page_number=number,
        text=text,
    )


def rebuild_text_from_spans(chunk: object, pages: tuple[DocumentPage, ...]) -> str:
    spans = getattr(chunk, "source_spans")
    pieces: list[str] = []
    for span in spans:
        source_page = next(item for item in pages if item.document_page_id == span.document_page_id)
        pieces.append(source_page.text[span.source_char_start : span.source_char_end])
    return "".join(pieces)


def test_page_and_paragraph_boundaries_preserve_exact_text_and_spans() -> None:
    pages = (page(11, 7, 1, "First paragraph.\n\nSecond paragraph."),)

    chunks = chunk_document(7, pages, make_config())

    assert len(chunks) == 1
    assert chunks[0].text == pages[0].text
    assert rebuild_text_from_spans(chunks[0], pages) == chunks[0].text
    assert chunks[0].source_spans == (
        ChunkSourceSpanDraft(
            document_page_id=11,
            page_number=1,
            source_char_start=0,
            source_char_end=len(pages[0].text),
            chunk_char_start=0,
            chunk_char_end=len(pages[0].text),
        ),
    )


def test_cross_page_chunk_has_ordered_exact_spans() -> None:
    pages = (page(11, 7, 1, "one two "), page(12, 7, 2, "three four"))

    chunks = chunk_document(7, pages, make_config())

    assert len(chunks) == 1
    assert chunks[0].text == "one two three four"
    assert rebuild_text_from_spans(chunks[0], pages) == chunks[0].text
    assert [span.page_number for span in chunks[0].source_spans] == [1, 2]
    assert [span.chunk_char_start for span in chunks[0].source_spans] == [0, 8]


def test_pages_without_a_source_boundary_are_not_merged() -> None:
    pages = (page(11, 7, 1, "one two"), page(12, 7, 2, "three four"))

    chunks = chunk_document(7, pages, make_config())

    assert [chunk.text for chunk in chunks] == ["one two", "three four"]
    assert chunks[0].source_spans[-1].document_page_id != chunks[1].source_spans[0].document_page_id


def test_long_natural_unit_uses_bounded_overlap_only_when_split() -> None:
    config = make_config()
    text = " ".join(f"term{i}" for i in range(17))
    pages = (page(11, 7, 1, text),)

    chunks = chunk_document(7, pages, config)

    assert len(chunks) >= 3
    assert all(chunk.token_count <= config.chunk_hard_max_tokens for chunk in chunks)
    assert all(count_tokens(chunk.text) == chunk.token_count for chunk in chunks)
    for previous, current in zip(chunks, chunks[1:]):
        assert set(previous.text.split()) & set(current.text.split())
    assert chunks[0].source_spans[0].source_char_start == 0
    assert chunks[-1].source_spans[-1].source_char_end == len(text)
    assert chunks == chunk_document(7, pages, config)


def test_natural_units_do_not_receive_blanket_overlap_or_lose_content() -> None:
    text = "one two three four five\n\nalpha beta gamma delta epsilon"
    pages = (page(11, 7, 1, text),)

    chunks = chunk_document(7, pages, make_config())

    assert len(chunks) == 2
    assert "one two three four five" in chunks[0].text
    assert "alpha beta gamma delta epsilon" in chunks[1].text
    assert chunks[0].source_spans[-1].source_char_end == chunks[1].source_spans[0].source_char_start
    assert chunks[0].text + chunks[1].text == text


def test_empty_pages_are_skipped_and_coverage_is_deterministic() -> None:
    pages = (page(11, 7, 1, "   \n\n"), page(12, 7, 2, "usable text"))
    empty_pages = (page(11, 7, 1, " \t\n"),)

    result = chunk_document_result(7, pages, make_config())
    empty_result = chunk_document_result(7, empty_pages, make_config())

    assert result.coverage is ChunkCoverage.PARTIAL
    assert result.chunks
    assert empty_result.coverage is ChunkCoverage.NONE
    assert empty_result.chunks == ()


def test_all_nonempty_pages_have_full_coverage() -> None:
    pages = (page(11, 7, 1, "alpha"), page(12, 7, 2, "beta"))

    assert chunk_document_result(7, pages, make_config()).coverage is ChunkCoverage.FULL


def test_heading_metadata_is_conservative_and_deterministic() -> None:
    pages = (page(11, 7, 1, "# Heading\n\nParagraph body"),)

    chunks = chunk_document(7, pages, make_config())

    assert chunks[0].heading == "Heading"
    assert chunks == chunk_document(7, pages, make_config())


def test_unicode_and_technical_terms_remain_exact() -> None:
    text = "العربية BERT YOLOv11 mAP50-95 CNN"
    pages = (page(11, 7, 1, text),)

    chunks = chunk_document(7, pages, make_config())

    assert chunks[0].text == text
    assert rebuild_text_from_spans(chunks[0], pages) == text
    assert "العربية" in chunks[0].text
    assert "mAP50-95" in chunks[0].text


def test_chunk_keys_change_with_version_text_or_configuration() -> None:
    pages = (page(11, 7, 1, "stable text"),)
    base = chunk_document(7, pages, make_config())

    assert (
        base[0].chunk_key
        != chunk_document(8, (page(11, 8, 1, "stable text"),), make_config())[0].chunk_key
    )
    assert (
        base[0].chunk_key
        != chunk_document(7, (page(11, 7, 1, "changed"),), make_config())[0].chunk_key
    )
    changed_config = make_config(chunker_version="deterministic-v2")
    assert base[0].chunk_key != chunk_document(7, pages, changed_config)[0].chunk_key


def test_chunk_key_includes_source_version_sha256() -> None:
    pages = (page(11, 7, 1, "stable text"),)
    first = chunk_document(7, pages, make_config(), source_version_sha256=SOURCE_SHA)
    second = chunk_document(7, pages, make_config(), source_version_sha256="b" * 64)

    assert first[0].chunk_key != second[0].chunk_key


def test_source_version_sha256_is_required_and_validated() -> None:
    pages = (page(11, 7, 1, "stable text"),)

    with pytest.raises(ValueError, match="source version SHA-256"):
        chunk_document(7, pages, make_config(), source_version_sha256="not-a-sha")
    with pytest.raises(TypeError, match="source_version_sha256"):
        _chunk_document(7, pages, make_config())  # type: ignore[call-arg]


def test_invalid_page_order_and_version_fail_closed() -> None:
    with pytest.raises(ValueError, match="ordered"):
        chunk_document(
            7,
            (page(11, 7, 2, "later"), page(12, 7, 1, "earlier")),
            make_config(),
        )
    with pytest.raises(ValueError, match="document version"):
        chunk_document(7, (page(11, 8, 1, "wrong"),), make_config())


def test_drafts_are_immutable() -> None:
    chunk = chunk_document(7, (page(11, 7, 1, "text"),), make_config())[0]

    with pytest.raises(AttributeError):
        chunk.text = "changed"  # type: ignore[misc]


def test_config_values_are_used_without_hidden_tokenizer_or_model_dependency() -> None:
    config = make_config(chunk_target_tokens=4, chunk_preferred_min_tokens=2)
    pages = (page(11, 7, 1, "one two three four five six"),)

    chunks = chunk_document(7, pages, config)

    assert all(chunk.token_count <= config.chunk_hard_max_tokens for chunk in chunks)
    assert all(chunk.token_count == count_tokens(chunk.text) for chunk in chunks)


def test_source_spans_use_half_open_intervals() -> None:
    pages = (page(11, 7, 1, "alpha beta"),)
    chunk = chunk_document(7, pages, make_config())[0]
    span = chunk.source_spans[0]

    assert pages[0].text[span.source_char_start : span.source_char_end] == chunk.text
    assert span.source_char_start == 0
    assert span.source_char_end == len(pages[0].text)
    assert span.chunk_char_start == 0
    assert span.chunk_char_end == len(chunk.text)


def test_document_page_is_frozen() -> None:
    value = page(11, 7, 1, "text")

    with pytest.raises(AttributeError):
        value.text = "changed"  # type: ignore[misc]
    assert value.text == "text"
