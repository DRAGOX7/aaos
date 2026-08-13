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
        import importlib.abc
        import importlib.machinery
        import shutil
        import subprocess
        import sys

        import httpx2

        def forbidden(name):
            def fail(*args, **kwargs):
                raise AssertionError(f"{name} during import")
            return fail

        subprocess.run = forbidden("subprocess.run")
        shutil.which = forbidden("shutil.which")
        httpx2.AsyncHTTPTransport = forbidden("AsyncHTTPTransport")
        httpx2.AsyncClient = forbidden("AsyncClient")

        try:
            import winreg
        except ImportError:
            winreg = None
        if winreg is not None:
            for reader_name in REGISTRY_READERS:
                if hasattr(winreg, reader_name):
                    setattr(winreg, reader_name, forbidden(f"winreg.{reader_name}"))

        sys.path.insert(0, SOURCE_ROOT)
        installed_probes = set()

        class ProbeLoader(importlib.abc.Loader):
            def __init__(self, loader, module_name):
                self._loader = loader
                self._module_name = module_name

            def create_module(self, spec):
                create_module = getattr(self._loader, "create_module", None)
                return None if create_module is None else create_module(spec)

            def exec_module(self, module):
                self._loader.exec_module(module)
                for attribute_name in PROBED_ATTRIBUTES.get(self._module_name, ()):
                    setattr(
                        module,
                        attribute_name,
                        forbidden(f"{self._module_name}.{attribute_name}"),
                    )
                    installed_probes.add(f"{self._module_name}.{attribute_name}")
                if self._module_name == "aaos.doctor.service":
                    module.DoctorService.run = forbidden("DoctorService.run")
                    installed_probes.add("DoctorService.run")

        class ProbeFinder(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path, target=None):
                if fullname not in PROBED_ATTRIBUTES and fullname != "aaos.doctor.service":
                    return None
                spec = importlib.machinery.PathFinder.find_spec(fullname, path)
                if spec is None or spec.loader is None:
                    return spec
                spec.loader = ProbeLoader(spec.loader, fullname)
                return spec

        sys.meta_path.insert(0, ProbeFinder())

        IMPORTS

        expected_probes = {
            f"{module_name}.{attribute_name}"
            for module_name, attribute_names in PROBED_ATTRIBUTES.items()
            for attribute_name in attribute_names
        }
        expected_probes.add("DoctorService.run")
        if installed_probes != expected_probes:
            raise AssertionError(
                f"import probes missing: {sorted(expected_probes - installed_probes)}"
            )
        """
        )
        .replace("SOURCE_ROOT", repr(str(source_root)))
        .replace("REGISTRY_READERS", repr(tuple(sorted(_REGISTRY_READER_NAMES))))
        .replace(
            "PROBED_ATTRIBUTES",
            repr(
                {
                    "aaos.doctor.commands": ("FixedCommandInspector",),
                    "aaos.doctor.nvidia": ("CommandNvidiaInspector",),
                    "aaos.doctor.reference": ("LocalReferenceInspector",),
                    "aaos.doctor.runtime": ("LocalRuntimeInspector", "inspect_runtime"),
                    "aaos.doctor.system": ("LocalSystemInspector", "inspect_local_system"),
                    "aaos.inference.lm_studio": ("LMStudioGateway",),
                }
            ),
        )
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


def test_ast_guard_checks_function_definition_expressions_but_not_function_body() -> None:
    tree = ast.parse(
        """
@LMStudioGateway()
def guarded(
    positional: inspect_annotation() = inspect_default(),
    *,
    keyword: inspect_keyword_annotation() = inspect_keyword_default(),
) -> inspect_return_annotation():
    inspect_function_body()
"""
    )

    forbidden_calls = {
        _call_name(call)
        for call in _ModuleScopeCallVisitor().calls(tree)
        if _is_forbidden_call(call)
    }

    assert forbidden_calls == {
        "LMStudioGateway",
        "inspect_annotation",
        "inspect_default",
        "inspect_keyword_annotation",
        "inspect_keyword_default",
        "inspect_return_annotation",
    }


class _ModuleScopeCallVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self._calls: list[ast.Call] = []

    def calls(self, tree: ast.AST) -> tuple[ast.Call, ...]:
        self.visit(tree)
        return tuple(self._calls)

    def visit_Call(self, node: ast.Call) -> None:
        self._calls.append(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function_definition(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_definition(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_arguments(node.args)

    def _visit_function_definition(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        self._visit_arguments(node.args)
        if node.returns is not None:
            self.visit(node.returns)
        for type_parameter in getattr(node, "type_params", ()):
            self.visit(type_parameter)

    def _visit_arguments(self, arguments: ast.arguments) -> None:
        for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs):
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if arguments.vararg is not None and arguments.vararg.annotation is not None:
            self.visit(arguments.vararg.annotation)
        if arguments.kwarg is not None and arguments.kwarg.annotation is not None:
            self.visit(arguments.kwarg.annotation)
        for default in (*arguments.defaults, *arguments.kw_defaults):
            if default is not None:
                self.visit(default)


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
