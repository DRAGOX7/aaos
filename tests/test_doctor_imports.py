"""Regression coverage for side-effect-free doctor and CLI imports."""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

_DOCTOR_IMPORTS = (
    "aaos.doctor",
    "aaos.doctor.commands",
    "aaos.doctor.model_check",
    "aaos.doctor.models",
    "aaos.doctor.nvidia",
    "aaos.doctor.reference",
    "aaos.doctor.rendering",
    "aaos.doctor.runtime",
    "aaos.doctor.service",
    "aaos.doctor.system",
    "aaos.cli",
    "aaos.main",
)

_FORBIDDEN_MODULE_SCOPE_CALLS = {
    "subprocess.run",
    "shutil.which",
    "AsyncHTTPTransport",
    "LMStudioGateway",
    "DoctorService.run",
}

_REGISTRY_READER_NAMES = frozenset(
    {"ConnectRegistry", "EnumKey", "EnumValue", "OpenKey", "QueryValueEx"}
)


def test_doctor_imports_are_inert_in_an_isolated_process(tmp_path: Path) -> None:
    """Imports must not inspect hardware, start commands, or contact LM Studio."""
    source_root = Path(__file__).resolve().parents[1] / "src"
    script = (
        textwrap.dedent(
            """
        import shutil
        import subprocess
        import sys

        import httpx2

        def forbidden(*args, **kwargs):
            raise AssertionError("external diagnostic work during import")

        subprocess.run = forbidden
        shutil.which = forbidden
        httpx2.AsyncHTTPTransport = forbidden
        httpx2.AsyncClient = forbidden
        sys.path.insert(0, SOURCE_ROOT)

        IMPORTS
        """
        )
        .replace("SOURCE_ROOT", repr(str(source_root)))
        .replace("IMPORTS", "\n".join(f"import {module}" for module in _DOCTOR_IMPORTS))
    )

    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert list(tmp_path.iterdir()) == []


def test_doctor_sources_have_no_forbidden_module_scope_calls() -> None:
    """Keep import-time process, registry, hardware, and network work prohibited."""
    source_root = Path(__file__).resolve().parents[1] / "src" / "aaos"
    source_files = sorted((source_root / "doctor").glob("*.py")) + [
        source_root / "cli.py",
        source_root / "main.py",
    ]

    violations: list[str] = []
    for source_file in source_files:
        tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        for call in _ModuleScopeCallVisitor().calls(tree):
            if _is_forbidden_call(call):
                violations.append(
                    f"{source_file.relative_to(source_root)}:{call.lineno}: {_call_name(call)}"
                )

    assert not violations, "module-scope diagnostic calls are forbidden:\n" + "\n".join(violations)


class _ModuleScopeCallVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self._function_depth = 0
        self._calls: list[ast.Call] = []

    def calls(self, tree: ast.AST) -> tuple[ast.Call, ...]:
        self.visit(tree)
        return tuple(self._calls)

    def visit_Call(self, node: ast.Call) -> None:
        if self._function_depth == 0:
            self._calls.append(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function_depth += 1
        self.generic_visit(node)
        self._function_depth -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function_depth += 1
        self.generic_visit(node)
        self._function_depth -= 1

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._function_depth += 1
        self.generic_visit(node)
        self._function_depth -= 1


def _is_forbidden_call(call: ast.Call) -> bool:
    call_name = _call_name(call)
    terminal_name = call_name.rsplit(".", 1)[-1]
    return (
        call_name in _FORBIDDEN_MODULE_SCOPE_CALLS
        or terminal_name in _FORBIDDEN_MODULE_SCOPE_CALLS
        or terminal_name in _REGISTRY_READER_NAMES
        or terminal_name.startswith("inspect_")
    )


def _call_name(call: ast.Call) -> str:
    return _expression_name(call.func)


def _expression_name(expression: ast.expr) -> str:
    if isinstance(expression, ast.Name):
        return expression.id
    if isinstance(expression, ast.Attribute):
        parent = _expression_name(expression.value)
        return f"{parent}.{expression.attr}" if parent else expression.attr
    return ""
