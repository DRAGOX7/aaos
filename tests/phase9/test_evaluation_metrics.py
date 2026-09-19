from __future__ import annotations

import pytest

from tests.phase9.evaluation.metrics import (
    NliLabel,
    citation_accuracy,
    nli_confusion,
    recall_at_k,
    routing_domain_report,
    unsupported_claim_rate,
    visual_selection_report,
)


def test_domain_metrics_keep_denominators_and_provenance_separate() -> None:
    assert recall_at_k(("doc-1",), ("doc-1", "doc-2"), k=1).numerator == 1
    assert unsupported_claim_rate(unsupported=1, total=4).ratio == 0.25
    confusion = nli_confusion(
        (NliLabel.ENTAILMENT, NliLabel.NEUTRAL, NliLabel.CONTRADICTION),
        (NliLabel.ENTAILMENT, NliLabel.CONTRADICTION, NliLabel.CONTRADICTION),
    )
    assert confusion.entailment == 1
    assert confusion.neutral == 0
    assert confusion.contradiction == 1
    assert citation_accuracy(correct=2, total=3).denominator == 3
    assert routing_domain_report(("cal-en-001", "cal-ar-001"), correct=1).denominator == 2
    visual = visual_selection_report(
        selected=2, expected=3, provenance_kinds=("EMBEDDED_PDF", "DERIVED_VISUAL_EVIDENCE")
    )
    assert visual.metric.denominator == 3
    assert visual.provenance_kinds == ("DERIVED_VISUAL_EVIDENCE", "EMBEDDED_PDF")


def test_metrics_reject_invalid_denominators_and_provenance() -> None:
    with pytest.raises(ValueError):
        recall_at_k(("doc-1",), (), k=0)
    with pytest.raises(ValueError):
        unsupported_claim_rate(unsupported=2, total=1)
    with pytest.raises(ValueError):
        nli_confusion((NliLabel.ENTAILMENT,), ())
    with pytest.raises(ValueError):
        visual_selection_report(selected=1, expected=1, provenance_kinds=("EMBEDDED_PDF", "OTHER"))
