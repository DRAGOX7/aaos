from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from aaos.main import create_app


def _schema() -> dict[str, object]:
    sessions: sessionmaker[Session] = sessionmaker()

    return create_app(
        session_factory=sessions,
    ).openapi()


def test_openapi_contains_versioned_courses_contract() -> None:
    schema = _schema()

    paths = schema["paths"]
    assert isinstance(paths, dict)

    assert "/api/v1/courses" in paths
    assert "/health" in paths

    components = schema["components"]
    assert isinstance(components, dict)

    schemas = components["schemas"]
    assert isinstance(schemas, dict)

    course_schema = schemas["CourseSummary"]
    assert isinstance(course_schema, dict)

    properties = course_schema["properties"]
    assert isinstance(properties, dict)

    assert set(properties) == {
        "id",
        "title",
        "code",
        "description",
        "updated_at",
    }

    rendered = json.dumps(course_schema)

    assert "source_system" not in rendered
    assert "source_id" not in rendered


def test_courses_operation_references_course_list_response() -> None:
    schema = _schema()

    paths = schema["paths"]
    assert isinstance(paths, dict)

    courses_path = paths["/api/v1/courses"]
    assert isinstance(courses_path, dict)

    operation = courses_path["get"]
    assert isinstance(operation, dict)

    responses = operation["responses"]
    assert isinstance(responses, dict)

    success = responses["200"]
    assert isinstance(success, dict)

    content = success["content"]
    assert isinstance(content, dict)

    application_json = content["application/json"]
    assert isinstance(application_json, dict)

    response_schema = application_json["schema"]

    assert response_schema == {
        "$ref": "#/components/schemas/CourseListResponse"
    }


def test_openapi_exporter_is_byte_deterministic(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    for output in (first, second):
        completed = subprocess.run(
            [
                sys.executable,
                "scripts/export_openapi.py",
                str(output),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        assert completed.returncode == 0, completed.stderr
        assert output.exists()

    assert first.read_bytes() == second.read_bytes()

    rendered = first.read_text(encoding="utf-8")

    assert rendered.endswith("\n")
    assert not rendered.endswith("\n\n")

    parsed = json.loads(rendered)

    assert "/api/v1/courses" in parsed["paths"]


def test_committed_openapi_artifact_matches_exporter(
    tmp_path: Path,
) -> None:
    generated = tmp_path / "openapi.json"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/export_openapi.py",
            str(generated),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr

    committed = Path("frontend/openapi/aaos-v1.json")

    assert committed.exists()
    assert committed.read_bytes() == generated.read_bytes()


def test_frontend_contract_generation_workspace_is_frozen() -> None:
    package_path = Path("frontend/package.json")
    lock_path = Path("frontend/package-lock.json")
    generated_path = Path("frontend/types/api.generated.ts")

    assert package_path.exists()
    assert lock_path.exists()
    assert generated_path.exists()

    package = json.loads(package_path.read_text(encoding="utf-8"))

    assert package["private"] is True
    assert package["scripts"] == {
        "generate:openapi": "uv run python ../scripts/export_openapi.py",
        "generate:api-types": (
            "npm run generate:openapi && "
            "openapi-typescript openapi/aaos-v1.json "
            "-o types/api.generated.ts"
        ),
        "check:api-types": (
            "npm run generate:api-types && "
            "git diff --exit-code -- "
            "openapi/aaos-v1.json types/api.generated.ts"
        ),
    }

    dev_dependencies = package["devDependencies"]

    assert "openapi-typescript" in dev_dependencies

    version = dev_dependencies["openapi-typescript"]

    assert isinstance(version, str)
    assert version
    assert not version.startswith("^")
    assert not version.startswith("~")

    generated = generated_path.read_text(encoding="utf-8")

    assert "CourseSummary" in generated
    assert "CourseListResponse" in generated
    assert '"/api/v1/courses"' in generated

    assert "source_system" not in generated
    assert "source_id" not in generated


def test_generated_types_are_byte_reproducible() -> None:
    generated = Path("frontend/types/api.generated.ts")

    assert generated.exists()

    before = generated.read_bytes()

    npm_executable = "npm.cmd" if sys.platform == "win32" else "npm"

    completed = subprocess.run(
        [
            npm_executable,
            "run",
            "generate:api-types",
        ],
        cwd="frontend",
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert generated.read_bytes() == before
