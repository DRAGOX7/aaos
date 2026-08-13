from __future__ import annotations

import ctypes
import errno
import os
import platform
import sys
from collections.abc import Callable
from typing import Protocol, TypeVar, runtime_checkable

from aaos.doctor.models import (
    DiagnosticCode,
    DiagnosticFact,
    DiagnosticResult,
    DiagnosticStatus,
    FactObservation,
    SystemObservation,
)

if sys.platform == "win32":
    import winreg as _winreg
else:
    _winreg = None


@runtime_checkable
class SystemInspector(Protocol):
    def inspect(self) -> SystemObservation: ...


class LocalSystemInspector:
    def inspect(self) -> SystemObservation:
        return inspect_local_system()


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_uint32),
        ("dwMemoryLoad", ctypes.c_uint32),
        ("ullTotalPhys", ctypes.c_uint64),
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


_T = TypeVar("_T")


def _unavailable(
    code: DiagnosticCode = DiagnosticCode.UNSUPPORTED_PLATFORM,
) -> FactObservation[_T]:
    return FactObservation(None, code)


def _read_text(reader: Callable[[], str]) -> FactObservation[str]:
    try:
        value = reader()
    except PermissionError:
        return _unavailable(DiagnosticCode.PERMISSION_DENIED)
    except OSError as error:
        return _unavailable(_code_for_os_error(error))
    normalized = value.strip()
    if not normalized:
        return _unavailable()
    return FactObservation(normalized)


def _read_logical_processors() -> FactObservation[int]:
    try:
        value = os.cpu_count()
    except PermissionError:
        return _unavailable(DiagnosticCode.PERMISSION_DENIED)
    except OSError as error:
        return _unavailable(_code_for_os_error(error))
    if type(value) is not int or value <= 0:
        return _unavailable()
    return FactObservation(value)


def _code_for_os_error(error: OSError) -> DiagnosticCode:
    if isinstance(error, PermissionError):
        return DiagnosticCode.PERMISSION_DENIED
    if getattr(error, "winerror", None) == 5 or error.errno in {errno.EACCES, errno.EPERM}:
        return DiagnosticCode.PERMISSION_DENIED
    return DiagnosticCode.UNSUPPORTED_PLATFORM


def _read_windows_cpu_model() -> FactObservation[str]:
    if _winreg is None:
        return _unavailable()
    try:
        with _winreg.OpenKey(
            _winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        ) as key:
            raw_value, _ = _winreg.QueryValueEx(key, "ProcessorNameString")
    except PermissionError:
        return _unavailable(DiagnosticCode.PERMISSION_DENIED)
    except OSError as error:
        return _unavailable(_code_for_os_error(error))
    if not isinstance(raw_value, str) or not raw_value.strip():
        return _unavailable()
    return FactObservation(raw_value.strip())


def _read_windows_total_memory_bytes() -> FactObservation[int]:
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        return _unavailable()

    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(_MemoryStatusEx)
    try:
        kernel32 = loader("kernel32", use_last_error=True)
        global_memory_status_ex = kernel32.GlobalMemoryStatusEx
        global_memory_status_ex.argtypes = [ctypes.POINTER(_MemoryStatusEx)]
        global_memory_status_ex.restype = ctypes.c_int
        succeeded = global_memory_status_ex(ctypes.byref(status))
    except PermissionError:
        return _unavailable(DiagnosticCode.PERMISSION_DENIED)
    except (AttributeError, OSError) as error:
        if isinstance(error, OSError):
            return _unavailable(_code_for_os_error(error))
        return _unavailable()

    if not succeeded:
        get_last_error = getattr(ctypes, "get_last_error", None)
        last_error = get_last_error() if get_last_error is not None else 0
        if last_error == 5:
            return _unavailable(DiagnosticCode.PERMISSION_DENIED)
        return _unavailable()
    if status.ullTotalPhys <= 0:
        return _unavailable()
    return FactObservation(status.ullTotalPhys)


def inspect_local_system() -> SystemObservation:
    os_name = _read_text(platform.system)
    architecture = _read_text(platform.machine)
    logical_processors = _read_logical_processors()

    if os_name.value == "Windows":
        cpu_model = _read_windows_cpu_model()
        total_memory_bytes = _read_windows_total_memory_bytes()
    else:
        cpu_model = _read_text(platform.processor)
        total_memory_bytes = _unavailable()

    return SystemObservation(
        os_name=os_name,
        architecture=architecture,
        cpu_model=cpu_model,
        logical_processors=logical_processors,
        total_memory_bytes=total_memory_bytes,
    )


def _optional_result(
    check_id: str,
    observations: tuple[tuple[str, FactObservation[str] | FactObservation[int]], ...],
) -> DiagnosticResult:
    facts = tuple(
        DiagnosticFact(name, observation.value)
        for name, observation in observations
        if observation.value is not None
    )
    unavailable_code = next(
        (observation.code for _, observation in observations if observation.value is None),
        None,
    )
    if unavailable_code is None:
        return DiagnosticResult(
            check_id,
            DiagnosticStatus.PASS,
            f"{check_id.capitalize()} information available",
            facts=facts,
        )
    return DiagnosticResult(
        check_id,
        DiagnosticStatus.WARN,
        f"{check_id.capitalize()} information incomplete",
        facts=facts,
        code=unavailable_code,
    )


def system_results(
    observation: SystemObservation,
) -> tuple[DiagnosticResult, DiagnosticResult, DiagnosticResult]:
    platform_result = _optional_result(
        "platform",
        (("os_name", observation.os_name), ("architecture", observation.architecture)),
    )
    cpu_result = _optional_result(
        "cpu",
        (
            ("cpu_model", observation.cpu_model),
            ("logical_processors", observation.logical_processors),
        ),
    )
    memory_result = _optional_result(
        "memory", (("total_memory_bytes", observation.total_memory_bytes),)
    )
    return platform_result, cpu_result, memory_result
