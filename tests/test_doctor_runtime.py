from __future__ import annotations

import pytest

from aaos.doctor.models import (
    DiagnosticCode,
    DiagnosticStatus,
    RuntimeObservation,
)
from aaos.doctor.runtime import inspect_runtime, runtime_results


def test_python_311_and_64_bit_pass() -> None:
    results = runtime_results(RuntimeObservation(3, 11, 64))

    assert [result.status for result in results] == [DiagnosticStatus.PASS] * 2


@pytest.mark.parametrize("version", [(3, 10), (3, 12), (4, 0)])
def test_unsupported_python_version_fails(version: tuple[int, int]) -> None:
    result = runtime_results(RuntimeObservation(*version, pointer_width_bits=64))[0]

    assert result.status is DiagnosticStatus.FAIL
    assert result.code is DiagnosticCode.UNSUPPORTED_RUNTIME


def test_non_64_bit_runtime_fails() -> None:
    result = runtime_results(RuntimeObservation(3, 11, 32))[1]

    assert result.status is DiagnosticStatus.FAIL
    assert result.code is DiagnosticCode.NON_64_BIT_RUNTIME


def test_inspect_runtime_uses_version_and_width_readers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aaos.doctor.runtime._read_python_version", lambda: (3, 11))
    monkeypatch.setattr("aaos.doctor.runtime._read_pointer_width_bits", lambda: 64)

    assert inspect_runtime() == RuntimeObservation(3, 11, 64)
