"""Deterministic support for one-percentage claims read from table rows.

The claim NLI cannot link a sentence ("Machine learning implementation is
worth 20% of the project grade.") to a header-value row
("Machine learning implementation 20%"), so it judges such true claims
neutral. This check accepts a claim only when exactly one row of the cited
text binds the claim's single percentage to a label the claim repeats, and
the claim asserts nothing beyond that label, that percentage, the cited
documents' identity and fixed weight phrasing. Anything else is no match,
and the caller's ordinary verification decides.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_ARABIC_INDIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٪", "01234567890123456789%")
_ARABIC_LETTER = re.compile(r"[؀-ۿ]")
# A standalone number: not part of a word such as "SE440" or "CLO1".
_NUMBER = re.compile(r"(?<![A-Za-z\d.])\d+(?:\.\d+)?(?![A-Za-z\d])")
_CLAIM_PERCENT = re.compile(r"(?<![A-Za-z\d.])(\d+(?:\.\d+)?)\s*(?:%|percent\b)", re.IGNORECASE)
_ROW_PERCENT = re.compile(r"(?<![A-Za-z\d.])(\d+(?:\.\d+)?)\s*%")
_TOKEN = re.compile(r"[a-z][a-z0-9#]*")
_COURSE_CODE = re.compile(r"[a-z]{2,4}\d{3}[a-z]?")
_LABEL_TRIM = " \t\r\n()[]{}:;,.-–—|"
MAX_LABEL_WORDS = 8

_STOPWORDS = frozenset({"a", "an", "and", "the", "of", "in", "for", "to", "on", "with", "or"})
# Phrasing a weight statement may use without asserting anything further.
_WEIGHT_PHRASING = frozenset(
    {
        "is",
        "are",
        "worth",
        "weighs",
        "weight",
        "weighted",
        "accounts",
        "counts",
        "makes",
        "up",
        "total",
        "overall",
        "grade",
        "grading",
        "mark",
        "marks",
        "score",
        "course",
        "project",
        "component",
        "part",
        "final",
        "percent",
    }
)


def _normalize(text: str) -> str:
    return text.translate(_ARABIC_INDIC_DIGITS)


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _label_words(label: str) -> frozenset[str]:
    return frozenset(
        word for word in re.findall(r"[a-z]{2,}", label.lower()) if word not in _STOPWORDS
    )


def _row_labels(chunk: str, value: float) -> Iterable[frozenset[str]]:
    """Labels bound to each occurrence of ``value``% in ``chunk``.

    A row's label is the text since the previous number, which also covers
    rows a PDF extraction wrapped across lines ("Final Exam \\r\\n(40%)").
    """
    numbers = [match.end() for match in _NUMBER.finditer(chunk)]
    for match in _ROW_PERCENT.finditer(chunk):
        if float(match.group(1)) != value:
            continue
        start = max((end for end in numbers if end <= match.start()), default=0)
        label = " ".join(chunk[start : match.start()].split()).strip(_LABEL_TRIM)
        if not label or len(label.split()) > MAX_LABEL_WORDS:
            continue
        words = _label_words(label)
        if words:
            yield words


def table_row_supports(
    claim_text: str, cited_texts: tuple[str, ...], document_titles: tuple[str, ...]
) -> bool:
    """Return whether exactly one cited table row states the claim's percentage."""
    claim = _normalize(claim_text)
    if _ARABIC_LETTER.search(claim):
        return False  # labels are English table text; Arabic claims keep the NLI path
    percents = _CLAIM_PERCENT.findall(claim)
    if len(percents) != 1 or len(_NUMBER.findall(claim)) != 1:
        return False
    value = float(percents[0])

    claim_tokens = _tokens(claim)
    claim_words = frozenset(claim_tokens)
    matches = {
        label
        for chunk in cited_texts
        for label in _row_labels(_normalize(chunk), value)
        if label <= claim_words
    }
    if len(matches) != 1:
        return False
    (label,) = matches

    identity = frozenset(token for title in document_titles for token in _tokens(title))
    leftover = [
        token
        for token in claim_tokens
        if token not in label
        and token not in _STOPWORDS
        and token not in _WEIGHT_PHRASING
        and token not in identity
        and not _COURSE_CODE.fullmatch(token)
    ]
    return not leftover
