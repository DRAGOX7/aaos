"""Phase I3: verified, cited document and course summaries."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from test_rag_evidence import GENERATION, _authority

from aaos.rag.contracts import (
    EntailmentResult,
    RagAnswerStatus,
    RagClaim,
    RenderedRagResponse,
)
from aaos.rag.generation import CitationValidator, GroundedModelOutput
from aaos.rag.repository import AuthoritativeRetrievalChunk, QuerySnapshot
from aaos.rag.summaries import DocumentSummarizer, SummaryOutcome
from aaos.rag.summary_contracts import StoredDocumentSummary, SummaryCacheKey

MODEL = "qwen/qwen3.5-4b"


def _document(version_id: int, chunk_count: int, *, tokens: int = 600) -> tuple[Any, ...]:
    return tuple(
        _authority(
            f"v{version_id}-c{index}",
            version_id=version_id,
            course_id=11,
            ordinal=index,
            text=f"Fact {index} of document {version_id}.",
            token_count=tokens,
            start=index * 100,
            end=index * 100 + 40,
            title=f"doc-{version_id}.pdf",
        )
        for index in range(1, chunk_count + 1)
    )


class FakeRepository:
    def __init__(self, *documents: tuple[AuthoritativeRetrievalChunk, ...]) -> None:
        self.by_key = {item.chunk.chunk_key: item for doc in documents for item in doc}
        self.by_version: dict[int, tuple[AuthoritativeRetrievalChunk, ...]] = {
            doc[0].document_version.document_version_id: doc for doc in documents
        }
        self.cache: dict[SummaryCacheKey, StoredDocumentSummary] = {}
        self.puts = 0
        self.generation = GENERATION

    def pin_query_snapshot(self, scope: Any, *, normalized_query: str = "") -> QuerySnapshot:
        return QuerySnapshot(
            generation=self.generation,
            scope=scope,
            document_version_ids=scope.document_version_ids,
            normalized_query=normalized_query,
        )

    def list_chunks(self, identity: Any, document_version_id: int | None = None) -> Any:
        assert document_version_id is not None
        return tuple(item.chunk for item in self.by_version[document_version_id])

    def materialize_snapshot_chunks(self, snapshot: QuerySnapshot, keys: tuple[str, ...]) -> Any:
        return tuple(self.by_key[key] for key in keys)

    def get_document_summary(self, key: SummaryCacheKey) -> StoredDocumentSummary | None:
        return self.cache.get(key)

    def put_document_summary(self, summary: StoredDocumentSummary) -> None:
        self.puts += 1
        self.cache.setdefault(summary.key, summary)


class FakeGenerator:
    """Writes `claims_per_evidence` points per evidence item, citing that evidence."""

    def __init__(self, claims_per_evidence: int = 1, *, text: Callable[[str], str] | None = None):
        self.claims_per_evidence = claims_per_evidence
        self.text = text or (lambda exact: f"Point: {exact}")
        self.requests: list[Any] = []

    async def generate(self, request: Any, sufficiency: Any) -> GroundedModelOutput:
        self.requests.append(request)
        cited = [
            (self.text(item.exact_text), item.evidence_id)
            for item in request.evidence
            for _ in range(self.claims_per_evidence)
        ]
        claims = tuple(
            RagClaim(f"c{position}", text, (evidence_id,))
            for position, (text, evidence_id) in enumerate(cited, 1)
        )
        return GroundedModelOutput(
            status=RagAnswerStatus.ANSWER,
            answer_text="summary",
            language=request.answer_language,
            claims=claims,
            generation_id=request.generation_id,
            content_revision=request.content_revision,
            policy_version=request.policy_version,
        )


class FakeVerifier:
    async def verify(self, claim: RagClaim, *, evidence: Any) -> EntailmentResult:
        return (
            EntailmentResult.NOT_ENTAILED
            if "UNSUPPORTED" in claim.text
            else EntailmentResult.ENTAILED
        )


def _summarizer(
    repository: FakeRepository, generator: FakeGenerator, clock: Callable[[], float] | None = None
) -> DocumentSummarizer:
    return DocumentSummarizer(
        repository,
        generator,
        FakeVerifier(),
        CitationValidator(),
        model_identifier=MODEL,
        clock=clock or (lambda: 0.0),
    )


def test_document_summary_covers_sections_in_reading_order_and_is_cited() -> None:
    repository = FakeRepository(_document(101, 8))
    generator = FakeGenerator()

    result = asyncio.run(_summarizer(repository, generator).summarize_document(101, "en"))

    assert result.outcome is SummaryOutcome.READY
    assert isinstance(result.response, RenderedRagResponse)
    answer = result.response.answer
    assert [claim.text for claim in answer.claims] == [
        f"Point: Fact {index} of document 101." for index in range(1, 9)
    ]
    assert len(answer.citations) == 8
    assert len(generator.requests) == 3  # 600-token chunks: sections of 3, 3, 2
    assert all(request.answer_language == "en" for request in generator.requests)
    assert repository.puts == 1


def test_cached_summary_is_revalidated_without_new_model_calls() -> None:
    repository = FakeRepository(_document(101, 4))
    summarizer = _summarizer(repository, FakeGenerator())
    first = asyncio.run(summarizer.summarize_document(101, "en"))
    replay_generator = FakeGenerator()

    second = asyncio.run(_summarizer(repository, replay_generator).summarize_document(101, "en"))

    assert second.from_cache is True
    assert replay_generator.requests == []
    assert first.response is not None and second.response is not None
    assert [c.text for c in second.response.answer.claims] == [
        c.text for c in first.response.answer.claims
    ]


def test_reduce_spreads_twelve_points_evenly_across_sections() -> None:
    repository = FakeRepository(_document(101, 9))
    result = asyncio.run(
        _summarizer(repository, FakeGenerator(claims_per_evidence=2)).summarize_document(101, "en")
    )

    assert result.response is not None
    texts = [claim.text for claim in result.response.answer.claims]
    assert len(texts) == 12
    sections = [texts[0:4], texts[4:8], texts[8:12]]
    assert [{t.split()[2] for t in section} for section in sections] == [
        {"1", "2"},
        {"4", "5"},
        {"7", "8"},
    ]


def test_unverified_points_are_dropped_and_counted() -> None:
    repository = FakeRepository(_document(101, 3))
    generator = FakeGenerator(
        text=lambda exact: f"UNSUPPORTED {exact}" if "Fact 2" in exact else f"Point: {exact}"
    )

    result = asyncio.run(_summarizer(repository, generator).summarize_document(101, "en"))

    assert result.response is not None
    assert len(result.response.answer.claims) == 2
    assert result.dropped_points == 1


def test_no_verified_points_is_not_cached() -> None:
    repository = FakeRepository(_document(101, 2))
    generator = FakeGenerator(text=lambda exact: f"UNSUPPORTED {exact}")

    result = asyncio.run(_summarizer(repository, generator).summarize_document(101, "en"))

    assert result.outcome is SummaryOutcome.NO_VERIFIED_POINTS
    assert result.response is None
    assert repository.puts == 0


def test_summary_that_cannot_finish_in_budget_is_not_prepared_and_not_cached() -> None:
    repository = FakeRepository(_document(101, 9))
    generator = FakeGenerator()

    result = asyncio.run(
        _summarizer(repository, generator, clock=lambda: 100.0).summarize_document(
            101, "en", deadline=150.0
        )
    )

    assert result.outcome is SummaryOutcome.NOT_PREPARED
    assert result.missing_document_version_ids == (101,)
    assert repository.puts == 0


def test_arabic_summary_is_generated_and_cached_separately() -> None:
    repository = FakeRepository(_document(101, 2))
    generator = FakeGenerator()
    summarizer = _summarizer(repository, generator)

    asyncio.run(summarizer.summarize_document(101, "en"))
    arabic = asyncio.run(summarizer.summarize_document(101, "ar"))

    assert arabic.from_cache is False
    assert generator.requests[-1].answer_language == "ar"
    assert {key.language for key in repository.cache} == {"en", "ar"}


def test_course_summary_combines_cited_points_from_each_document() -> None:
    repository = FakeRepository(_document(101, 3), _document(102, 3))
    summarizer = _summarizer(repository, FakeGenerator())

    result = asyncio.run(summarizer.summarize_course((101, 102), "en"))

    assert result.outcome is SummaryOutcome.READY
    assert result.response is not None
    titles = {citation.document_title for citation in result.response.answer.citations}
    assert titles == {"doc-101.pdf", "doc-102.pdf"}
    assert result.document_version_ids == (101, 102)


def test_course_summary_reports_documents_it_could_not_prepare_in_budget() -> None:
    repository = FakeRepository(_document(101, 3), _document(102, 9))
    summarizer = _summarizer(repository, FakeGenerator())
    asyncio.run(summarizer.summarize_document(101, "en"))
    late = _summarizer(repository, FakeGenerator(), clock=lambda: 100.0)

    result = asyncio.run(late.summarize_course((101, 102), "en", deadline=150.0))

    assert result.outcome is SummaryOutcome.NOT_PREPARED
    assert result.missing_document_version_ids == (102,)


class FakeNliClassifier:
    """Overflows above `limit` characters; entails when the hypothesis key is present."""

    def __init__(self, limit: int = 120) -> None:
        self.limit = limit
        self.premises: list[str] = []
        self.closed = False

    def classify(self, premise: str, hypothesis: str) -> Any:
        from aaos.inference.nli import NliClassification, NliDecision, NliInputOverflowError

        if len(premise) + len(hypothesis) > self.limit:
            raise NliInputOverflowError("too long")
        self.premises.append(premise)
        key = hypothesis.split(":", 1)[-1].strip()
        entailed = key in premise
        return NliClassification(
            NliDecision.ENTAILED if entailed else NliDecision.NOT_ENTAILED,
            "entailment" if entailed else "neutral",
            0.9 if entailed else 0.1,
            0.1,
            0.0,
        )

    def close(self) -> None:
        self.closed = True


def _long_evidence(text: str) -> Any:
    from test_rag_generation import _evidence

    return _evidence("ev-long", text)


def test_windowed_verifier_finds_support_inside_a_long_premise() -> None:
    from aaos.rag.summaries import WindowedClaimVerifier

    filler = " ".join(f"word{index}" for index in range(60))
    evidence = (_long_evidence(f"{filler} The exam is on Sunday."),)
    classifier = FakeNliClassifier()
    verifier = WindowedClaimVerifier(classifier)

    supported = asyncio.run(
        verifier.verify(RagClaim("c1", "Point: Sunday", ("ev-long",)), evidence=evidence)
    )
    unsupported = asyncio.run(
        verifier.verify(RagClaim("c2", "Point: Monday", ("ev-long",)), evidence=evidence)
    )

    assert supported is EntailmentResult.ENTAILED
    assert unsupported is EntailmentResult.NOT_ENTAILED
    assert all(len(premise) <= 120 for premise in classifier.premises)


def test_windowed_verifier_fails_closed_when_the_claim_alone_is_too_long() -> None:
    import pytest

    from aaos.rag.contracts import RagErrorCode, RagOperationalError
    from aaos.rag.summaries import WindowedClaimVerifier

    verifier = WindowedClaimVerifier(FakeNliClassifier(limit=20))
    claim = RagClaim("c1", "Point: " + "x" * 40, ("ev-long",))

    with pytest.raises(RagOperationalError) as error:
        asyncio.run(verifier.verify(claim, evidence=(_long_evidence("short premise text"),)))

    assert error.value.code is RagErrorCode.ENTAILMENT_INPUT_OVERFLOW


def test_windowed_verifier_rejects_references_outside_the_supplied_evidence() -> None:
    import pytest

    from aaos.rag.contracts import RagErrorCode, RagOperationalError
    from aaos.rag.summaries import WindowedClaimVerifier

    verifier = WindowedClaimVerifier(FakeNliClassifier())

    with pytest.raises(RagOperationalError) as error:
        asyncio.run(
            verifier.verify(
                RagClaim("c1", "Point: x", ("missing",)), evidence=(_long_evidence("text"),)
            )
        )

    assert error.value.code is RagErrorCode.INVALID_EVIDENCE_REFERENCE


class FlakyGenerator(FakeGenerator):
    """Fails on the section containing ``marker`` for the first ``failures`` calls."""

    def __init__(self, marker: str, failures: int, code: Any = None) -> None:
        from aaos.rag.contracts import RagErrorCode

        super().__init__()
        self.marker = marker
        self.failures = failures
        self.code = code or RagErrorCode.INVALID_EVIDENCE_REFERENCE

    async def generate(self, request: Any, sufficiency: Any) -> GroundedModelOutput:
        from aaos.rag.contracts import RagOperationalError

        if self.failures and any(self.marker in item.exact_text for item in request.evidence):
            self.failures -= 1
            self.requests.append(request)
            raise RagOperationalError(self.code)
        return await super().generate(request, sufficiency)


def test_a_section_with_bad_model_output_is_retried_once() -> None:
    repository = FakeRepository(_document(101, 9))
    generator = FlakyGenerator("Fact 4 of", failures=1)

    result = asyncio.run(_summarizer(repository, generator).summarize_document(101, "en"))

    assert result.outcome is SummaryOutcome.READY
    assert result.skipped_sections == 0
    assert len(generator.requests) == 4  # three sections plus one retry
    assert result.response is not None
    assert any("Fact 4" in claim.text for claim in result.response.answer.claims)


def test_a_section_that_keeps_failing_is_skipped_not_the_whole_document() -> None:
    """Regression: one bad section output failed the whole document's summary."""
    repository = FakeRepository(_document(101, 9))
    generator = FlakyGenerator("Fact 4 of", failures=2)

    result = asyncio.run(_summarizer(repository, generator).summarize_document(101, "en"))

    assert result.outcome is SummaryOutcome.READY
    assert result.skipped_sections == 1
    assert result.response is not None
    texts = [claim.text for claim in result.response.answer.claims]
    assert not any("Fact 4" in text or "Fact 5" in text or "Fact 6" in text for text in texts)
    assert any("Fact 1" in text for text in texts) and any("Fact 7" in text for text in texts)


def test_infrastructure_failures_still_fail_the_document() -> None:
    import pytest

    from aaos.rag.contracts import RagErrorCode, RagOperationalError

    repository = FakeRepository(_document(101, 9))
    generator = FlakyGenerator("Fact 4 of", failures=1, code=RagErrorCode.MODEL_UNAVAILABLE)

    with pytest.raises(RagOperationalError) as error:
        asyncio.run(_summarizer(repository, generator).summarize_document(101, "en"))

    assert error.value.code is RagErrorCode.MODEL_UNAVAILABLE
    assert repository.puts == 0
