"""CLI for writing a canonical Phase 9 baseline manifest."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from scripts.phase9.release_manifest import build_release_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.repo_root.resolve()
    release_commit = subprocess.check_output(
        ("git", "-C", str(root), "rev-parse", "HEAD"), text=True, encoding="utf-8"
    ).strip()
    manifest = build_release_manifest(
        repo_root=root,
        release_commit=release_commit,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(
        (json.dumps(manifest.as_dict(), ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
