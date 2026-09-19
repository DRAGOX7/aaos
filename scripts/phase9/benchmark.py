"""Bounded benchmark value objects for Phase 9 release qualification."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

HARDWARE_PROFILE = "Windows 11; RTX 4050 Laptop GPU; 6 GB VRAM; 16 GB RAM"
P95_MIN_SAMPLES = 20


@dataclass(frozen=True, slots=True)
class BenchmarkSample:
    operation: str
    elapsed_ms: float
    sample_kind: Literal["WARM", "COLD"]
    implementation_kind: Literal["REAL_LOCAL", "FAKE", "SYNTHETIC"]
    hardware_profile: str

    def __post_init__(self) -> None:
        if not self.operation or len(self.operation) > 96:
            raise ValueError("operation must be a bounded identifier")
        if not isinstance(self.elapsed_ms, (int, float)) or isinstance(self.elapsed_ms, bool):
            raise TypeError("elapsed_ms must be numeric")
        if not math.isfinite(self.elapsed_ms) or self.elapsed_ms < 0:
            raise ValueError("elapsed_ms must be finite and non-negative")
        if self.sample_kind not in {"WARM", "COLD"}:
            raise ValueError("sample_kind is not closed")
        if self.implementation_kind not in {"REAL_LOCAL", "FAKE", "SYNTHETIC"}:
            raise ValueError("implementation_kind is not closed")
        if not self.hardware_profile or len(self.hardware_profile) > 256:
            raise ValueError("hardware_profile must be bounded")


@dataclass(frozen=True, slots=True)
class BenchmarkSummary:
    operation: str
    sample_count: int
    median_ms: float
    p95_ms: float | None
    samples: tuple[BenchmarkSample, ...]

    @classmethod
    def from_samples(
        cls, operation: str, samples: tuple[BenchmarkSample, ...]
    ) -> "BenchmarkSummary":
        if not samples:
            raise ValueError("benchmark requires at least one sample")
        if any(sample.operation != operation for sample in samples):
            raise ValueError("all samples must describe the same operation")
        ordered = sorted(sample.elapsed_ms for sample in samples)
        middle = len(ordered) // 2
        median = (
            ordered[middle]
            if len(ordered) % 2
            else (ordered[middle - 1] + ordered[middle]) / 2
        )
        p95: float | None = None
        if len(ordered) >= P95_MIN_SAMPLES:
            index = math.ceil(len(ordered) * 0.95) - 1
            p95 = ordered[index]
        return cls(operation, len(samples), median, p95, samples)


__all__ = ["BenchmarkSample", "BenchmarkSummary", "HARDWARE_PROFILE", "P95_MIN_SAMPLES"]
