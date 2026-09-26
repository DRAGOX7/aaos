from __future__ import annotations

import inspect
import os
import subprocess
import sys
from pathlib import Path

import aaos.main

REPOSITORY_ROOT = Path(__file__).parents[1]
FRONTEND_ROOT = REPOSITORY_ROOT / "frontend"


def test_import_and_app_construction_are_database_inert(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import aaos.main; aaos.main.create_app()"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(REPOSITORY_ROOT / "src")},
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stderr == ""
    assert not (tmp_path / "data" / "database" / "aaos.db").exists()


def test_app_construction_has_no_startup_runtime_side_effects() -> None:
    application = aaos.main.create_app()

    assert application.router.on_startup == []
    assert not (REPOSITORY_ROOT / "data" / "database" / "aaos.db").exists()


def test_approval_commands_use_server_approver_and_empty_browser_body() -> None:
    application = aaos.main.create_app()
    openapi = application.openapi()
    schema = openapi["components"]["schemas"]["ApprovalCommandRequest"]

    assert schema["properties"] == {}
    assert schema["additionalProperties"] is False

    source = inspect.getsource(aaos.main.create_app)
    assert "principal_provider" in source
    assert "approver=_approver()" in (
        Path(REPOSITORY_ROOT / "src" / "aaos" / "web" / "routes.py").read_text(encoding="utf-8")
    )


def test_routes_expose_no_generic_execution_or_reconciliation_endpoint() -> None:
    paths = set(aaos.main.create_app().openapi()["paths"])

    assert not any(
        any(segment in path.lower() for segment in ("execute", "retry", "reconcile", "provider"))
        for path in paths
    )


def test_activity_and_health_route_sources_are_read_only() -> None:
    for relative in (
        "src/aaos/web/activity_read.py",
        "src/aaos/web/health_read.py",
    ):
        source = (REPOSITORY_ROOT / relative).read_text(encoding="utf-8").lower()
        assert "session.add(" not in source
        assert "session.delete(" not in source
        assert "session.commit(" not in source
        assert "session.flush(" not in source


def test_frontend_browser_sources_have_no_forbidden_authority_or_secret_fields() -> None:
    source_files = tuple(
        path
        for directory in (
            FRONTEND_ROOT / "app",
            FRONTEND_ROOT / "components",
            FRONTEND_ROOT / "lib",
            FRONTEND_ROOT / "types",
        )
        for path in directory.rglob("*")
        if path.is_file() and path.suffix in {".ts", ".tsx", ".svg"}
    )
    browser_source = "\n".join(path.read_text(encoding="utf-8") for path in source_files)
    forbidden = (
        "DATABASE_URL",
        "Authorization",
        "Bearer",
        "data/auth",
        "data/credentials",
        "TrustedApproverContext",
        "provider payload",
        "Google tokens",
        "OAuth",
        "cookies",
        "traceback",
        "SQL",
        "API keys",
        "PreparedAction",
        "ActionEvent",
        "request fingerprints",
        "idempotency keys",
    )

    for token in forbidden:
        assert token.lower() not in browser_source.lower()
