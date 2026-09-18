from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[1]


def test_importing_main_in_a_clean_directory_does_not_create_normal_database(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import aaos.main; aaos.main.create_app()"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(REPOSITORY_ROOT / "src")},
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout == ""
    assert result.stderr == ""
    assert not (tmp_path / "data" / "database" / "aaos.db").exists()
