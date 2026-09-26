from __future__ import annotations

import aaos.web.contracts as web_contracts
from aaos.rag.contracts import (
    ProvenanceKind,
    StructuralCitation,
)
from aaos.web.rag import RagReadService


class _Rows:
    def all(self) -> list[tuple[int, int, str]]:
        return [
            (
                101,
                55,
                "notes.docx",
            )
        ]


class _Session:
    def __enter__(self) -> "_Session":
        return self

    def __exit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        return None

    def execute(self, statement: object) -> _Rows:
        del statement
        return _Rows()


def _sessions() -> _Session:
    return _Session()


def _citation() -> StructuralCitation:
    return StructuralCitation(
        claim_id="claim-struct",
        document_version_id=101,
        document_title="notes.docx",
        unit_start=4,
        unit_end=4,
        heading="Neural Networks",
        table_index=None,
        row_index=None,
        column_index=None,
        provenance_kind=ProvenanceKind.STRUCTURAL_DOCUMENT,
    )


def test_web_structural_citation_contract_is_page_free() -> None:
    citation_type = getattr(
        web_contracts,
        "RagStructuralCitation",
        None,
    )

    assert citation_type is not None

    model_fields = set(citation_type.model_fields)

    assert model_fields == {
        "document_id",
        "document_title",
        "unit_start",
        "unit_end",
        "heading",
        "table_index",
        "row_index",
        "column_index",
    }

    assert "page_start" not in model_fields
    assert "page_end" not in model_fields


def test_web_projection_projects_structural_citation_without_pages() -> None:
    service = object.__new__(RagReadService)
    service._sessions = _sessions  # type: ignore[attr-defined]

    projected = service._project_citations((_citation(),))

    assert len(projected) == 1

    citation = projected[0]

    structural_type = getattr(
        web_contracts,
        "RagStructuralCitation",
        None,
    )

    assert structural_type is not None
    assert isinstance(citation, structural_type)

    assert citation.document_id == 55
    assert citation.document_title == "notes.docx"
    assert citation.unit_start == 4
    assert citation.unit_end == 4
    assert citation.heading == "Neural Networks"

    assert citation.table_index is None
    assert citation.row_index is None
    assert citation.column_index is None

    assert not hasattr(citation, "page_start")
    assert not hasattr(citation, "page_end")
