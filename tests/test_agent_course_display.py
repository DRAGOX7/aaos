"""Readable course names and Arabic dates in answer text."""

from __future__ import annotations

from datetime import date

import pytest

from aaos.agent.course_names import arabic_date, course_display_title


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("AI380-OPTIMIZATION ALGORITHMS (All Sections )", "AI380 · Optimization Algorithms"),
        ("AI380-OPTIMIZATION ALGORITHMS (1)", "AI380 · Optimization Algorithms"),
        ("AI491-GRADUATION PROJECT (1) (1)", "AI491 · Graduation Project (1)"),
        (
            "HI420-APPLICATIONS OF ARTIFICIAL INTELLIGENCE IN HEALTHCARE (All Sections )",
            "HI420 · Applications of Artificial Intelligence in Healthcare",
        ),
        (
            "AI495C-SPECIAL TOPICS IN ARTIFICIAL INTELLIGENCE (3) (1)",
            "AI495C · Special Topics in Artificial Intelligence (3)",
        ),
        (
            "HSS100O.T-DISABILITY AND THE SOCIETY(NOT ALLOWED FOR STUDENTS) (All Sections )",
            "HSS100O.T · Disability and the Society (Not Allowed for Students)",
        ),
        (
            "SE440-PROJECT MANAGEMENT (All Sections ) (past course)",
            "SE440 · Project Management (past course)",
        ),
        ("Deep Learning", "Deep Learning"),
        ("Operating Systems (past course)", "Operating Systems (past course)"),
    ],
)
def test_course_titles_read_as_code_and_name(title: str, expected: str) -> None:
    assert course_display_title(title) == expected


def test_arabic_dates_use_levantine_month_names() -> None:
    assert arabic_date(date(2026, 10, 2)) == "2 تشرين الأول 2026"
    assert arabic_date(date(2026, 1, 15)) == "15 كانون الثاني 2026"
