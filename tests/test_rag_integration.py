"""Disposable end-to-end Phase 4 RAG integration coverage."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aaos.documents.contracts import IngestionStatus
from aaos.inference import ChatRequest, ChatResponse, ModelAvailability
from aaos.persistence.academic_models import CourseORM, RemoteFileORM
from aaos.persistence.database import create_database_engine, create_session_factory
from aaos.persistence.document_models import (
    ContentBlobORM,
    DocumentORM,
    DocumentPageORM,
    DocumentVersionORM,
)
from aaos.rag.chroma import ChromaDenseIndexFactory
from aaos.rag.config import RagIndexConfig, RagRuntimePaths, compute_config_fingerprint
from aaos.rag.contracts import (
    CourseScope,
    GenerationStatus,
    GlobalScope,
    HistoricalScope,
    RagAnswerStatus,
    RagQuery,
    RenderedRagResponse,
)
from aaos.rag.embeddings import FakeEmbeddingService
from aaos.rag.repository import RagRepository
from aaos.rag.runtime import Phase4RagRuntime, build_phase4_runtime

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
SessionFactory = Callable[[], Session]


class _GroundingGateway:
    """Return deterministic claims over the evidence IDs in the prompt."""

    def __init__(self) -> None:
        self.calls = 0
        self.requests: list[ChatRequest] = []

    async def check_availability(self) -> ModelAvailability:
        return ModelAvailability(True, True, "fake-answer", ("fake-answer",), (4096,))

    async def generate(self, request: ChatRequest) -> ChatResponse:
        self.calls += 1
        self.requests.append(request)
        if request.structured_output_schema == "requirement_decomposition":
            return ChatResponse(
                content='{"requirements":[{"requirement_text":"Answer the question"}]}',
                model_identifier="qwen/qwen3.5-4b",
            )
        if request.structured_output_schema == "atomic_requirement_decomposition":
            return ChatResponse(
                content=(
                    '{"status":"OK","requirements":[{"requirement_text":"Answer the question"}]}'
                ),
                model_identifier="qwen/qwen3.5-4b",
            )
        if request.structured_output_schema == "evidence_segments_single_requirement":
            payload = next(
                message.content.split("<segment-selection-input>", 1)[1].split(
                    "</segment-selection-input>", 1
                )[0]
                for message in request.messages
                if "<segment-selection-input>" in message.content
            )
            segment_input = json.loads(payload)
            segments = segment_input["segments"]
            ids = [segments[0]["segment_id"]] if segments else []
            return ChatResponse(
                content=json.dumps({"segment_ids": ids}), model_identifier="qwen/qwen3.5-4b"
            )
        if request.structured_output_schema == "requirement_entailment":
            return ChatResponse(
                content='{"result":"ENTAILED"}',
                model_identifier="qwen/qwen3.5-4b",
            )
        if request.structured_output_schema == "requirement_coverage":
            payload = next(
                message.content.split("<coverage-input>", 1)[1].split("</coverage-input>", 1)[0]
                for message in request.messages
                if "<coverage-input>" in message.content
            )
            coverage = json.loads(payload)
            evidence_ids = [item["evidence_id"] for item in coverage["evidence"][:1]]
            judgments = [
                {
                    "requirement_id": item["requirement_id"],
                    "support": "SUPPORTED" if evidence_ids else "UNSUPPORTED",
                    "evidence_ids": evidence_ids,
                }
                for item in coverage["requirements"]
            ]
            return ChatResponse(
                content=json.dumps({"requirements": judgments}),
                model_identifier="qwen/qwen3.5-4b",
            )
        if any("<semantic-input>" in message.content for message in request.messages):
            return ChatResponse(
                content='{"status":"SUPPORTED","unsupported_aspects":[]}',
                model_identifier="qwen/qwen3.5-4b",
            )
        payload = next(
            message.content.split("<grounded-input>", 1)[1].split("</grounded-input>", 1)[0]
            for message in request.messages
            if "<grounded-input>" in message.content
        )
        grounded = json.loads(payload)
        evidence = grounded["evidence"]
        language = grounded["answer_language"]
        if language == "ar":
            text = "هذه إجابة اختبارية مؤسّسة على الدليل."
        else:
            text = "This is a deterministic evidence-grounded test answer."
        if not evidence:
            response = {
                "status": "INSUFFICIENT_EVIDENCE",
                "answer_text": "Insufficient evidence.",
                "language": language,
                "claims": [],
            }
        else:
            response = {
                "status": "ANSWER",
                "answer_text": text,
                "language": language,
                "claims": [
                    {
                        "claim_id": "claim-1",
                        "text": text,
                        "evidence_ids": [evidence[0]["evidence_id"]],
                    }
                ],
            }
        return ChatResponse(
            content=json.dumps(response, ensure_ascii=False), model_identifier="fake"
        )


class _FakeModelLifecycle:
    def ensure_answer_model_unloaded(self) -> None:
        return None

    def ensure_answer_model_loaded(self) -> None:
        return None

    def close(self) -> None:
        return None


def _migration_config(database_path: Path) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("aaos.database_path", str(database_path))
    return config


def _rag_config() -> RagIndexConfig:
    return RagIndexConfig(
        32,
        1,
        32,
        64,
        4,
        12,
        12,
        60,
        6,
        256,
        "chunk-v1",
        "unicode-v1",
        "conservative-v1",
        "fake-embedding-service",
        "test-only-v1",
        3,
        sufficiency_min_chunks=1,
        sufficiency_min_tokens=1,
    )


def _paths(tmp_path: Path) -> RagRuntimePaths:
    root = tmp_path.resolve()
    return RagRuntimePaths(
        root / "database" / "rag.db",
        root / "rag" / "chroma",
        root / "temporary" / "phase4.lock",
        root / "rag" / "evaluations",
    )


def _add_document(
    sessions: SessionFactory,
    *,
    course: CourseORM,
    source_id: str,
    pages: tuple[str, ...],
) -> int:
    digest = sha256((source_id + "\n" + "\n".join(pages)).encode("utf-8")).hexdigest()
    with sessions() as session, session.begin():
        remote = RemoteFileORM(
            course_id=course.id,
            source_system="phase3",
            source_id=source_id,
            title=f"{source_id}.pdf",
            is_active=True,
        )
        session.add(remote)
        session.flush()
        document = DocumentORM(
            remote_file_id=remote.id,
            ingestion_status=IngestionStatus.READY.value,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(document)
        session.flush()
        blob = ContentBlobORM(
            sha256=digest,
            size_bytes=100,
            media_type="application/pdf",
            storage_key=f"sha256/{digest[:2]}/{digest}.pdf",
            created_at=NOW,
        )
        session.add(blob)
        session.flush()
        version = DocumentVersionORM(
            document_id=document.id,
            version_number=1,
            content_blob_id=blob.id,
            source_filename=f"{source_id}.pdf",
            ingested_at=NOW,
            extraction_status="SUCCESS",
            page_count=len(pages),
        )
        session.add(version)
        session.flush()
        session.add_all(
            DocumentPageORM(
                document_version_id=version.id,
                page_number=page_number,
                text=text,
                text_source="EMBEDDED_PDF",
                width_points=612.0,
                height_points=792.0,
                created_at=NOW,
            )
            for page_number, text in enumerate(pages, start=1)
        )
        return int(version.id)


@contextmanager
def _seeded_runtime(tmp_path: Path) -> Iterator[tuple[Phase4RagRuntime, dict[str, int], Path]]:
    paths = _paths(tmp_path)
    paths.database_path.parent.mkdir(parents=True)
    command.upgrade(_migration_config(paths.database_path), "head")
    engine = create_database_engine(paths.database_path)
    sessions = create_session_factory(engine)
    try:
        with sessions() as session, session.begin():
            course_en = CourseORM(
                source_system="phase3",
                source_id="course-en",
                title="English course",
                is_active=True,
            )
            course_ar = CourseORM(
                source_system="phase3",
                source_id="course-ar",
                title="دورة عربية",
                is_active=True,
            )
            session.add_all((course_en, course_ar))
            session.flush()
            course_ids = {"en": int(course_en.id), "ar": int(course_ar.id)}
        version_ids = {
            "en": _add_document(
                sessions,
                course=course_en,
                source_id="english-guide",
                pages=(
                    "The English examination deadline is Friday and the room is 101.",
                    "Bring the approved calculator to the examination.",
                ),
            ),
            "ar": _add_document(
                sessions,
                course=course_ar,
                source_id="arabic-guide",
                pages=(
                    "موعد الامتحان العربي يوم الأحد والقاعة ٢٠٢.",
                    "أحضر الآلة الحاسبة المعتمدة إلى الامتحان.",
                ),
            ),
        }
        repository = RagRepository(sessions)
        config = _rag_config()
        initial = repository.create_generation(
            config_fingerprint=compute_config_fingerprint(config),
            chunker_version=config.chunker_version,
            chunking_config="{}",
            lexical_normalizer_version=config.lexical_normalizer_version,
            embedding_model=config.embedding_model_identifier,
            embedding_revision=config.embedding_model_revision,
            embedding_dimensions=config.embedding_dimensions,
            retrieval_config="{}",
        )
        repository.activate_generation(initial.identity, expected_revision=0)
    finally:
        engine.dispose()

    gateway = _GroundingGateway()
    runtime = build_phase4_runtime(
        paths=paths,
        config=_rag_config(),
        gateway=gateway,
        dense_factory=ChromaDenseIndexFactory(paths),
        embedding_factory=lambda _config: FakeEmbeddingService(dimensions=3),
        model_lifecycle_factory=lambda: _FakeModelLifecycle(),
    )
    try:
        yield runtime, {**course_ids, **version_ids}, paths.database_path
    finally:
        runtime.close()


def _ask(runtime: Phase4RagRuntime, query: RagQuery) -> RenderedRagResponse:
    return asyncio.run(runtime.service.ask(query))


def _phase3_counts(database_path: Path) -> tuple[int, int, int, int]:
    engine = create_database_engine(database_path)
    sessions = create_session_factory(engine)
    try:
        with sessions() as session, session.begin():
            return (
                int(session.scalar(select(func.count(CourseORM.id))) or 0),
                int(session.scalar(select(func.count(RemoteFileORM.id))) or 0),
                int(session.scalar(select(func.count(DocumentORM.id))) or 0),
                int(session.scalar(select(func.count(DocumentVersionORM.id))) or 0),
            )
    finally:
        engine.dispose()


def test_disposable_bilingual_pipeline_uses_authoritative_citations(tmp_path: Path) -> None:
    with _seeded_runtime(tmp_path) as (runtime, ids, database_path):
        baseline = _phase3_counts(database_path)
        rebuilt = runtime.indexing.rebuild()
        assert rebuilt.safe_status == "PASS"
        first = _ask(runtime, RagQuery("When is the examination deadline?", CourseScope(ids["en"])))
        second = _ask(runtime, RagQuery("ما موعد الامتحان؟", CourseScope(ids["ar"])))
        repeated = _ask(
            runtime, RagQuery("When is the examination deadline?", CourseScope(ids["en"]))
        )

        assert first.answer.status is RagAnswerStatus.ANSWER
        assert second.answer.status is RagAnswerStatus.ANSWER
        assert first.answer.citations[0].page_start == 1
        assert second.answer.citations[0].page_start == 1
        assert first.answer.citations[0].document_version_id == ids["en"]
        assert second.answer.citations[0].document_version_id == ids["ar"]
        # Evidence IDs are request-local control identifiers; trusted citation
        # provenance and rendered output remain deterministic across requests.
        assert repeated.rendered_text == first.rendered_text
        assert repeated.answer.citations == first.answer.citations
        assert _phase3_counts(database_path) == baseline


def test_index_new_noop_and_generation_rebuild_preserve_historical_snapshot(
    tmp_path: Path,
) -> None:
    with _seeded_runtime(tmp_path) as (runtime, ids, _database_path):
        first_rebuild = runtime.indexing.rebuild()
        no_op = runtime.indexing.index_new()
        assert no_op.safe_status == "NO_OP"
        assert no_op.content_revision == first_rebuild.content_revision

        paths = _paths(tmp_path)
        engine = create_database_engine(paths.database_path)
        sessions = create_session_factory(engine)
        try:
            repository = RagRepository(sessions)
            pinned = repository.pin_query_snapshot(GlobalScope(), normalized_query="examination")
            old_generation = pinned.generation
            assert old_generation.generation_id == first_rebuild.generation_id
            second_rebuild = runtime.indexing.rebuild()
            assert second_rebuild.generation_id != old_generation.generation_id
            assert (
                repository.get_generation(old_generation.generation_id).status
                is GenerationStatus.RETIRED
            )
            old_chunks = repository.materialize_snapshot_chunks(
                pinned,
                (repository.list_chunks(old_generation, ids["en"])[0].chunk_key,),
            )
            assert old_chunks[0].chunk.document_version_id == ids["en"]
        finally:
            engine.dispose()

        # Explicit historical scope remains a separate, bounded query mode.
        historical = _ask(
            runtime, RagQuery("What is the examination?", HistoricalScope((ids["en"],)))
        )
        assert historical.answer.citations[0].document_version_id == ids["en"]
        assert historical.answer.status is RagAnswerStatus.ANSWER


def test_global_scope_cannot_leak_cross_course_evidence(tmp_path: Path) -> None:
    with _seeded_runtime(tmp_path) as (runtime, ids, _database_path):
        runtime.indexing.rebuild()
        result = _ask(runtime, RagQuery("What is the English examination deadline?", GlobalScope()))
        assert result.answer.status is RagAnswerStatus.ANSWER
        assert result.answer.citations
        assert all(
            citation.document_version_id == ids["en"] for citation in result.answer.citations
        )


def test_runtime_uses_only_disposable_paths_and_closes_resources(tmp_path: Path) -> None:
    with _seeded_runtime(tmp_path) as (_runtime, _ids, database_path):
        assert database_path != Path.cwd() / "data" / "database" / "aaos.db"
        assert not (Path.cwd() / "data" / "database" / "aaos.db").exists()
        assert not (Path.cwd() / "data" / "rag").exists()
        assert not (Path.cwd() / "data" / "documents").exists()
