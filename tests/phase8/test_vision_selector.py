from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from aaos.documents.contracts import ExtractedPage, ExtractionStatus, ParsedPdf, TextSource
from aaos.persistence.academic_models import Base, CourseORM, RemoteFileORM
from aaos.persistence.database import create_database_engine, create_session_factory
from aaos.persistence.document_models import ContentBlobORM, DocumentORM, DocumentVersionORM
from aaos.phase8.vision_contracts import VisualSelectorPolicy


def _parsed(*texts: str, page_count: int | None = None) -> ParsedPdf:
    pages = tuple(
        ExtractedPage(index, text, TextSource.EMBEDDED_PDF, 612.0, 792.0)
        for index, text in enumerate(texts, start=1)
    )
    return ParsedPdf(
        page_count=len(pages) if page_count is None else page_count,
        extraction_status=ExtractionStatus.PARTIAL,
        pages=pages,
    )


def _policy(max_pages: int = 2) -> VisualSelectorPolicy:
    return VisualSelectorPolicy(
        max_pages=max_pages, max_render_bytes=100_000, selector_version="v1"
    )


def test_ordinary_dense_text_page_is_not_selected() -> None:
    from aaos.phase8.vision_selector import select_visual_pages

    parsed = _parsed("ordinary academic prose " * 500)

    assert select_visual_pages(parsed, selector_policy=_policy()) == ()


def test_sparse_and_empty_pages_are_selected_deterministically_and_budgeted() -> None:
    from aaos.phase8.vision_selector import select_visual_pages

    parsed = _parsed("", "short label", "ordinary academic prose " * 500)
    first = select_visual_pages(parsed, selector_policy=_policy(max_pages=2))
    second = select_visual_pages(parsed, selector_policy=_policy(max_pages=2))

    assert first == second
    assert len(first) == 2
    assert tuple(item.page_number for item in first) == (1, 2)
    assert all(item.document_version_id == 0 for item in first)
    assert all(item.estimated_bytes > 0 for item in first)
    assert all(item.selector_version == "v1" for item in first)


def test_malformed_page_metadata_is_rejected_without_out_of_range_candidates() -> None:
    from aaos.phase8.vision_selector import select_visual_pages

    parsed = _parsed("", page_count=3)

    with pytest.raises(ValueError, match="page count"):
        select_visual_pages(parsed, selector_policy=_policy())


def test_selector_rejects_invalid_policy() -> None:
    from aaos.phase8.vision_contracts import VisualSelectorPolicy

    with pytest.raises(ValueError):
        VisualSelectorPolicy(max_pages=0, max_render_bytes=100, selector_version="v1")


def test_selector_identity_requires_trusted_positive_document_version_binding() -> None:
    from aaos.phase8.vision_contracts import (
        TrustedDocumentVersionBinding,
        VisualPageCandidate,
    )
    from aaos.phase8.vision_selector import (
        bind_visual_page_candidate,
        select_visual_pages,
        trusted_document_version_binding,
    )

    candidate = select_visual_pages(_parsed(""), selector_policy=_policy())[0]
    assert candidate.document_version_id == 0

    with pytest.raises(TypeError):
        TrustedDocumentVersionBinding(7)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        trusted_document_version_binding(7)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="positive"):
        TrustedDocumentVersionBinding._from_persistence(0)
    with pytest.raises(ValueError, match="positive"):
        TrustedDocumentVersionBinding._from_persistence(-1)
    with pytest.raises(ValueError, match="trusted"):
        VisualPageCandidate(7, 1, "EMPTY_PAGE", 1024, "v1")

    bound = bind_visual_page_candidate(
        candidate,
        document_version=TrustedDocumentVersionBinding._from_persistence(7),
    )
    assert bound.document_version_id == 7
    assert bound.page_number == candidate.page_number

    with pytest.raises(ValueError, match="unbound"):
        bind_visual_page_candidate(
            bound,
            document_version=TrustedDocumentVersionBinding._from_persistence(8),
        )


def test_trusted_binding_requires_persistent_document_version(tmp_path: Path) -> None:
    from aaos.phase8.vision_selector import trusted_document_version_binding

    engine: Engine = create_database_engine(tmp_path / "vision-binding.db")
    Base.metadata.create_all(engine)
    sessions: sessionmaker[Session] = create_session_factory(engine)
    now = datetime(2026, 9, 19, tzinfo=timezone.utc)
    with sessions() as session, session.begin():
        session.add(
            CourseORM(
                id=1,
                source_system="test",
                source_id="course-1",
                title="Course 1",
                is_active=True,
            )
        )
        session.add(
            RemoteFileORM(
                id=1,
                course_id=1,
                source_system="test",
                source_id="file-1",
                title="Document 1",
                is_active=True,
            )
        )
        session.add(
            ContentBlobORM(
                id=1,
                sha256="a" * 64,
                size_bytes=1,
                media_type="application/pdf",
                storage_key="sha256/aa/" + "a" * 64 + ".pdf",
                created_at=now,
            )
        )
        session.add(
            DocumentORM(
                id=1,
                remote_file_id=1,
                ingestion_status="READY",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            DocumentVersionORM(
                id=7,
                document_id=1,
                version_number=1,
                content_blob_id=1,
                source_filename="document.pdf",
                ingested_at=now,
                extraction_status="SUCCESS",
                page_count=1,
            )
        )
        session.flush()
        version = session.get(DocumentVersionORM, 7)
        assert version is not None
        binding = trusted_document_version_binding(version)
        assert binding.document_version_id == 7

        version.source_filename = "mutated.pdf"
        with pytest.raises(ValueError, match="stable"):
            trusted_document_version_binding(version)

        with pytest.raises(ValueError, match="persistent"):
            trusted_document_version_binding(DocumentVersionORM(id=8))
    engine.dispose()
