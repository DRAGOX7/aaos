from __future__ import annotations

from pathlib import Path

from tests.phase9.failure_scenarios import import_inert_probe


def test_import_probe_is_side_effect_free(tmp_path: Path) -> None:
    result = import_inert_probe(tmp_path)
    assert result.imported is True
    assert result.database_created is False
    assert result.network_calls == 0
    assert result.model_calls == 0
    assert result.docker_calls == 0
    assert result.provider_calls == 0
    assert result.created_paths == ()
