"""Table-row support for claims the NLI cannot read (plan 2026-10-02-table-claim-check)."""

from __future__ import annotations

import pytest

from aaos.rag.table_claims import table_row_supports

# Extracted text of the two real tables, exactly as the index stores it.
SE440_TABLE = (
    "Mini-Credential – CISCO \r\nCertification (10%)\r\nFirst Exam (25%)\r\nChapters 2-3\r\n"
    "Second Exam (25%)\r\nChapters 3-4\r\nFinal Exam \r\n(40%) Final Part#2\r\nInternal PSM "
    "\r\nCertification Exam (20%)\r\nFinal Part#1 (20%)\r\nChapters 3-5\r\n"
    "CLO1. Understand the Basic Terms and Concepts \r\nof Software Project Management. 8%"
)
SE440_TITLE = "SE440 SPM - Syllabus - S3 2025-26 V3.pdf"
KNIME_TABLE = (
    "Criterion Weight\r\nDataset selection and understanding 10%\r\n"
    "Data cleaning and preprocessing 15%\r\nEDA and data understanding 10%\r\n"
    "Machine learning implementation 20%\r\nModel testing and evaluation 15%\r\n"
    "Experiments, comparison, and improvement 15%\r\n"
    "KNIME workflow organization and clarity 10%\r\nReport quality and explanation 5%\r\n"
    "Total 100%\r\nImportant Note"
)
KNIME_TITLE = "A STAI Course mini-project - DA and ML in KNIME .pdf"


@pytest.mark.parametrize(
    ("claim", "chunk", "title"),
    [
        ("The final exam is worth 40% of the SE440 grade.", SE440_TABLE, SE440_TITLE),
        (
            "The machine learning implementation component is worth 20% of the project grade.",
            KNIME_TABLE,
            KNIME_TITLE,
        ),
        (
            "Machine learning implementation is worth 20% of the project grade.",
            KNIME_TABLE,
            KNIME_TITLE,
        ),
        ("The first exam accounts for 25% of the grade.", SE440_TABLE, SE440_TITLE),
        ("Report quality and explanation counts for 5%.", KNIME_TABLE, KNIME_TITLE),
        ("The Internal PSM Certification Exam is worth 20%.", SE440_TABLE, SE440_TITLE),
        ("The final exam is worth 40 percent of the grade.", SE440_TABLE, SE440_TITLE),
    ],
)
def test_a_claim_matching_one_table_row_is_supported(claim: str, chunk: str, title: str) -> None:
    assert table_row_supports(claim, (chunk,), (title,)) is True


@pytest.mark.parametrize(
    ("claim", "chunk", "title"),
    [
        # wrong number
        ("The final exam is worth 25% of the SE440 grade.", SE440_TABLE, SE440_TITLE),
        # wrong row
        ("The second exam is worth 40%.", SE440_TABLE, SE440_TITLE),
        # swapped rows
        ("Machine learning implementation is worth 15%.", KNIME_TABLE, KNIME_TITLE),
        # extra assertion
        ("The final exam is worth 40% and is optional.", SE440_TABLE, SE440_TITLE),
        # negation
        ("The final exam is not worth 40%.", SE440_TABLE, SE440_TITLE),
        # label only partly present
        ("The exam is worth 40%.", SE440_TABLE, SE440_TITLE),
        # two numbers
        ("The first and second exams are worth 25% each, 50% in total.", SE440_TABLE, SE440_TITLE),
        # no percentage
        ("The final exam covers chapters 3 to 5.", SE440_TABLE, SE440_TITLE),
        # Arabic claims stay on the NLI path in v1
        ("الامتحان النهائي يساوي 40٪ من العلامة.", SE440_TABLE, SE440_TITLE),
        # abbreviated label fails closed (accepted false negative)
        ("Data cleaning is worth 15%.", KNIME_TABLE, KNIME_TITLE),
    ],
)
def test_a_wrong_or_out_of_scope_claim_is_not_supported(claim: str, chunk: str, title: str) -> None:
    assert table_row_supports(claim, (chunk,), (title,)) is False


def test_two_different_rows_fitting_the_claim_are_ambiguous() -> None:
    chunk = "Quizzes 40%\nExam 30%\nFinal exam 30%"

    assert table_row_supports("The final exam is worth 30%.", (chunk,), ("x.pdf",)) is False


def test_the_same_row_repeated_in_overlapping_chunks_is_one_match() -> None:
    assert (
        table_row_supports(
            "The final exam is worth 40% of the SE440 grade.",
            (SE440_TABLE, SE440_TABLE[40:]),
            (SE440_TITLE,),
        )
        is True
    )


def test_a_percentage_only_in_other_text_does_not_bind() -> None:
    assert (
        table_row_supports(
            "The final exam is worth 40% of the grade.",
            ("The final exam is held in week 16. Attendance below 40% fails the course.",),
            ("x.pdf",),
        )
        is False
    )


def test_a_long_label_does_not_bind() -> None:
    chunk = "Students who complete every lab and every quiz on time in the semester 40%"

    assert (
        table_row_supports(
            "Students who complete every lab and every quiz on time in the semester get 40%.",
            (chunk,),
            ("x.pdf",),
        )
        is False
    )


def test_arabic_indic_digits_and_percent_sign_are_normalized() -> None:
    assert (
        table_row_supports(
            "The final exam is worth ٤٠٪ of the grade.", (SE440_TABLE,), (SE440_TITLE,)
        )
        is True
    )
