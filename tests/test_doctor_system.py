from __future__ import annotations

import ctypes
from types import SimpleNamespace
from typing import Any, cast

import pytest

from aaos.doctor import system
from aaos.doctor.models import (
    DiagnosticCode,
    DiagnosticFact,
    DiagnosticStatus,
    FactObservation,
    SystemObservation,
)
from aaos.doctor.system import (
    LocalSystemInspector,
    SystemInspector,
    inspect_local_system,
    system_results,
)


def test_non_windows_inspection_uses_each_approved_reader_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"system": 0, "machine": 0, "processor": 0, "cpu_count": 0}

    def read(name: str, value: str | int) -> str | int:
        calls[name] += 1
        return value

    monkeypatch.setattr("aaos.doctor.system.platform.system", lambda: read("system", "Linux"))
    monkeypatch.setattr("aaos.doctor.system.platform.machine", lambda: read("machine", "x86_64"))
    monkeypatch.setattr(
        "aaos.doctor.system.platform.processor", lambda: read("processor", "Ryzen 7")
    )
    monkeypatch.setattr("aaos.doctor.system.os.cpu_count", lambda: read("cpu_count", 16))
    monkeypatch.setattr(
        system,
        "_read_windows_cpu_model",
        lambda: pytest.fail("Windows CPU reader must not run on Linux"),
    )
    monkeypatch.setattr(
        system,
        "_read_windows_total_memory_bytes",
        lambda: pytest.fail("Windows memory reader must not run on Linux"),
    )

    observation = inspect_local_system()

    assert observation == SystemObservation(
        os_name=FactObservation("Linux"),
        architecture=FactObservation("x86_64"),
        cpu_model=FactObservation("Ryzen 7"),
        logical_processors=FactObservation(16),
        total_memory_bytes=FactObservation(None, DiagnosticCode.UNSUPPORTED_PLATFORM),
    )
    assert calls == {"system": 1, "machine": 1, "processor": 1, "cpu_count": 1}
    assert [result.status for result in system_results(observation)] == [
        DiagnosticStatus.PASS,
        DiagnosticStatus.PASS,
        DiagnosticStatus.WARN,
    ]


def test_windows_inspection_uses_private_cpu_and_memory_readers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("aaos.doctor.system.platform.system", lambda: "Windows")
    monkeypatch.setattr("aaos.doctor.system.platform.machine", lambda: "AMD64")
    monkeypatch.setattr("aaos.doctor.system.os.cpu_count", lambda: 20)
    monkeypatch.setattr(
        "aaos.doctor.system.platform.processor",
        lambda: pytest.fail("platform.processor must not run on Windows"),
    )
    monkeypatch.setattr(
        system, "_read_windows_cpu_model", lambda: FactObservation("Intel Core i7-12700H")
    )
    monkeypatch.setattr(
        system, "_read_windows_total_memory_bytes", lambda: FactObservation(17_179_869_184)
    )

    observation = inspect_local_system()

    assert observation == SystemObservation(
        os_name=FactObservation("Windows"),
        architecture=FactObservation("AMD64"),
        cpu_model=FactObservation("Intel Core i7-12700H"),
        logical_processors=FactObservation(20),
        total_memory_bytes=FactObservation(17_179_869_184),
    )
    assert [result.status for result in system_results(observation)] == [
        DiagnosticStatus.PASS,
        DiagnosticStatus.PASS,
        DiagnosticStatus.PASS,
    ]


def test_blank_platform_facts_become_typed_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aaos.doctor.system.platform.system", lambda: " ")
    monkeypatch.setattr("aaos.doctor.system.platform.machine", lambda: "")
    monkeypatch.setattr("aaos.doctor.system.platform.processor", lambda: "")
    monkeypatch.setattr("aaos.doctor.system.os.cpu_count", lambda: None)

    observation = inspect_local_system()
    results = system_results(observation)

    unavailable = FactObservation[Any](None, DiagnosticCode.UNSUPPORTED_PLATFORM)
    assert observation == SystemObservation(
        os_name=unavailable,
        architecture=unavailable,
        cpu_model=unavailable,
        logical_processors=unavailable,
        total_memory_bytes=unavailable,
    )
    assert [result.check_id for result in results] == ["platform", "cpu", "memory"]
    assert [result.status for result in results] == [DiagnosticStatus.WARN] * 3
    assert all(result.code is DiagnosticCode.UNSUPPORTED_PLATFORM for result in results)
    assert all(result.status is not DiagnosticStatus.FAIL for result in results)


def test_known_non_windows_blank_processor_is_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("aaos.doctor.system.platform.system", lambda: "Linux")
    monkeypatch.setattr("aaos.doctor.system.platform.machine", lambda: "x86_64")
    monkeypatch.setattr("aaos.doctor.system.platform.processor", lambda: " ")
    monkeypatch.setattr("aaos.doctor.system.os.cpu_count", lambda: 16)
    monkeypatch.setattr(
        system,
        "_read_windows_cpu_model",
        lambda: pytest.fail("Windows CPU reader must not run on Linux"),
    )
    monkeypatch.setattr(
        system,
        "_read_windows_total_memory_bytes",
        lambda: pytest.fail("Windows memory reader must not run on Linux"),
    )

    observation = inspect_local_system()

    assert observation.cpu_model == FactObservation(None, DiagnosticCode.UNSUPPORTED_PLATFORM)
    cpu_result = system_results(observation)[1]
    assert cpu_result.status is DiagnosticStatus.WARN
    assert cpu_result.code is DiagnosticCode.UNSUPPORTED_PLATFORM


def test_unknown_os_does_not_run_any_platform_specific_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("aaos.doctor.system.platform.system", lambda: " ")
    monkeypatch.setattr("aaos.doctor.system.platform.machine", lambda: "AMD64")
    monkeypatch.setattr("aaos.doctor.system.os.cpu_count", lambda: 20)
    monkeypatch.setattr(
        "aaos.doctor.system.platform.processor",
        lambda: pytest.fail("non-Windows CPU reader must not run for an unknown OS"),
    )
    monkeypatch.setattr(
        system,
        "_read_windows_cpu_model",
        lambda: pytest.fail("Windows CPU reader must not run for an unknown OS"),
    )
    monkeypatch.setattr(
        system,
        "_read_windows_total_memory_bytes",
        lambda: pytest.fail("Windows memory reader must not run for an unknown OS"),
    )

    observation = inspect_local_system()

    assert observation.cpu_model == FactObservation(None, DiagnosticCode.UNSUPPORTED_PLATFORM)
    assert observation.total_memory_bytes == FactObservation(
        None, DiagnosticCode.UNSUPPORTED_PLATFORM
    )
    _, cpu_result, memory_result = system_results(observation)
    assert cpu_result.status is DiagnosticStatus.WARN
    assert memory_result.status is DiagnosticStatus.WARN
    assert all(
        result.code is DiagnosticCode.UNSUPPORTED_PLATFORM for result in (cpu_result, memory_result)
    )


@pytest.mark.parametrize("logical_processors", [None, 0, -1])
def test_unavailable_logical_processor_count_is_typed(
    monkeypatch: pytest.MonkeyPatch, logical_processors: int | None
) -> None:
    monkeypatch.setattr("aaos.doctor.system.platform.system", lambda: "Linux")
    monkeypatch.setattr("aaos.doctor.system.platform.machine", lambda: "x86_64")
    monkeypatch.setattr("aaos.doctor.system.platform.processor", lambda: "Ryzen 7")
    monkeypatch.setattr("aaos.doctor.system.os.cpu_count", lambda: logical_processors)

    observation = inspect_local_system()

    assert observation.logical_processors == FactObservation(
        None, DiagnosticCode.UNSUPPORTED_PLATFORM
    )
    cpu_result = system_results(observation)[1]
    assert cpu_result.status is DiagnosticStatus.WARN
    assert cpu_result.code is DiagnosticCode.UNSUPPORTED_PLATFORM


def test_windows_cpu_registry_access_denied_is_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deny_access(*args: object) -> object:
        raise PermissionError("registry denied")

    fake_winreg = SimpleNamespace(
        HKEY_LOCAL_MACHINE=object(), OpenKey=deny_access, QueryValueEx=pytest.fail
    )
    monkeypatch.setattr(system, "_winreg", fake_winreg)

    observation = system._read_windows_cpu_model()

    assert observation == FactObservation(None, DiagnosticCode.PERMISSION_DENIED)


def test_windows_cpu_reader_uses_only_approved_key_and_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []
    root = object()

    class RegistryKey:
        def __enter__(self) -> RegistryKey:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    registry_key = RegistryKey()

    def open_key(hive: object, path: str) -> RegistryKey:
        calls.append((hive, path))
        return registry_key

    def query_value(opened_key: object, value_name: str) -> tuple[str, int]:
        calls.append((opened_key, value_name))
        return "  Intel Core i7-12700H  ", 1

    fake_winreg = SimpleNamespace(
        HKEY_LOCAL_MACHINE=root, OpenKey=open_key, QueryValueEx=query_value
    )
    monkeypatch.setattr(system, "_winreg", fake_winreg)

    observation = system._read_windows_cpu_model()

    assert observation == FactObservation("Intel Core i7-12700H")
    assert calls == [
        (root, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"),
        (registry_key, "ProcessorNameString"),
    ]


def test_windows_cpu_blank_registry_value_is_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RegistryKey:
        def __enter__(self) -> RegistryKey:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    fake_winreg = SimpleNamespace(
        HKEY_LOCAL_MACHINE=object(),
        OpenKey=lambda *args: RegistryKey(),
        QueryValueEx=lambda *args: (" ", 1),
    )
    monkeypatch.setattr(system, "_winreg", fake_winreg)

    assert system._read_windows_cpu_model() == FactObservation(
        None, DiagnosticCode.UNSUPPORTED_PLATFORM
    )


def test_windows_memory_reader_calls_global_memory_status_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class GlobalMemoryStatusEx:
        argtypes: object = None
        restype: object = None

        def __call__(self, status_pointer: object) -> int:
            nonlocal calls
            calls += 1
            status = cast(system._MemoryStatusEx, getattr(status_pointer, "_obj"))
            status.ullTotalPhys = 17_179_869_184
            return 1

    function = GlobalMemoryStatusEx()
    kernel32 = SimpleNamespace(GlobalMemoryStatusEx=function)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel32, raising=False)

    observation = system._read_windows_total_memory_bytes()

    assert observation == FactObservation(17_179_869_184)
    assert calls == 1


def test_windows_memory_false_return_is_typed_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class GlobalMemoryStatusEx:
        argtypes: object = None
        restype: object = None

        def __call__(self, status_pointer: object) -> int:
            return 0

    kernel32 = SimpleNamespace(GlobalMemoryStatusEx=GlobalMemoryStatusEx())
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel32, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 0, raising=False)

    assert system._read_windows_total_memory_bytes() == FactObservation(
        None, DiagnosticCode.UNSUPPORTED_PLATFORM
    )


def test_windows_memory_access_denied_is_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    def deny_access(*args: object, **kwargs: object) -> object:
        raise PermissionError("kernel32 denied")

    monkeypatch.setattr(ctypes, "WinDLL", deny_access, raising=False)

    observation = system._read_windows_total_memory_bytes()

    assert observation == FactObservation(None, DiagnosticCode.PERMISSION_DENIED)


def test_system_results_include_only_available_facts_and_never_fail() -> None:
    observation = SystemObservation(
        os_name=FactObservation("Windows"),
        architecture=FactObservation(None, DiagnosticCode.UNSUPPORTED_PLATFORM),
        cpu_model=FactObservation(None, DiagnosticCode.PERMISSION_DENIED),
        logical_processors=FactObservation(20),
        total_memory_bytes=FactObservation(None, DiagnosticCode.UNSUPPORTED_PLATFORM),
    )

    platform_result, cpu_result, memory_result = system_results(observation)

    assert platform_result.facts == (DiagnosticFact("os_name", "Windows"),)
    assert platform_result.status is DiagnosticStatus.WARN
    assert platform_result.code is DiagnosticCode.UNSUPPORTED_PLATFORM
    assert cpu_result.facts == (DiagnosticFact("logical_processors", 20),)
    assert cpu_result.status is DiagnosticStatus.WARN
    assert cpu_result.code is DiagnosticCode.PERMISSION_DENIED
    assert memory_result.facts == ()
    assert memory_result.status is DiagnosticStatus.WARN
    assert memory_result.code is DiagnosticCode.UNSUPPORTED_PLATFORM
    assert all(
        result.status is not DiagnosticStatus.FAIL
        for result in (platform_result, cpu_result, memory_result)
    )


def test_local_inspector_implements_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = SystemObservation(
        os_name=FactObservation("Linux"),
        architecture=FactObservation("x86_64"),
        cpu_model=FactObservation("Ryzen 7"),
        logical_processors=FactObservation(16),
        total_memory_bytes=FactObservation(None, DiagnosticCode.UNSUPPORTED_PLATFORM),
    )
    monkeypatch.setattr(system, "inspect_local_system", lambda: expected)
    inspector = LocalSystemInspector()

    assert isinstance(inspector, SystemInspector)
    assert inspector.inspect() == expected
