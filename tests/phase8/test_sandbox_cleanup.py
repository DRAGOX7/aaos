from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from aaos.phase8.computation_contracts import (
    COMPUTATION_CONTRACT_VERSION,
    ComputationInput,
    ComputationLimits,
    ComputationRequest,
)
from aaos.phase8.sandbox_resources import materialize_inputs


def _request() -> ComputationRequest:
    content = b"input"
    return ComputationRequest(
        request_id="request-1",
        code="1 + 1",
        inputs=(ComputationInput("input-1", "text/plain", sha256(content).hexdigest(), content),),
        limits=ComputationLimits(5, 5, 64 * 1024 * 1024, 4096, 2, 4096),
        contract_version=COMPUTATION_CONTRACT_VERSION,
    )


def test_materialization_is_bounded_and_uses_task_local_names(tmp_path: Path) -> None:
    paths = materialize_inputs(_request(), tmp_path)
    assert len(paths) == 1
    assert paths[0].parent == tmp_path
    assert paths[0].name == "input_input-1.bin"
    assert paths[0].read_bytes() == b"input"


def test_materialization_rejects_symlinked_workspace(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    with pytest.raises(ValueError, match="symlink"):
        materialize_inputs(_request(), link)
