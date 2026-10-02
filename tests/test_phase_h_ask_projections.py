from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from aaos.agent.academic_orchestrator import _sources_for_evidence
from aaos.agent.contracts import ToolName
from aaos.web.contracts import (
    AcademicAskRagAnswerProvenanceView,
    AcademicAskRagProvenanceSummaryView,
    AcademicAskResponse,
    AcademicAskSource,
    AcademicAskStatusView,
)


def test_ask_response_validates_page_and_structural_rag_citations() -> None:
    response = AcademicAskResponse(
        status=AcademicAskStatusView.ANSWERED,
        step_count=1,
        rag_provenance={
            "answer_text": "Grounded answer",
            "citations": (
                {
                    "claim_id": "claim-1",
                    "document_version_id": 12,
                    "document_title": "Algorithms",
                    "page_start": 2,
                    "page_end": 3,
                },
                {
                    "claim_id": "claim-2",
                    "document_version_id": 15,
                    "document_title": "Lecture notes",
                    "unit_start": 4,
                    "unit_end": 4,
                    "heading": "Graphs",
                    "table_index": 1,
                    "row_index": 2,
                    "column_index": 0,
                },
            ),
            "content_revision": 3,
            "generation_id": 7,
            "language": "en",
            "rendered_text": "Grounded answer",
            "status": "ANSWER",
        },
    )

    assert isinstance(response.rag_provenance, AcademicAskRagAnswerProvenanceView)
    assert response.rag_provenance.citations[0].document_title == "Algorithms"
    assert response.rag_provenance.citations[1].unit_start == 4


def test_ask_rag_provenance_summary_is_typed_for_study_plans() -> None:
    response = AcademicAskResponse(
        status=AcademicAskStatusView.ANSWERED,
        step_count=1,
        rag_provenance={
            "generation_id": 4,
            "content_revision": 2,
            "citation_count": 1,
        },
    )

    assert isinstance(response.rag_provenance, AcademicAskRagProvenanceSummaryView)


@pytest.mark.parametrize(
    "citation",
    [
        {
            "claim_id": "claim-1",
            "document_version_id": 12,
            "document_title": "Algorithms",
            "page_start": 5,
            "page_end": 3,
        },
        {
            "claim_id": "claim-1",
            "document_version_id": 12,
            "document_title": "Algorithms",
            "unit_start": 1,
            "unit_end": 2,
            "heading": None,
            "table_index": 1,
            "row_index": None,
            "column_index": 0,
        },
        {
            "claim_id": "claim-1",
            "document_version_id": 12,
            "document_title": "Algorithms",
            "unit_start": 1,
            "unit_end": 2,
            "heading": None,
            "table_index": 1,
            "row_index": 2,
            "column_index": 0,
            "source_url": "https://untrusted.example/",
        },
    ],
)
def test_ask_rag_citation_rejects_invalid_ranges_coordinates_and_extra_fields(
    citation: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        AcademicAskResponse(
            status=AcademicAskStatusView.ANSWERED,
            step_count=1,
            rag_provenance={
                "answer_text": "Grounded answer",
                "citations": (citation,),
                "content_revision": 3,
                "generation_id": 7,
                "language": "en",
                "rendered_text": "Grounded answer",
                "status": "ANSWER",
            },
        )


def test_study_task_source_contains_only_persisted_browser_safe_fields() -> None:
    due_at = datetime(2026, 10, 1, 12, tzinfo=timezone.utc).isoformat()
    source = _sources_for_evidence(
        [
            (
                ToolName.STUDY_TASK_LIST,
                {
                    "filter": {"status": "OPEN"},
                    "source_ref": "read-1",
                    "records": [
                        {
                            "id": 41,
                            "title": "Review graph algorithms",
                            "status": "OPEN",
                            "priority": "HIGH",
                            "course_id": 5,
                            "course_title": "Algorithms",
                            "assignment_id": 8,
                            "assignment_title": "Homework 2",
                            "due_at": due_at,
                            "created_at": due_at,
                            "updated_at": due_at,
                        }
                    ],
                },
            )
        ],
        source_turn=1,
    )[0]

    view = AcademicAskSource.model_validate(
        {
            "tool_name": source.tool_name,
            "entity_kind": source.entity_kind,
            "entity_id": source.entity_id,
            "title": source.title,
            "study_task_status": source.study_task_status,
            "study_task_priority": source.study_task_priority,
            "study_task_due_at": source.study_task_due_at,
            "study_task_course_id": source.study_task_course_id,
            "study_task_course_title": source.study_task_course_title,
            "study_task_assignment_id": source.study_task_assignment_id,
            "study_task_assignment_title": source.study_task_assignment_title,
        }
    )

    assert view.study_task_status == "OPEN"
    assert view.study_task_priority == "HIGH"
    assert view.study_task_due_at == datetime.fromisoformat(due_at)
    assert view.study_task_assignment_title == "Homework 2"


def test_latest_study_task_mutation_replaces_earlier_read_provenance() -> None:
    sources = _sources_for_evidence(
        [
            (
                ToolName.STUDY_TASK_COMPLETE,
                {
                    "filter": {},
                    "source_ref": "read-2",
                    "records": [{"id": 41, "title": "Review graphs", "status": "COMPLETED", "priority": "HIGH"}],
                },
            ),
            (
                ToolName.STUDY_TASK_LIST,
                {
                    "filter": {},
                    "source_ref": "read-1",
                    "records": [{"id": 41, "title": "Review graphs", "status": "OPEN", "priority": "HIGH"}],
                },
            ),
        ],
        source_turn=2,
    )

    assert len(sources) == 1
    assert sources[0].tool_name == ToolName.STUDY_TASK_COMPLETE.value
    assert sources[0].study_task_status == "COMPLETED"


def test_moodle_changes_for_one_entity_keep_distinct_change_provenance() -> None:
    sources = _sources_for_evidence(
        [
            (
                ToolName.MOODLE_LIST_CHANGES,
                {
                    "filter": {},
                    "source_ref": "read-1",
                    "from_snapshot_id": 12,
                    "to_snapshot_id": 13,
                    "records": [{
                        "id": 52,
                        "change_id": "assignment-52-change-1",
                        "entity_type": "ASSIGNMENT",
                        "change_kind": "MODIFIED",
                        "title": "Assignment 2",
                        "changed_fields": ["due_at"],
                    }],
                },
            ),
            (
                ToolName.MOODLE_LIST_CHANGES,
                {
                    "filter": {},
                    "source_ref": "read-2",
                    "from_snapshot_id": 13,
                    "to_snapshot_id": 14,
                    "records": [{
                        "id": 52,
                        "change_id": "assignment-52-change-2",
                        "entity_type": "ASSIGNMENT",
                        "change_kind": "MODIFIED",
                        "title": "Assignment 2",
                        "changed_fields": ["due_at"],
                    }],
                },
            ),
        ],
        source_turn=3,
    )

    assert len(sources) == 2
    assert [(source.change_id, source.from_snapshot_id, source.to_snapshot_id) for source in sources] == [
        ("assignment-52-change-1", 12, 13),
        ("assignment-52-change-2", 13, 14),
    ]


def test_moodle_change_source_contains_validated_change_kind_and_due_values() -> None:
    before_due_at = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    after_due_at = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
    source = _sources_for_evidence(
        [
            (
                ToolName.MOODLE_LIST_CHANGES,
                {
                    "comparison_status": "AVAILABLE",
                    "unavailable_reason": None,
                    "from_snapshot_id": 12,
                    "to_snapshot_id": 13,
                    "changed_courses_count": 1,
                    "added_count": 0,
                    "removed_count": 0,
                    "modified_count": 1,
                    "total_change_count": 1,
                    "records": [
                        {
                            "id": 52,
                            "change_id": "change-52",
                            "entity_type": "ASSIGNMENT",
                            "change_kind": "MODIFIED",
                            "title": "Algorithms Assignment 2",
                            "changed_fields": ["due_at"],
                            "before_due_at": before_due_at.isoformat(),
                            "after_due_at": after_due_at.isoformat(),
                        }
                    ],
                    "offset": 0,
                    "limit": 50,
                    "has_more": False,
                    "filter": {},
                    "source_ref": "read-1",
                },
            )
        ],
        source_turn=1,
    )[0]

    view = AcademicAskSource.model_validate(
        {
            "tool_name": source.tool_name,
            "entity_kind": source.entity_kind,
            "entity_id": source.entity_id,
            "title": source.title,
            "change_id": source.change_id,
            "from_snapshot_id": source.from_snapshot_id,
            "to_snapshot_id": source.to_snapshot_id,
            "changed_fields": source.changed_fields,
            "change_kind": source.change_kind,
            "before_due_at": source.before_due_at,
            "after_due_at": source.after_due_at,
        }
    )

    assert view.change_kind == "MODIFIED"
    assert view.changed_fields == ("due_at",)
    assert view.before_due_at == before_due_at
    assert view.after_due_at == after_due_at


def test_ask_source_rejects_changed_fields_outside_the_moodle_contract() -> None:
    with pytest.raises(ValidationError):
        AcademicAskSource(
            tool_name="moodle.list_changes",
            entity_kind="moodle_assignment_change",
            entity_id=52,
            changed_fields=("arbitrary_internal_field",),
        )

@pytest.mark.parametrize(
    "tool_name,record",
    [
        (
            ToolName.MOODLE_LIST_CHANGES,
            {
                "id": 52,
                "entity_type": "ASSIGNMENT",
                "change_kind": "MODIFIED",
                "changed_fields": ["private_internal_field"],
            },
        ),
        (
            ToolName.MOODLE_LIST_CHANGES,
            {
                "id": 52,
                "entity_type": "ASSIGNMENT",
                "change_kind": "UNKNOWN",
                "changed_fields": ["due_at"],
            },
        ),
        (ToolName.STUDY_TASK_LIST, {"id": 41, "status": "DELETED"}),
        (ToolName.STUDY_TASK_LIST, {"id": 41, "priority": "URGENT"}),
    ],
)
def test_ask_source_projection_rejects_values_outside_browser_contract(
    tool_name: ToolName,
    record: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        _sources_for_evidence(
            [
                (
                    tool_name,
                    {
                        "filter": {},
                        "source_ref": "read-1",
                        "records": [record],
                    },
                )
            ],
            source_turn=1,
        )


def test_rag_provenance_carries_the_past_course_label() -> None:
    from aaos.web.routes import _academic_ask_rag_provenance_view

    payload = {
        "answer_text": "Grounded answer",
        "citations": [
            {
                "claim_id": "claim-1",
                "document_version_id": 12,
                "document_title": "Syllabus",
                "page_start": 1,
                "page_end": 1,
            }
        ],
        "content_revision": 3,
        "generation_id": 7,
        "language": "en",
        "rendered_text": "Grounded answer",
        "status": "ANSWER",
    }

    current = _academic_ask_rag_provenance_view(payload)
    past = _academic_ask_rag_provenance_view({**payload, "from_past_course": True})

    assert isinstance(current, AcademicAskRagAnswerProvenanceView)
    assert current.from_past_course is False
    assert isinstance(past, AcademicAskRagAnswerProvenanceView)
    assert past.from_past_course is True
