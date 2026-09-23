from __future__ import annotations

from pathlib import Path

from scripts.phase9.qualify_clean_setup import qualify_database


def test_disposable_database_reaches_phase8_head_without_normal_db(tmp_path: Path) -> None:
    disposable = tmp_path / "acceptance.db"
    report = qualify_database(disposable)
    assert report.head_matches is True
    assert report.revision == "0008_document_structural_provenance"
    assert "visual_analysis_runs" in report.tables
    assert "visual_elements" in report.tables
    assert "document_structure_units" in report.tables
    assert report.external_calls == 0
    assert report.external_writes == 0
    assert report.created_paths == (str(disposable),)


def test_historical_baseline_is_reported_without_mutating_existing_path(tmp_path: Path) -> None:
    disposable = tmp_path / "baseline.db"
    report = qualify_database(disposable, baseline="0006_phase7e_task_records")
    assert report.revision == "0006_phase7e_task_records"
    assert report.head_matches is False
    assert report.created_paths == (str(disposable),)
