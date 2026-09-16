"""Export the AAOS OpenAPI schema deterministically."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from aaos.main import create_app

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_OUTPUT = (
    _REPOSITORY_ROOT
    / "frontend"
    / "openapi"
    / "aaos-v1.json"
)


def build_openapi_schema() -> dict[str, object]:
    """Build the browser API schema without requiring a live database."""

    sessions: sessionmaker[Session] = sessionmaker()

    return create_app(
        session_factory=sessions,
    ).openapi()


def export_openapi(output_path: Path) -> None:
    """Write stable UTF-8 JSON with deterministic formatting."""

    schema = build_openapi_schema()

    rendered = json.dumps(
        schema,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path.write_bytes(
        (rendered + "\n").encode("utf-8")
    )


def main() -> int:
    arguments = sys.argv[1:]

    if len(arguments) > 1:
        print(
            "usage: export_openapi.py [OUTPUT_PATH]",
            file=sys.stderr,
        )
        return 2

    output_path = (
        Path(arguments[0])
        if arguments
        else _DEFAULT_OUTPUT
    )

    export_openapi(output_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
