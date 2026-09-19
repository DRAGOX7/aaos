from __future__ import annotations

from pathlib import Path

from scripts.phase9.public_release import scan_public_release


def test_public_release_scan_is_clean_and_does_not_return_matching_content(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("sanitized release", encoding="utf-8")
    release = tmp_path / "docs" / "release"
    release.mkdir(parents=True)
    (release / "report.md").write_text("synthetic evidence only", encoding="utf-8")
    report = scan_public_release(tmp_path)
    assert report.secret_findings == 0
    assert report.private_data_findings == 0
    assert report.normal_database_findings == 0
    assert report.model_weight_findings == 0
    assert report.temporary_artifact_findings == 0
    assert report.raw_matches == ()


def test_public_release_scan_flags_sensitive_paths_without_leaking_values(tmp_path: Path) -> None:
    secret_dir = tmp_path / "data" / "credentials"
    secret_dir.mkdir(parents=True)
    (secret_dir / "oauth.json").write_text('{"client_secret":"sentinel"}', encoding="utf-8")
    (tmp_path / "model.gguf").write_bytes(b"weights")
    (tmp_path / "acceptance.db").write_bytes(b"sqlite")
    report = scan_public_release(tmp_path)
    assert report.secret_findings >= 1
    assert report.normal_database_findings >= 1
    assert report.model_weight_findings >= 1
    assert "sentinel" not in report.to_dict()
    assert report.raw_matches == ()
