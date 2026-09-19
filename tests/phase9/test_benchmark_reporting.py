from __future__ import annotations

import pytest

from scripts.phase9.benchmark import (
    HARDWARE_PROFILE,
    BenchmarkSample,
    BenchmarkSummary,
)


def test_benchmark_summary_keeps_warm_cold_and_real_fake_labels() -> None:
    samples = tuple(
        BenchmarkSample(
            operation="fake-retrieval",
            elapsed_ms=float(index + 1),
            sample_kind="WARM" if index else "COLD",
            implementation_kind="FAKE",
            hardware_profile=HARDWARE_PROFILE,
        )
        for index in range(3)
    )
    summary = BenchmarkSummary.from_samples("fake-retrieval", samples)
    assert summary.sample_count == 3
    assert summary.median_ms == 2.0
    assert summary.p95_ms is None
    assert {sample.sample_kind for sample in summary.samples} == {"COLD", "WARM"}
    assert all(sample.implementation_kind == "FAKE" for sample in summary.samples)
    assert "6 GB" in HARDWARE_PROFILE
    assert "16 GB" in HARDWARE_PROFILE


def test_benchmark_rejects_mixed_operations_and_invalid_values() -> None:
    first = BenchmarkSample("operation-a", 1.0, "WARM", "REAL_LOCAL", HARDWARE_PROFILE)
    second = BenchmarkSample("operation-b", 2.0, "WARM", "REAL_LOCAL", HARDWARE_PROFILE)
    with pytest.raises(ValueError):
        BenchmarkSummary.from_samples("operation-a", (first, second))
    with pytest.raises(ValueError):
        BenchmarkSample("operation-a", -1.0, "WARM", "FAKE", HARDWARE_PROFILE)
