"""Explicitly authorized Phase 4 acceptance boundary.

Normal pytest collection performs only pure guard checks.  Real target
hardware, corpus, local answer-model, human-review, and RAGAS activity is
never started unless the dedicated opt-in and authorization markers are set by
the human for a separately reviewed run.  The fake mode exercises only the
sanitized report composition and never touches production paths or services.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from aaos.rag.contracts import GlobalScope
from aaos.rag.evaluation.contracts import EvaluationCase, EvaluationDataset, RelevanceJudgment
from aaos.rag.evaluation.reports import (
    SAFE_ACCEPTANCE_FIELDS,
    build_acceptance_report,
    serialize_evaluation_summary,
)
from aaos.rag.retrieval import SufficiencyStatus

RUN_ENV = "AAOS_RUN_PHASE4_LOCAL_RAG_ACCEPTANCE"
AUTH_ENV = "AAOS_PHASE4_ACCEPTANCE_AUTHORIZED"
FAKE_ENV = "AAOS_PHASE4_ACCEPTANCE_FAKE"
CORPUS_ENV = "AAOS_PHASE4_ACCEPTANCE_CORPUS_ROOT"
OUTPUT_ENV = "AAOS_PHASE4_ACCEPTANCE_OUTPUT_ROOT"

_NORMAL_DATA_ROOT = Path.cwd() / "data"
_PROTECTED_ROOTS = (
    _NORMAL_DATA_ROOT / "database",
    _NORMAL_DATA_ROOT / "rag",
    _NORMAL_DATA_ROOT / "documents",
)


@dataclass(frozen=True, slots=True)
class _AcceptanceConfiguration:
    """Validated, non-secret acceptance inputs; no files are opened here."""

    corpus_root: Path
    output_root: Path


def _real_opt_in() -> bool:
    return os.environ.get(RUN_ENV) == "1"


def _fake_opt_in() -> bool:
    return os.environ.get(FAKE_ENV) == "1"


def _load_real_configuration() -> _AcceptanceConfiguration:
    if not _real_opt_in() or os.environ.get(AUTH_ENV) != "1":
        raise RuntimeError("explicit Phase 4 acceptance authorization is required")
    roots: list[Path] = []
    for name in (CORPUS_ENV, OUTPUT_ENV):
        raw = os.environ.get(name)
        if not raw:
            raise RuntimeError("explicit disposable acceptance roots are required")
        path = Path(raw)
        if not path.is_absolute() or path.resolve(strict=False) != path:
            raise RuntimeError("acceptance roots must be absolute and normalized")
        if any(path == root or path.is_relative_to(root) for root in _PROTECTED_ROOTS):
            raise RuntimeError("acceptance roots must not use normal AAOS data paths")
        roots.append(path)
    return _AcceptanceConfiguration(corpus_root=roots[0], output_root=roots[1])


def _fake_dataset() -> EvaluationDataset:
    return EvaluationDataset(
        cases=(
            EvaluationCase(
                case_id="acceptance-en",
                question="private test question",
                query_language="en",
                answer_language="en",
                expected_status=SufficiencyStatus.SUPPORTED,
                scope=GlobalScope(),
                relevance=(RelevanceJudgment(1, (1,), 3),),
                categories=("semantic", "current"),
                authorized_document_version_ids=(1,),
            ),
        ),
        dataset_version="phase4-acceptance-fake-v1",
    )


def test_default_acceptance_guard_has_no_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without opt-in, no model, storage, network, or freeze operation runs."""
    for name in (RUN_ENV, AUTH_ENV, FAKE_ENV, CORPUS_ENV, OUTPUT_ENV):
        monkeypatch.delenv(name, raising=False)
    normal_database = tmp_path / "database" / "aaos.db"
    normal_chroma = tmp_path / "rag" / "chroma"
    assert not _real_opt_in()
    assert not _fake_opt_in()
    assert not normal_database.exists()
    assert not normal_chroma.exists()


def test_real_acceptance_requires_all_explicit_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(RUN_ENV, "1")
    monkeypatch.delenv(AUTH_ENV, raising=False)
    with pytest.raises(RuntimeError, match="authorization"):
        _load_real_configuration()


def test_real_acceptance_rejects_normal_data_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(RUN_ENV, "1")
    monkeypatch.setenv(AUTH_ENV, "1")
    monkeypatch.setenv(CORPUS_ENV, str(_NORMAL_DATA_ROOT / "documents"))
    monkeypatch.setenv(OUTPUT_ENV, str(_NORMAL_DATA_ROOT / "rag"))
    with pytest.raises(RuntimeError, match="normal AAOS data paths"):
        _load_real_configuration()


@pytest.mark.skipif(not _fake_opt_in(), reason=f"set {FAKE_ENV}=1 for offline fake acceptance")
def test_fake_acceptance_report_is_allowlisted_and_private_safe(tmp_path: Path) -> None:
    summary = build_acceptance_report(_fake_dataset(), run_id="fake-acceptance")
    assert set(summary) == SAFE_ACCEPTANCE_FIELDS
    rendered = serialize_evaluation_summary(summary)
    assert "private test question" not in rendered
    assert "acceptance-en" not in rendered
    destination = tmp_path / "evaluation-summary.json"
    destination.write_text(rendered, encoding="utf-8")
    assert json.loads(destination.read_text(encoding="utf-8"))["schema_version"] == (
        "phase4-evaluation-v1"
    )


@pytest.mark.skipif(
    not (_real_opt_in() and os.environ.get(AUTH_ENV) == "1"),
    reason=f"set {RUN_ENV}=1 and {AUTH_ENV}=1 for separately reviewed acceptance",
)
def test_controlled_target_hardware_acceptance_requires_reviewed_roots() -> None:
    """The real gate remains explicit and cannot silently use normal paths."""
    configuration = _load_real_configuration()
    for path in (configuration.corpus_root, configuration.output_root):
        assert all(path != root and not path.is_relative_to(root) for root in _PROTECTED_ROOTS)
