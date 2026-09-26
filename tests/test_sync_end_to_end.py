from __future__ import annotations

import io
import sys
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from docx_fixtures import mixed_structure_docx
from pdf_fixtures import valid_multipage_pdf
from sqlalchemy import func, select

from aaos.academic.source import CompleteSourceSnapshot, RawCourse, RawRemoteFile
from aaos.documents.cli import Phase3ServiceRuntime
from aaos.documents.config import DocumentRuntimePaths, resolve_document_runtime_paths
from aaos.documents.contracts import FetchedDocx, FetchedPdf, RemoteFileSelection
from aaos.documents.docx import DocxProcessor
from aaos.documents.lock import IngestionLock
from aaos.documents.pdf import PdfProcessor
from aaos.documents.repository import DocumentRepository
from aaos.documents.service import Phase3IngestionService
from aaos.documents.storage import ContentBlobStore
from aaos.inference import ChatRequest, ChatResponse, ModelAvailability
from aaos.moodle.file_download import BrowserAuthenticatedFetcher
from aaos.moodle.integration import sync_moodle_snapshot
from aaos.persistence.academic_models import CourseORM, RemoteFileORM
from aaos.persistence.database import create_database_engine, create_session_factory
from aaos.persistence.document_models import (
    ContentBlobORM,
    DocumentORM,
    DocumentPageORM,
    DocumentStructureUnitORM,
    DocumentVersionORM,
)
from aaos.persistence.rag_models import (
    RagChunkORM,
    RagChunkSourceSpanORM,
    RagChunkStructureSourceSpanORM,
    RagDocumentIndexORM,
    RagIndexGenerationORM,
)
from aaos.rag.config import RagIndexConfig, resolve_rag_runtime_paths
from aaos.rag.contracts import (
    DenseIndexFactory,
    EmbeddingModelMetadata,
    EmbeddingService,
    GroundedAnswerRequest,
    GroundedModelOutput,
    RagAnswerStatus,
    RagClaim,
    SemanticCoverageDecision,
    SemanticCoverageStatus,
)
from aaos.rag.dense import InMemoryDenseIndexFactory
from aaos.rag.embeddings import FakeEmbeddingService
from aaos.rag.retrieval import SufficiencyDecision
from aaos.rag.runtime import Phase4RagRuntime, build_phase4_runtime

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SYSTEM = "moodle:fixture:offline-step5"
NOW = datetime(2026, 9, 24, 9, tzinfo=timezone.utc)


class _OfflineFetcher(BrowserAuthenticatedFetcher):
    def __init__(self, calls: list[int]) -> None:
        self.calls = calls
        self.pdf = FetchedPdf(
            valid_multipage_pdf(),
            "synthetic-lecture.pdf",
            "application/pdf",
        )
        self.docx = FetchedDocx(
            mixed_structure_docx(),
            "synthetic-notes.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

    def fetch(self, selection: RemoteFileSelection) -> FetchedPdf | FetchedDocx:
        self.calls.append(selection.remote_file_id)
        if selection.source_id == "resource-pdf":
            return self.pdf
        if selection.source_id == "resource-docx":
            return self.docx
        raise AssertionError("unexpected synthetic resource")


class _FetcherSession(AbstractContextManager[BrowserAuthenticatedFetcher]):
    def __init__(self, fetcher: _OfflineFetcher) -> None:
        self.fetcher = fetcher

    def __enter__(self) -> BrowserAuthenticatedFetcher:
        return self.fetcher

    def __exit__(self, *_args: object) -> None:
        return None


class _OfflineEmbeddingService(EmbeddingService):
    def __init__(self, work: dict[str, int]) -> None:
        self._inner = FakeEmbeddingService(dimensions=3)
        self._work = work

    def model_metadata(self) -> EmbeddingModelMetadata:
        return self._inner.model_metadata()

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self._work["document_embeddings"] += len(texts)
        return self._inner.embed_documents(texts)

    def embed_query(self, text: str) -> tuple[float, ...]:
        self._work["query_embeddings"] += 1
        return self._inner.embed_query(text)

    def release_model(self) -> None:
        self._inner.release_model()

    def close(self) -> None:
        self._inner.close()


class _OfflineGateway:
    async def check_availability(self) -> ModelAvailability:
        return ModelAvailability(True, True, "offline-fake", ("offline-fake",), (4096,))

    async def generate(self, _request: ChatRequest) -> ChatResponse:
        raise AssertionError("offline RAG acceptance must use injected deterministic components")


class _OfflineModelLifecycle:
    def ensure_answer_model_unloaded(self) -> None:
        return None

    def ensure_answer_model_loaded(self) -> None:
        return None

    def close(self) -> None:
        return None


class _SupportedCoverage:
    async def classify(self, request: GroundedAnswerRequest) -> SemanticCoverageDecision:
        return SemanticCoverageDecision(
            SemanticCoverageStatus.SUPPORTED,
            (),
            request.generation_id,
            request.content_revision,
            request.policy_version,
        )


class _EvidenceAnswerGenerator:
    async def generate(
        self,
        request: GroundedAnswerRequest,
        _sufficiency: SufficiencyDecision,
    ) -> GroundedModelOutput:
        evidence = request.evidence[0]
        return GroundedModelOutput(
            status=RagAnswerStatus.ANSWER,
            answer_text="The synthetic source contains grounded material.",
            language="en",
            claims=(
                RagClaim(
                    "claim-1",
                    "The synthetic source contains grounded material.",
                    (evidence.evidence_id,),
                ),
            ),
            generation_id=request.generation_id,
            content_revision=request.content_revision,
            policy_version=request.policy_version,
        )


def _offline_snapshot() -> CompleteSourceSnapshot:
    return CompleteSourceSnapshot(
        source_system=SOURCE_SYSTEM,
        courses=(RawCourse(source_id="course-1", title="Synthetic Systems"),),
        remote_files=(
            RawRemoteFile(
                source_id="resource-pdf",
                course_source_id="course-1",
                title="Synthetic Lecture PDF",
                remote_url="https://moodle.invalid/pluginfile.php/100/lecture.pdf",
            ),
            RawRemoteFile(
                source_id="resource-docx",
                course_source_id="course-1",
                title="Synthetic Notes DOCX",
                remote_url="https://moodle.invalid/pluginfile.php/101/notes.docx",
            ),
        ),
    )


def test_sync_ingests_and_indexes_page_and_structure_content_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from aaos import cli as app_cli
    from aaos.documents import cli as documents_cli
    from aaos.rag import cli as rag_cli

    data_root = (tmp_path / "aaos-runtime").resolve()
    data_root.mkdir(parents=True)
    database_path = data_root / "database" / "aaos.db"
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("aaos.database_path", str(database_path))
    command.upgrade(config, "head")

    engine = create_database_engine(database_path)
    sessions = create_session_factory(engine)
    fetch_calls: list[int] = []
    fake_fetcher = _OfflineFetcher(fetch_calls)
    document_paths = resolve_document_runtime_paths(data_root)
    rag_paths = resolve_rag_runtime_paths(data_root)
    dense_factory: DenseIndexFactory = InMemoryDenseIndexFactory()
    embedding_work = {"document_embeddings": 0, "query_embeddings": 0}
    rag_config = RagIndexConfig(
        chunk_target_tokens=32,
        chunk_preferred_min_tokens=8,
        chunk_preferred_max_tokens=32,
        chunk_hard_max_tokens=64,
        chunk_overlap_tokens=4,
        dense_candidate_limit=10,
        lexical_candidate_limit=10,
        rrf_k=60,
        max_evidence_chunks=4,
        max_evidence_tokens=128,
        chunker_version="offline-step5-v1",
        lexical_normalizer_version="unicode-v1",
        sufficiency_policy_version="offline-step5-v1",
        embedding_model_identifier="fake-embedding-service",
        embedding_model_revision="test-only-v1",
        embedding_dimensions=3,
        sufficiency_min_chunks=1,
        sufficiency_min_tokens=1,
        sufficiency_max_fused_rank=10,
    )

    def build_document_runtime(
        bootstrap_config: object,
        settings: object,
        auth_paths: object,
        paths: DocumentRuntimePaths,
    ) -> Phase3ServiceRuntime:
        del bootstrap_config, settings, auth_paths
        assert paths.database_path == database_path
        document_engine = create_database_engine(database_path)
        document_sessions = create_session_factory(document_engine)
        service = Phase3IngestionService(
            repository=DocumentRepository(document_sessions),
            blob_store=ContentBlobStore(document_paths.content_store_root),
            pdf_processor=PdfProcessor(),
            docx_processor=DocxProcessor(),
            ingestion_lock=IngestionLock(document_paths.ingestion_lock_path),
            fetcher_factory=lambda: _FetcherSession(fake_fetcher),
            clock=lambda: NOW,
        )
        return Phase3ServiceRuntime(service=service, _engine=document_engine)

    def build_rag_runtime() -> Phase4RagRuntime:
        return build_phase4_runtime(
            paths=rag_paths,
            config=rag_config,
            gateway=_OfflineGateway(),
            dense_factory=dense_factory,
            embedding_factory=lambda _config: _OfflineEmbeddingService(embedding_work),
            semantic_classifier_factory=lambda _gateway: _SupportedCoverage(),
            answer_generator_factory=lambda _gateway: _EvidenceAnswerGenerator(),
            model_lifecycle_factory=_OfflineModelLifecycle,
        )

    def run_synthetic_moodle_sync() -> int:
        sync_moodle_snapshot(_offline_snapshot(), session_factory=sessions)
        return 0

    environment = SimpleNamespace(
        data_root=data_root,
        bootstrap_config=None,
        settings=SimpleNamespace(source_system=SOURCE_SYSTEM),
        auth_paths=None,
    )
    monkeypatch.setattr(app_cli, "_run_configured_moodle_sync", run_synthetic_moodle_sync)
    monkeypatch.setattr(documents_cli, "load_runtime_moodle_environment", lambda: environment)
    monkeypatch.setattr(documents_cli, "_normal_database_path", lambda: database_path)
    monkeypatch.setattr(documents_cli, "build_phase3_service", build_document_runtime)
    monkeypatch.setattr(rag_cli, "_build_configured_runtime", build_rag_runtime)
    monkeypatch.setattr(sys, "argv", ["aaos", "sync"])

    try:
        first_exit = app_cli.main()
        first_output = capsys.readouterr()

        assert first_exit == 0, first_output.err
        assert "INGESTED=2" in first_output.out
        assert "STATUS=PASS" in first_output.out
        assert len(fetch_calls) == 2

        with sessions() as session:
            assert session.scalar(select(func.count()).select_from(CourseORM)) == 1
            assert session.scalar(select(func.count()).select_from(RemoteFileORM)) == 2
            assert session.scalar(select(func.count()).select_from(DocumentORM)) == 2
            versions = tuple(
                session.scalars(select(DocumentVersionORM).order_by(DocumentVersionORM.id))
            )
            assert len(versions) == 2
            page_version = next(item for item in versions if item.provenance_mode == "PAGE")
            structure_version = next(
                item for item in versions if item.provenance_mode == "STRUCTURE"
            )
            assert page_version.page_count == 3
            assert structure_version.page_count is None
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(DocumentPageORM)
                    .where(DocumentPageORM.document_version_id == page_version.id)
                )
                == 3
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(DocumentPageORM)
                    .where(DocumentPageORM.document_version_id == structure_version.id)
                )
                == 0
            )
            initial_structure_unit_count = session.scalar(
                select(func.count())
                .select_from(DocumentStructureUnitORM)
                .where(DocumentStructureUnitORM.document_version_id == structure_version.id)
            )
            assert initial_structure_unit_count is not None
            assert initial_structure_unit_count > 0

            generation = session.scalar(
                select(RagIndexGenerationORM).where(RagIndexGenerationORM.status == "ACTIVE")
            )
            assert generation is not None
            chunks = tuple(
                session.scalars(
                    select(RagChunkORM).where(RagChunkORM.generation_id == generation.id)
                )
            )
            assert {chunk.provenance_mode for chunk in chunks} == {"PAGE", "STRUCTURE"}
            page_chunks = tuple(chunk for chunk in chunks if chunk.provenance_mode == "PAGE")
            structure_chunks = tuple(
                chunk for chunk in chunks if chunk.provenance_mode == "STRUCTURE"
            )
            assert page_chunks and structure_chunks
            assert all(
                chunk.page_start is not None and chunk.page_end is not None for chunk in page_chunks
            )
            assert all(
                chunk.page_start is None and chunk.page_end is None for chunk in structure_chunks
            )
            initial_page_span_count = session.scalar(
                select(func.count())
                .select_from(RagChunkSourceSpanORM)
                .where(RagChunkSourceSpanORM.rag_chunk_id.in_(chunk.id for chunk in page_chunks))
            )
            initial_structure_span_count = session.scalar(
                select(func.count())
                .select_from(RagChunkStructureSourceSpanORM)
                .where(
                    RagChunkStructureSourceSpanORM.rag_chunk_id.in_(
                        chunk.id for chunk in structure_chunks
                    )
                )
            )
            assert initial_page_span_count is not None
            assert initial_page_span_count > 0
            assert initial_structure_span_count is not None
            assert initial_structure_span_count > 0
            indexes = tuple(
                session.scalars(
                    select(RagDocumentIndexORM).where(
                        RagDocumentIndexORM.generation_id == generation.id
                    )
                )
            )
            assert {item.document_version_id: item.status for item in indexes} == {
                page_version.id: "READY",
                structure_version.id: "READY",
            }
            initial_page_count = session.scalar(select(func.count()).select_from(DocumentPageORM))
            initial_chunk_count = len(chunks)
            initial_generation_count = session.scalar(
                select(func.count()).select_from(RagIndexGenerationORM)
            )
            page_version_id = int(page_version.id)
            structure_version_id = int(structure_version.id)
            generation_id = int(generation.id)
            initial_version_hashes = tuple(
                session.scalar(
                    select(ContentBlobORM.sha256).where(ContentBlobORM.id == item.content_blob_id)
                )
                for item in versions
            )
            initial_document_embeddings = embedding_work["document_embeddings"]

        def ask(version_id: int, question: str) -> str:
            stdout = io.StringIO()
            stderr = io.StringIO()
            assert (
                rag_cli.run_rag_command(
                    ["ask", "--document-version-id", str(version_id)],
                    build_runtime=build_rag_runtime,
                    stdin=io.StringIO(f"{question}\n"),
                    stdout=stdout,
                    stderr=stderr,
                )
                == 0
            ), stderr.getvalue()
            return stdout.getvalue()

        initial_pdf_answer = ask(page_version_id, "What does the PDF cover?")
        assert any(f"(page {number})" in initial_pdf_answer for number in (1, 2, 3))
        initial_docx_answer = ask(structure_version_id, "What do the notes contain?")
        assert "unit" in initial_docx_answer
        assert "page " not in initial_docx_answer

        second_exit = app_cli.main()
        second_output = capsys.readouterr()
        assert second_exit == 0, second_output.err
        assert "UNCHANGED=2" in second_output.out or "SKIPPED=2" in second_output.out
        assert "STATUS=NO_OP" in second_output.out
        assert len(fetch_calls) == 2
        assert embedding_work["document_embeddings"] == initial_document_embeddings

        with sessions() as session:
            assert session.scalar(select(func.count()).select_from(CourseORM)) == 1
            assert session.scalar(select(func.count()).select_from(RemoteFileORM)) == 2
            assert session.scalar(select(func.count()).select_from(DocumentORM)) == 2
            versions_after = tuple(
                session.scalars(select(DocumentVersionORM).order_by(DocumentVersionORM.id))
            )
            assert len(versions_after) == 2
            version_hashes_after = tuple(
                session.scalar(
                    select(ContentBlobORM.sha256).where(ContentBlobORM.id == item.content_blob_id)
                )
                for item in versions_after
            )
            assert version_hashes_after == initial_version_hashes
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(DocumentPageORM)
                    .where(DocumentPageORM.document_version_id == structure_version_id)
                )
                == 0
            )
            assert (
                session.scalar(select(func.count()).select_from(DocumentPageORM))
                == initial_page_count
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(DocumentStructureUnitORM)
                    .where(DocumentStructureUnitORM.document_version_id == structure_version_id)
                )
                == initial_structure_unit_count
            )
            active_generations = tuple(
                session.scalars(
                    select(RagIndexGenerationORM).where(RagIndexGenerationORM.status == "ACTIVE")
                )
            )
            assert len(active_generations) == 1
            assert active_generations[0].id == generation_id
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(RagChunkORM)
                    .where(RagChunkORM.generation_id == generation_id)
                )
                == initial_chunk_count
            )
            assert (
                session.scalar(select(func.count()).select_from(RagIndexGenerationORM))
                == initial_generation_count
            )
            indexes_after = tuple(
                session.scalars(
                    select(RagDocumentIndexORM).where(
                        RagDocumentIndexORM.generation_id == generation_id
                    )
                )
            )
            assert {item.document_version_id: item.status for item in indexes_after} == {
                page_version_id: "READY",
                structure_version_id: "READY",
            }
            page_chunks_after = select(RagChunkORM.id).where(
                RagChunkORM.generation_id == generation_id,
                RagChunkORM.provenance_mode == "PAGE",
            )
            structure_chunks_after = select(RagChunkORM.id).where(
                RagChunkORM.generation_id == generation_id,
                RagChunkORM.provenance_mode == "STRUCTURE",
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(RagChunkSourceSpanORM)
                    .where(RagChunkSourceSpanORM.rag_chunk_id.in_(page_chunks_after))
                )
                == initial_page_span_count
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(RagChunkStructureSourceSpanORM)
                    .where(RagChunkStructureSourceSpanORM.rag_chunk_id.in_(structure_chunks_after))
                )
                == initial_structure_span_count
            )

        assert ask(page_version_id, "What does the PDF cover?") == initial_pdf_answer
        assert ask(structure_version_id, "What do the notes contain?") == initial_docx_answer

    finally:
        engine.dispose()
