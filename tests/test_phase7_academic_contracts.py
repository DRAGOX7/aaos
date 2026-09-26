from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from aaos.web.contracts import (
    AssignmentListResponse,
    AssignmentSummary,
    DocumentExtractionStatusView,
    DocumentIndexCoverageView,
    DocumentIndexReadinessView,
    DocumentIngestionStatusView,
    DocumentListResponse,
    DocumentSummary,
)

UTC = timezone.utc
UPDATED_AT = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


def _assignment(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": 7,
        "course_id": 11,
        "title": "Read chapter",
        "description": "Review the assigned chapter.",
        "due_at": UPDATED_AT,
        "updated_at": UPDATED_AT,
    }
    values.update(overrides)
    return values


def _document(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": 31,
        "course_id": 11,
        "title": "Syllabus",
        "ingestion_status": DocumentIngestionStatusView.READY,
        "extraction_status": DocumentExtractionStatusView.SUCCESS,
        "page_count": 4,
        "index_readiness": DocumentIndexReadinessView.READY,
        "index_coverage": DocumentIndexCoverageView.FULL,
        "updated_at": UPDATED_AT,
    }
    values.update(overrides)
    return values


def test_assignment_summary_has_exact_closed_field_set() -> None:
    assert set(AssignmentSummary.model_fields) == {
        "id",
        "course_id",
        "title",
        "description",
        "due_at",
        "updated_at",
    }

    assignment = AssignmentSummary.model_validate(_assignment())
    assert assignment.id == 7
    assert assignment.course_id == 11
    assert assignment.due_at == UPDATED_AT
    assert assignment.updated_at == UPDATED_AT

    with pytest.raises(ValidationError):
        assignment.title = "mutated"


def test_assignment_list_response_is_a_tuple_of_safe_assignments() -> None:
    assignment = AssignmentSummary.model_validate(_assignment())

    response = AssignmentListResponse(assignments=(assignment,))

    assert response.assignments == (assignment,)
    assert isinstance(response.assignments, tuple)


def test_document_status_enums_match_existing_persistence_values() -> None:
    assert [status.value for status in DocumentIngestionStatusView] == [
        "NEVER_ATTEMPTED",
        "READY",
        "FAILED",
    ]
    assert [status.value for status in DocumentExtractionStatusView] == [
        "SUCCESS",
        "PARTIAL",
        "NO_TEXT",
    ]
    assert [status.value for status in DocumentIndexReadinessView] == [
        "PREPARING",
        "READY",
        "NOT_SEARCHABLE",
        "FAILED",
    ]
    assert [status.value for status in DocumentIndexCoverageView] == [
        "FULL",
        "PARTIAL",
        "NONE",
    ]


def test_document_summary_has_exact_closed_field_set() -> None:
    assert set(DocumentSummary.model_fields) == {
        "id",
        "course_id",
        "title",
        "ingestion_status",
        "extraction_status",
        "page_count",
        "index_readiness",
        "index_coverage",
        "updated_at",
    }

    document = DocumentSummary.model_validate(_document())

    assert document.id == 31
    assert document.course_id == 11
    assert document.page_count == 4
    assert document.updated_at == UPDATED_AT

    with pytest.raises(ValidationError):
        document.title = "mutated"


def test_document_list_response_is_a_tuple_of_safe_documents() -> None:
    document = DocumentSummary.model_validate(_document())

    response = DocumentListResponse(documents=(document,))

    assert set(DocumentListResponse.model_fields) == {"documents"}
    assert response.documents == (document,)
    assert isinstance(response.documents, tuple)

    with pytest.raises(ValidationError):
        DocumentListResponse.model_validate({"documents": (document,), "permission": "READ_ONLY"})

    with pytest.raises(ValidationError):
        response.documents = ()


@pytest.mark.parametrize(
    ("contract", "payload", "field"),
    (
        (AssignmentSummary, _assignment(id="7"), "id"),
        (AssignmentSummary, _assignment(course_id="11"), "course_id"),
        (AssignmentSummary, _assignment(title=7), "title"),
        (AssignmentSummary, _assignment(due_at="2026-09-17T12:00:00Z"), "due_at"),
        (DocumentSummary, _document(id="31"), "id"),
        (DocumentSummary, _document(course_id="11"), "course_id"),
        (DocumentSummary, _document(page_count="4"), "page_count"),
        (DocumentSummary, _document(updated_at="2026-09-17T12:00:00Z"), "updated_at"),
    ),
)
def test_academic_contracts_reject_non_strict_field_types(
    contract: type[AssignmentSummary] | type[DocumentSummary],
    payload: dict[str, object],
    field: str,
) -> None:
    with pytest.raises(ValidationError, match=field):
        contract.model_validate(payload)


@pytest.mark.parametrize(
    "contract,payload",
    (
        (AssignmentSummary, _assignment(permission="READ_ONLY")),
        (AssignmentSummary, _assignment(tool_name="rag_query")),
        (AssignmentSummary, _assignment(source_id="private-source-id")),
        (DocumentSummary, _document(storage_key="sha256/private.pdf")),
        (DocumentSummary, _document(local_path="C:/private/document.pdf")),
        (DocumentSummary, _document(raw_sql="SELECT * FROM documents")),
        (DocumentSummary, _document(provider_payload={"secret": "value"})),
    ),
)
def test_academic_contracts_reject_unknown_or_authority_fields(
    contract: type[AssignmentSummary] | type[DocumentSummary],
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        contract.model_validate(payload)


def test_document_contracts_allow_absent_persisted_state_without_new_statuses() -> None:
    document = DocumentSummary.model_validate(
        _document(
            extraction_status=None,
            index_readiness=None,
            index_coverage=None,
            page_count=None,
        )
    )

    assert document.extraction_status is None
    assert document.index_readiness is None
    assert document.index_coverage is None
    assert document.page_count is None


def test_document_status_fields_reject_unsupported_values() -> None:
    with pytest.raises(ValidationError):
        DocumentSummary.model_validate(_document(ingestion_status="UNKNOWN"))

    with pytest.raises(ValidationError):
        DocumentSummary.model_validate(_document(index_readiness="AVAILABLE"))
