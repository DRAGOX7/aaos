"""Foundation checks for the supported AAOS Python runtime."""

from __future__ import annotations

import struct
import sys


def test_supported_python_version() -> None:
    """AAOS must run on the governed Python 3.11 compatibility line."""
    assert sys.version_info[:2] == (3, 11), (
        f"AAOS requires Python 3.11, received {sys.version_info.major}.{sys.version_info.minor}"
    )


def test_interpreter_is_64_bit() -> None:
    """AAOS requires a 64-bit interpreter for its later local ML workloads."""
    pointer_width_bits = struct.calcsize("P") * 8
    assert pointer_width_bits == 64, f"AAOS requires a 64-bit interpreter, got {pointer_width_bits}-bit"
