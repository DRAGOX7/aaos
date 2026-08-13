# Phase 0F Doctor and Hardware Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only, offline-testable `aaos doctor` command that reports typed runtime, hardware, development-tool, and configured LM Studio diagnostics without managing the environment.

**Architecture:** A typed `DoctorService` receives injected runtime, system, command, NVIDIA, reference-profile, and `ModelGateway` boundaries. Inspectors establish facts; the service applies the approved `PASS`/`WARN`/`FAIL` policy, deterministic rendering remains separate, and a minimal standard-library CLI composes the production adapters.

**Tech Stack:** Python 3.11 standard library, existing `httpx2`/`ModelGateway`, pytest, Ruff, mypy, uv; no new runtime dependency and no CLI framework.

## Global Constraints

- Implement only approved Phase 0F behavior from `docs/superpowers/specs/2026-08-13-phase-0f-doctor-hardware-design.md` at commit `967d159bb3828212f7e01576bfa0b28d78325191`.
- Diagnostics are local, read-only, and explicitly invoked; they never repair or manage the environment.
- Add no Typer, Click, CUDA, PyTorch, `lms`, or other runtime dependency.
- Python 3.11 and a 64-bit interpreter are the only hardware/runtime identity requirements currently supported by repository evidence.
- CPU, RAM, GPU identity, and VRAM differences are warning-only; introduce no hardware minimum.
- Missing NVIDIA information is `WARN` unless a separately required AAOS capability fails.
- LM Studio reachability, exact configured-model availability, and at least one loaded context length meeting the configured requirement are required.
- Compare loaded context with `settings.lm_studio_context_length`; never encode 8192 as a production doctor-policy constant.
- Use `ModelGateway.check_availability()` exactly once per doctor run, send no generation request, create no second LM Studio client, and close the exclusively owned gateway deterministically.
- Run only fixed `uv --version` and fixed `nvidia-smi` queries through the subprocess boundary; use fixed arrays, `shell=False`, a five-second timeout, bounded output, sanitized environment, explicit parsing, and no raw-output exposure.
- Add no retry, persistent doctor report, dashboard, HTTP endpoint, telemetry, model lifecycle action, or Phase 0G+ capability.
- Imports perform no network, subprocess, registry, hardware, persistence, or filesystem effects.
- Default automated tests remain offline and require no installed `uv`, `nvidia-smi`, NVIDIA hardware, LM Studio, port 1234, or network access.
- Status and exit semantics are exact: any `FAIL` -> aggregate `FAIL`/exit 1; `WARN` without `FAIL` -> aggregate `WARN`/exit 0; all `PASS` -> aggregate `PASS`/exit 0; CLI usage error -> exit 2.
- Never expose credentials, environment-variable contents, raw exceptions, subprocess stdout/stderr, HTTP bodies, prompts, completions, reasoning traces, or private academic data.
- Each implementation task follows RED -> minimal GREEN -> focused verification -> review -> focused commit. Stage only files listed by that task.
- After each task, write an ignored workflow report at `.superpowers/sdd/2026-08-13-phase-0f-doctor-hardware-implementation/task-N-report.md` listing files changed, commands executed, tests executed, failures or limitations, and the recommended next action. These reports are execution evidence, not runtime doctor reports and are never staged.
- Use command-scoped `git -c safe.directory="C:/Users/aljaf/aaos" ...` for every Git command if ownership requires it.

---

## Proposed file structure

```text
src/aaos/
├── cli.py                         # minimal argv parsing, composition, rendering, exit
└── doctor/
    ├── __init__.py                # approved public doctor exports only
    ├── models.py                  # result/status/code and observation value types
    ├── runtime.py                 # Python version and pointer-width inspection
    ├── system.py                  # fixed platform/CPU/RAM readers and unavailable results
    ├── commands.py                # fixed subprocess execution and uv parsing
    ├── nvidia.py                  # fixed NVIDIA query and CSV parsing
    ├── reference.py               # validated-profile comparison only
    ├── model_check.py             # ModelGateway availability-to-result translation
    ├── service.py                 # ordered checks, classification, aggregation
    └── rendering.py               # deterministic safe console text

tests/
├── test_doctor_models.py
├── test_doctor_runtime.py
├── test_doctor_system.py
├── test_doctor_commands.py
├── test_doctor_nvidia.py
├── test_doctor_reference.py
├── test_doctor_model_check.py
├── test_doctor_service.py
├── test_doctor_cli.py
└── test_doctor_imports.py

pyproject.toml                     # only [project.scripts] aaos = "aaos.cli:main"
uv.lock                            # refreshed only if uv records entry-point metadata changes

.superpowers/sdd/2026-08-13-phase-0f-doctor-hardware-implementation/
└── *.md                           # ignored execution/acceptance evidence, never product output
```

No file combines collection, policy, rendering, and CLI composition. `models.py` is the single source of truth for shared names; every later task consumes the signatures below without renaming or parallel substitutes.

## Frozen cross-task interfaces

Task 1 defines these shared types:

```python
from typing import Generic, TypeAlias, TypeVar

class DiagnosticStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"

class DiagnosticCode(StrEnum):
    UNSUPPORTED_RUNTIME = "unsupported_runtime"
    NON_64_BIT_RUNTIME = "non_64_bit_runtime"
    MISSING_COMMAND = "missing_command"
    PERMISSION_DENIED = "permission_denied"
    TIMEOUT = "timeout"
    UNSUPPORTED_PLATFORM = "unsupported_platform"
    MALFORMED_OUTPUT = "malformed_output"
    OVERSIZED_OUTPUT = "oversized_output"
    COMMAND_FAILED = "command_failed"
    NOT_EVALUATED = "not_evaluated"
    CONNECTION_FAILED = "connection_failed"
    PROTOCOL_FAILED = "protocol_failed"
    BACKEND_FAILED = "backend_failed"
    MODEL_UNAVAILABLE = "model_unavailable"
    UNSUPPORTED_INFERENCE = "unsupported_inference"
    INFERENCE_FAILED = "inference_failed"
    INSUFFICIENT_CONTEXT = "insufficient_context"
    REFERENCE_MISMATCH = "reference_mismatch"
    REFERENCE_UNKNOWN = "reference_unknown"

FactValue: TypeAlias = str | int | bool

@dataclass(frozen=True, slots=True)
class DiagnosticFact:
    name: str
    value: FactValue

@dataclass(frozen=True, slots=True)
class DiagnosticResult:
    check_id: str
    status: DiagnosticStatus
    summary: str
    facts: tuple[DiagnosticFact, ...] = ()
    code: DiagnosticCode | None = None

@dataclass(frozen=True, slots=True)
class DoctorReport:
    results: tuple[DiagnosticResult, ...]

    @property
    def status(self) -> DiagnosticStatus: ...

    @property
    def exit_code(self) -> int: ...

T = TypeVar("T")

@dataclass(frozen=True, slots=True)
class FactObservation(Generic[T]):
    value: T | None
    code: DiagnosticCode | None = None

@dataclass(frozen=True, slots=True)
class RuntimeObservation:
    python_major: int
    python_minor: int
    pointer_width_bits: int

@dataclass(frozen=True, slots=True)
class SystemObservation:
    os_name: FactObservation[str]
    architecture: FactObservation[str]
    cpu_model: FactObservation[str]
    logical_processors: FactObservation[int]
    total_memory_bytes: FactObservation[int]

@dataclass(frozen=True, slots=True)
class CommandOutput:
    text: str | None
    code: DiagnosticCode | None = None

@dataclass(frozen=True, slots=True)
class UvObservation:
    version: str | None
    code: DiagnosticCode | None = None

@dataclass(frozen=True, slots=True)
class GpuObservation:
    name: str
    total_vram_mib: int
    driver_version: str
    used_vram_mib: int | None = None
    free_vram_mib: int | None = None

@dataclass(frozen=True, slots=True)
class NvidiaObservation:
    gpus: tuple[GpuObservation, ...]
    code: DiagnosticCode | None = None

class ReferenceMatch(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"

@dataclass(frozen=True, slots=True)
class ReferenceObservation:
    match: ReferenceMatch
    facts: tuple[DiagnosticFact, ...]
```

The implementation imports `Generic`, `TypeAlias`, and `TypeVar` from `typing`; all displayed signatures are valid for the governed Python 3.11 runtime.

Shared protocols and functions are introduced by the named tasks:

```python
class RuntimeInspector(Protocol):
    def inspect(self) -> RuntimeObservation: ...

class LocalRuntimeInspector:
    def inspect(self) -> RuntimeObservation: ...

class SystemInspector(Protocol):
    def inspect(self) -> SystemObservation: ...

class LocalSystemInspector:
    def inspect(self) -> SystemObservation: ...

class CommandInspector(Protocol):
    def inspect_uv(self) -> UvObservation: ...
    def inspect_nvidia_output(self) -> CommandOutput: ...

class NvidiaInspector(Protocol):
    def inspect(self) -> NvidiaObservation: ...

class ReferenceInspector(Protocol):
    def compare(self, system: SystemObservation, nvidia: NvidiaObservation) -> ReferenceObservation: ...

class LocalReferenceInspector:
    def compare(self, system: SystemObservation, nvidia: NvidiaObservation) -> ReferenceObservation: ...

def inspect_runtime() -> RuntimeObservation: ...
def inspect_local_system() -> SystemObservation: ...
def parse_uv_version(output: CommandOutput) -> UvObservation: ...
def parse_nvidia_output(output: CommandOutput) -> NvidiaObservation: ...
def compare_reference_profile(system: SystemObservation, nvidia: NvidiaObservation) -> ReferenceObservation: ...
def model_diagnostic_results(gateway: ModelGateway, settings: AppSettings) -> Awaitable[tuple[DiagnosticResult, ...]]: ...
def render_report(report: DoctorReport) -> str: ...
def run_cli(argv: Sequence[str], run_doctor: Callable[[], DoctorReport]) -> int: ...
def main() -> int: ...
```

`DoctorService` has one constructor and one operation:

```python
class DoctorService:
    def __init__(
        self,
        *,
        settings: AppSettings,
        runtime_inspector: RuntimeInspector,
        system_inspector: SystemInspector,
        command_inspector: CommandInspector,
        nvidia_inspector: NvidiaInspector,
        reference_inspector: ReferenceInspector,
        model_gateway: ModelGateway,
        close_model_gateway: Callable[[], Awaitable[None]],
    ) -> None: ...

    async def run(self) -> DoctorReport: ...
```

`NvidiaInspector` production composition wraps `CommandInspector.inspect_nvidia_output()` and `parse_nvidia_output()`. Tests may inject direct fake inspectors. The service owns exactly one cleanup call through `close_model_gateway`, in `finally`, after exactly one availability operation.

---

### Task 0: Verify the approved baseline

**Files:**
- Read: `AGENTS.md`
- Read: `docs/superpowers/specs/2026-08-13-phase-0f-doctor-hardware-design.md`
- Read: `src/aaos/config.py`
- Read: `src/aaos/inference/gateway.py`
- Read: `src/aaos/inference/lm_studio.py`
- Modify: none

**Interfaces:**
- Consumes: approved spec at `967d159bb3828212f7e01576bfa0b28d78325191` and existing `AppSettings`, `ModelGateway`, `LMStudioGateway`.
- Produces: verified clean baseline; no code or commit.

- [ ] **Step 1: Verify repository and approved design identity**

```powershell
git -c safe.directory="C:/Users/aljaf/aaos" status --short --branch
git -c safe.directory="C:/Users/aljaf/aaos" rev-parse HEAD
git -c safe.directory="C:/Users/aljaf/aaos" show --no-patch --oneline 967d159bb3828212f7e01576bfa0b28d78325191
```

Expected: clean `main`; the approved design commit exists. If the tree is dirty or the approved spec differs, stop before production modification and report the mismatch.

- [ ] **Step 2: Verify the locked environment before production modification**

```powershell
uv lock --check
uv sync --locked --all-groups
uv run python -c "import sys; print(sys.version_info[:2]); print(sys.maxsize > 2**32)"
```

Expected: lock and sync pass; output establishes `(3, 11)` and `True`. If the locked environment cannot be created, stop before modifying production files.

- [ ] **Step 3: Record baseline gates**

```powershell
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest -v
git -c safe.directory="C:/Users/aljaf/aaos" diff --check
```

Expected: deterministic baseline passes with only the existing opt-in live LM Studio test skipped. Record exact counts in the Task 0 report.

---

### Task 1: Define diagnostic domain models and aggregation

**Files:**
- Create: `src/aaos/doctor/__init__.py`
- Create: `src/aaos/doctor/models.py`
- Create: `tests/test_doctor_models.py`

**Interfaces:**
- Consumes: Python 3.11 dataclasses, `StrEnum`, `Generic`, `TypeAlias`, and `TypeVar` only.
- Produces: every model in the frozen cross-task interface, including `DiagnosticStatus`, `DiagnosticCode`, `DiagnosticFact`, `DiagnosticResult`, `DoctorReport`, observation types, and `ReferenceMatch`.

- [ ] **Step 1: Write failing model tests**

Create tests proving:

```python
def test_report_status_and_exit_code_follow_failure_precedence() -> None:
    warning = DiagnosticResult("uv", DiagnosticStatus.WARN, "uv unavailable")
    failure = DiagnosticResult("python", DiagnosticStatus.FAIL, "unsupported runtime")
    report = DoctorReport((warning, failure))
    assert report.status is DiagnosticStatus.FAIL
    assert report.exit_code == 1

def test_warning_report_exits_zero() -> None:
    report = DoctorReport((DiagnosticResult("gpu", DiagnosticStatus.WARN, "unknown"),))
    assert report.status is DiagnosticStatus.WARN
    assert report.exit_code == 0

def test_all_pass_report_exits_zero() -> None:
    report = DoctorReport((DiagnosticResult("python", DiagnosticStatus.PASS, "ok"),))
    assert report.status is DiagnosticStatus.PASS
    assert report.exit_code == 0
```

Also test rejection of empty reports, duplicate `check_id` values, blank fact names/summaries, a `PASS` with an error code, an unavailable `FactObservation` without a code, an available fact with a code, negative numeric hardware facts, and `NvidiaObservation` containing both GPUs and an error code.

- [ ] **Step 2: Run RED**

```powershell
uv run pytest tests/test_doctor_models.py -v
```

Expected: collection fails because `aaos.doctor` does not exist.

- [ ] **Step 3: Implement the frozen immutable models**

Implement the exact names in “Frozen cross-task interfaces.” Enforce these invariants in `__post_init__`:

- identifiers, summaries, names, and string fact values are non-blank;
- `PASS` has no diagnostic code; `WARN` and `FAIL` have a code;
- `DoctorReport.results` is non-empty and has unique check identifiers;
- `FactObservation.value` is present exactly when `code` is absent;
- runtime integers are non-negative;
- CPU count, RAM, VRAM, and GPU resource values are positive when present;
- a successful `NvidiaObservation` contains at least one GPU; a failed observation contains none;
- aggregate precedence is `FAIL`, then `WARN`, then `PASS`.

Export only shared public models from `doctor/__init__.py`; importing it performs no work.

- [ ] **Step 4: Run GREEN and static checks**

```powershell
uv run pytest tests/test_doctor_models.py -v
uv run ruff format --check src/aaos/doctor tests/test_doctor_models.py
uv run ruff check src/aaos/doctor tests/test_doctor_models.py
uv run mypy src/aaos/doctor tests/test_doctor_models.py
```

Expected: all pass.

- [ ] **Step 5: Review and commit**

Confirm the module imports no subprocess, platform, registry, HTTP, persistence, or LM Studio adapter. Then:

```powershell
git -c safe.directory="C:/Users/aljaf/aaos" add -- src/aaos/doctor/__init__.py src/aaos/doctor/models.py tests/test_doctor_models.py
git -c safe.directory="C:/Users/aljaf/aaos" diff --cached --check
git -c safe.directory="C:/Users/aljaf/aaos" commit -m "feat: define doctor diagnostic models"
```

---

### Task 2: Add runtime inspection and required-runtime policy

**Files:**
- Create: `src/aaos/doctor/runtime.py`
- Create: `tests/test_doctor_runtime.py`

**Interfaces:**
- Consumes: `RuntimeObservation`, `DiagnosticResult`, `DiagnosticStatus`, `DiagnosticCode`, and `DiagnosticFact` from Task 1.
- Produces: `RuntimeInspector`, `LocalRuntimeInspector`, `inspect_runtime() -> RuntimeObservation`, and `runtime_results(observation: RuntimeObservation) -> tuple[DiagnosticResult, DiagnosticResult]` in the fixed order `python_runtime`, `python_width`.

- [ ] **Step 1: Write failing runtime tests**

Test exact cases:

```python
def test_python_311_and_64_bit_pass() -> None:
    results = runtime_results(RuntimeObservation(3, 11, 64))
    assert [result.status for result in results] == [DiagnosticStatus.PASS] * 2

@pytest.mark.parametrize("version", [(3, 10), (3, 12), (4, 0)])
def test_unsupported_python_version_fails(version: tuple[int, int]) -> None:
    result = runtime_results(RuntimeObservation(*version, pointer_width_bits=64))[0]
    assert result.status is DiagnosticStatus.FAIL
    assert result.code is DiagnosticCode.UNSUPPORTED_RUNTIME

def test_non_64_bit_runtime_fails() -> None:
    result = runtime_results(RuntimeObservation(3, 11, 32))[1]
    assert result.status is DiagnosticStatus.FAIL
    assert result.code is DiagnosticCode.NON_64_BIT_RUNTIME
```

Also test that `inspect_runtime()` reports `sys.version_info[:2]` and `struct.calcsize("P") * 8` using monkeypatched reader callables; no subprocess is permitted.

- [ ] **Step 2: Run RED**

```powershell
uv run pytest tests/test_doctor_runtime.py -v
```

Expected: import fails because `aaos.doctor.runtime` does not exist.

- [ ] **Step 3: Implement runtime inspection and classification**

Define:

```python
@runtime_checkable
class RuntimeInspector(Protocol):
    def inspect(self) -> RuntimeObservation: ...

class LocalRuntimeInspector:
    def inspect(self) -> RuntimeObservation:
        return inspect_runtime()

def inspect_runtime() -> RuntimeObservation: ...

def runtime_results(
    observation: RuntimeObservation,
) -> tuple[DiagnosticResult, DiagnosticResult]: ...
```

Use only `sys.version_info` and `struct.calcsize`. Summaries and facts expose version and width only. Exact policy is Python `(3, 11)` and width `64`.

- [ ] **Step 4: Run GREEN and static checks**

```powershell
uv run pytest tests/test_doctor_runtime.py -v
uv run ruff format --check src/aaos/doctor/runtime.py tests/test_doctor_runtime.py
uv run ruff check src/aaos/doctor/runtime.py tests/test_doctor_runtime.py
uv run mypy src/aaos/doctor/runtime.py tests/test_doctor_runtime.py
```

- [ ] **Step 5: Review and commit**

Confirm no hardware identity or version range was invented and no import-time inspection occurs.

```powershell
git -c safe.directory="C:/Users/aljaf/aaos" add -- src/aaos/doctor/runtime.py tests/test_doctor_runtime.py
git -c safe.directory="C:/Users/aljaf/aaos" diff --cached --check
git -c safe.directory="C:/Users/aljaf/aaos" commit -m "feat: add doctor runtime checks"
```

---

### Task 3: Add system inspection with fixed platform readers

**Files:**
- Create: `src/aaos/doctor/system.py`
- Create: `tests/test_doctor_system.py`

**Interfaces:**
- Consumes: `FactObservation`, `SystemObservation`, `DiagnosticResult`, `DiagnosticStatus`, `DiagnosticCode`, and `DiagnosticFact`.
- Produces: `SystemInspector`, `LocalSystemInspector`, `inspect_local_system() -> SystemObservation`, and `system_results(observation: SystemObservation) -> tuple[DiagnosticResult, DiagnosticResult, DiagnosticResult]` ordered as `platform`, `cpu`, `memory`.

- [ ] **Step 1: Write failing platform/system tests**

Cover:

- OS name and architecture from monkeypatched `platform.system` and `platform.machine` readers;
- CPU model and logical count success;
- total RAM success;
- unavailable CPU or RAM becomes `WARN`, never an exception;
- unsupported platform becomes typed optional `WARN` facts;
- Windows CPU registry access denied produces `FactObservation(value=None, code=PERMISSION_DENIED)` and a `WARN` CPU result;
- Windows `GlobalMemoryStatusEx` access/failure produces a typed unavailable memory observation and `WARN` result;
- blank `platform.processor()` output on non-Windows produces `UNSUPPORTED_PLATFORM` rather than an invented fallback;
- `os.cpu_count()` returning `None` produces typed unavailable logical-processor information;
- no CPU identity or RAM amount controls `FAIL`.

Freeze tests around the exact production readers listed in Step 3 by monkeypatching the named module functions and the private Windows reader functions. There is no generic `inspect_first_success` helper because no Phase 0F fact has two approved production readers.

- [ ] **Step 2: Run RED**

```powershell
uv run pytest tests/test_doctor_system.py -v
```

Expected: import fails because `aaos.doctor.system` does not exist.

- [ ] **Step 3: Implement explicit read-only inspection**

Define `SystemInspector`, `LocalSystemInspector.inspect()` as a thin call to `inspect_local_system()`, and the exact public functions above. The production reader sequence is fixed per fact:

1. **OS name:** call `platform.system()` once. Blank output becomes `FactObservation(value=None, code=UNSUPPORTED_PLATFORM)`. No fallback.
2. **Architecture:** call `platform.machine()` once. Blank output becomes `FactObservation(value=None, code=UNSUPPORTED_PLATFORM)`. No fallback.
3. **Logical processors:** call `os.cpu_count()` once. `None` or a non-positive value becomes `FactObservation(value=None, code=UNSUPPORTED_PLATFORM)`. No fallback.
4. **CPU model on Windows:** open only `HKEY_LOCAL_MACHINE\\HARDWARE\\DESCRIPTION\\System\\CentralProcessor\\0` with `winreg.OpenKey`, then read only `ProcessorNameString` with `winreg.QueryValueEx`. Missing/blank data becomes `UNSUPPORTED_PLATFORM`; `PermissionError` becomes `PERMISSION_DENIED`. No second Windows CPU reader.
5. **CPU model on non-Windows:** call `platform.processor()` once. Missing/blank data becomes `UNSUPPORTED_PLATFORM`. No fallback.
6. **Total RAM on Windows:** call the standard-library `ctypes` binding for kernel32 `GlobalMemoryStatusEx` once and read `ullTotalPhys`. A false return, zero value, unsupported symbol, or expected OS error becomes a typed unavailable observation (`UNSUPPORTED_PLATFORM` or `PERMISSION_DENIED` as applicable). No fallback.
7. **Total RAM on non-Windows:** return `FactObservation(value=None, code=UNSUPPORTED_PLATFORM)`. Phase 0F introduces no POSIX-specific RAM reader.

Each source is read only when `inspect_local_system()` is explicitly invoked. No fact uses WMI/CIM, PowerShell, .NET, a third-party system dependency, registry writes, elevation, or an unspecified platform reader. Since every fact has one approved source for its platform, a source failure yields a typed optional warning rather than triggering an invented fallback or an overall `FAIL`.

- [ ] **Step 4: Run GREEN and static checks**

```powershell
uv run pytest tests/test_doctor_system.py -v
uv run ruff format --check src/aaos/doctor/system.py tests/test_doctor_system.py
uv run ruff check src/aaos/doctor/system.py tests/test_doctor_system.py
uv run mypy src/aaos/doctor/system.py tests/test_doctor_system.py
```

- [ ] **Step 5: Review and commit**

Confirm all OS/CPU/RAM paths are read-only and optional information cannot fail the aggregate.

```powershell
git -c safe.directory="C:/Users/aljaf/aaos" add -- src/aaos/doctor/system.py tests/test_doctor_system.py
git -c safe.directory="C:/Users/aljaf/aaos" diff --cached --check
git -c safe.directory="C:/Users/aljaf/aaos" commit -m "feat: add portable system diagnostics"
```

---

### Task 4: Add the fixed subprocess boundary and uv diagnostic

**Files:**
- Create: `src/aaos/doctor/commands.py`
- Create: `tests/test_doctor_commands.py`

**Interfaces:**
- Consumes: `CommandOutput`, `UvObservation`, `DiagnosticCode`.
- Produces: `CommandInspector`, `FixedCommandInspector`, `parse_uv_version(output: CommandOutput) -> UvObservation`, constants `COMMAND_TIMEOUT_SECONDS = 5.0` and `MAX_COMMAND_OUTPUT_BYTES = 16_384`.

`FixedCommandInspector` exposes only:

```python
class FixedCommandInspector:
    def inspect_uv(self) -> UvObservation: ...
    def inspect_nvidia_output(self) -> CommandOutput: ...
```

Its private runner accepts only module-owned command specifications. No public arbitrary-command method exists.

- [ ] **Step 1: Write failing subprocess tests**

Use injected executable discovery and process execution functions. Assert exact calls:

```python
UV_ARGS = ("--version",)
NVIDIA_ARGS = (
    "--query-gpu=name,memory.total,driver_version,memory.used,memory.free",
    "--format=csv,noheader,nounits",
)
```

Cover:

- successful `uv 0.12.3 (...)` parsing to version `0.12.3`;
- missing executable -> `MISSING_COMMAND`;
- `PermissionError` -> `PERMISSION_DENIED`;
- `subprocess.TimeoutExpired` -> `TIMEOUT` and termination through `subprocess.run(..., timeout=5.0)`;
- nonzero exit -> `COMMAND_FAILED`;
- malformed uv text -> `MALFORMED_OUTPUT`;
- encoded output over 16,384 bytes -> `OVERSIZED_OUTPUT` before parsing;
- runner receives a fixed absolute executable, list arguments, `shell=False`, `capture_output=True`, `text=False`, `check=False`, timeout 5.0, and a sanitized environment containing only the minimal Windows executable-discovery variables (`PATH`, `PATHEXT`, `SYSTEMROOT`, `WINDIR`) that were present;
- stderr and native exception strings never appear in `CommandOutput`.

- [ ] **Step 2: Run RED**

```powershell
uv run pytest tests/test_doctor_commands.py -v
```

Expected: import fails because `aaos.doctor.commands` does not exist.

- [ ] **Step 3: Implement the two-command allowlist**

Use `shutil.which` for controlled discovery and `subprocess.run` with the exact options above. Decode stdout as strict UTF-8 only after enforcing the byte bound. Map expected `FileNotFoundError`, `PermissionError`, `TimeoutExpired`, nonzero return, invalid UTF-8, and malformed version into the approved codes. Discard stderr and do not retain exception text.

The `CommandInspector` protocol contains only `inspect_uv` and `inspect_nvidia_output`. No caller can supply an executable or argument.

- [ ] **Step 4: Run GREEN and static checks**

```powershell
uv run pytest tests/test_doctor_commands.py -v
uv run ruff format --check src/aaos/doctor/commands.py tests/test_doctor_commands.py
uv run ruff check src/aaos/doctor/commands.py tests/test_doctor_commands.py
uv run mypy src/aaos/doctor/commands.py tests/test_doctor_commands.py
```

- [ ] **Step 5: Security review and commit**

Confirm fixed arguments, `shell=False`, output bounds, safe environment allowlist, no stderr retention, no `lms`, and no arbitrary public runner.

```powershell
git -c safe.directory="C:/Users/aljaf/aaos" add -- src/aaos/doctor/commands.py tests/test_doctor_commands.py
git -c safe.directory="C:/Users/aljaf/aaos" diff --cached --check
git -c safe.directory="C:/Users/aljaf/aaos" commit -m "feat: add bounded doctor command inspection"
```

---

### Task 5: Parse NVIDIA observations without hardware assumptions

**Files:**
- Create: `src/aaos/doctor/nvidia.py`
- Create: `tests/test_doctor_nvidia.py`

**Interfaces:**
- Consumes: `CommandInspector.inspect_nvidia_output()`, `CommandOutput`, `GpuObservation`, `NvidiaObservation`, and diagnostic models.
- Produces: `NvidiaInspector`, `CommandNvidiaInspector(command_inspector: CommandInspector)`, `parse_nvidia_output(output: CommandOutput) -> NvidiaObservation`, and `nvidia_result(observation: NvidiaObservation) -> DiagnosticResult` with check id `nvidia`.

- [ ] **Step 1: Write failing NVIDIA tests**

Use exact CSV rows with five fields:

```text
NVIDIA GeForce RTX 4050 Laptop GPU, 6141, 610.62, 4781, 1360
```

Cover:

- one GPU success;
- two GPU rows preserve backend order;
- zero/blank rows -> `MALFORMED_OUTPUT`;
- missing command, permission denied, timeout, command failure, and oversized output propagate as typed `NvidiaObservation` and warning result;
- wrong column count, blank name/driver, non-integer/negative memory, or used/free values individually exceeding total -> `MALFORMED_OUTPUT`;
- a different GPU model or VRAM amount remains successful observation data and never fails;
- raw CSV and sentinel stderr/exception strings never appear in `DiagnosticResult.summary` or facts.

- [ ] **Step 2: Run RED**

```powershell
uv run pytest tests/test_doctor_nvidia.py -v
```

Expected: import fails because `aaos.doctor.nvidia` does not exist.

- [ ] **Step 3: Implement strict CSV parsing and warning policy**

Parse with the standard-library `csv` module. Validate exactly five columns and explicit integer constraints. `CommandNvidiaInspector.inspect()` calls `inspect_nvidia_output()` once and parses it. `nvidia_result` returns `PASS` for established GPU facts and `WARN` with the observation code for unavailable/malformed optional NVIDIA data.

- [ ] **Step 4: Run GREEN and static checks**

```powershell
uv run pytest tests/test_doctor_nvidia.py -v
uv run ruff format --check src/aaos/doctor/nvidia.py tests/test_doctor_nvidia.py
uv run ruff check src/aaos/doctor/nvidia.py tests/test_doctor_nvidia.py
uv run mypy src/aaos/doctor/nvidia.py tests/test_doctor_nvidia.py
```

- [ ] **Step 5: Review and commit**

Confirm this task adds parsing only—no CUDA/PyTorch checks, hardware minimum, or direct subprocess call.

```powershell
git -c safe.directory="C:/Users/aljaf/aaos" add -- src/aaos/doctor/nvidia.py tests/test_doctor_nvidia.py
git -c safe.directory="C:/Users/aljaf/aaos" diff --cached --check
git -c safe.directory="C:/Users/aljaf/aaos" commit -m "feat: add NVIDIA doctor diagnostics"
```

---

### Task 6: Compare the validated reference profile

**Files:**
- Create: `src/aaos/doctor/reference.py`
- Create: `tests/test_doctor_reference.py`

**Interfaces:**
- Consumes: `SystemObservation`, `NvidiaObservation`, `ReferenceObservation`, `ReferenceMatch`, and diagnostic models.
- Produces: `ReferenceInspector`, `LocalReferenceInspector`, `compare_reference_profile(system, nvidia) -> ReferenceObservation`, and `reference_result(observation: ReferenceObservation) -> DiagnosticResult` with check id `reference_profile`.

- [ ] **Step 1: Write failing reference tests**

Cover exact classifications:

- observed i7-12700H, approximately 16 GB RAM, RTX 4050 Laptop GPU, and 6141 MiB -> `MATCH`/`PASS`;
- any CPU, RAM, GPU, or VRAM difference -> `MISMATCH`/`WARN` with `REFERENCE_MISMATCH`;
- any unavailable comparison input -> `UNKNOWN`/`WARN` with `REFERENCE_UNKNOWN`;
- a multi-GPU machine matches if one observed GPU matches the reference GPU name and VRAM; additional GPUs do not fail or force mismatch;
- no reference comparison result ever has `FAIL` or exit-control behavior;
- facts expose only normalized observed/reference comparison values, never raw command output.

Use a documented RAM comparison tolerance solely to recognize the approximate 16 GB reference observation. The tolerance is reference matching metadata, not an operational minimum.

- [ ] **Step 2: Run RED**

```powershell
uv run pytest tests/test_doctor_reference.py -v
```

Expected: import fails because `aaos.doctor.reference` does not exist.

- [ ] **Step 3: Implement comparison-only policy**

Define reference constants only in this module:

```python
REFERENCE_CPU = "Intel(R) Core(TM) i7-12700H"
REFERENCE_RAM_BYTES = 16_788_852_736
REFERENCE_GPU = "NVIDIA GeForce RTX 4050 Laptop GPU"
REFERENCE_VRAM_MIB = 6141
REFERENCE_RAM_TOLERANCE_BYTES = 512 * 1024 * 1024
```

The RAM tolerance answers only “does this observation resemble the measured profile?” It must never be imported by required-capability policy. Normalize whitespace, case, trademark punctuation, and the optional processor-generation prefix so the Windows registry value `12th Gen Intel(R) Core(TM) i7-12700H` matches the approved reference identity without rewriting displayed facts. `LocalReferenceInspector.compare()` delegates to `compare_reference_profile()`.

- [ ] **Step 4: Run GREEN and static checks**

```powershell
uv run pytest tests/test_doctor_reference.py -v
uv run ruff format --check src/aaos/doctor/reference.py tests/test_doctor_reference.py
uv run ruff check src/aaos/doctor/reference.py tests/test_doctor_reference.py
uv run mypy src/aaos/doctor/reference.py tests/test_doctor_reference.py
```

- [ ] **Step 5: Review and commit**

Search for any comparison constant outside `reference.py` and confirm mismatch/unknown is warning-only.

```powershell
rg -n "12700H|4050|6141|16_788_852_736" src tests
git -c safe.directory="C:/Users/aljaf/aaos" add -- src/aaos/doctor/reference.py tests/test_doctor_reference.py
git -c safe.directory="C:/Users/aljaf/aaos" diff --cached --check
git -c safe.directory="C:/Users/aljaf/aaos" commit -m "feat: compare doctor reference profile"
```

---

### Task 7: Translate ModelGateway availability into required diagnostics

**Files:**
- Create: `src/aaos/doctor/model_check.py`
- Create: `tests/test_doctor_model_check.py`

**Interfaces:**
- Consumes: existing `AppSettings`, `ModelGateway`, `ModelAvailability`, and every existing safe Phase 0E inference error; Task 1 diagnostic models.
- Produces: `async model_diagnostic_results(gateway, settings) -> tuple[DiagnosticResult, DiagnosticResult, DiagnosticResult]`, ordered as `lm_studio`, `configured_model`, `loaded_context`.

- [ ] **Step 1: Write failing gateway diagnostic tests**

Use a fake `ModelGateway` whose `check_availability()` records call count and whose `generate()` raises if called. Cover:

- reachable/available configured model with `loaded_context_lengths=(8192,)` and configured requirement 4096 passes all three checks;
- configured requirement 12,288 with loaded lengths `(8192, 16384)` passes, proving comparison uses settings rather than a literal 8192;
- available model with every loaded context below the configured requirement fails only `loaded_context` with `INSUFFICIENT_CONTEXT`;
- reachable but unavailable/unloaded model passes reachability, fails configured model, and marks context `WARN`/`NOT_EVALUATED`;
- exact configured-model identity comes from `settings.lm_studio_model`;
- each safe Phase 0E category maps to fixed redacted results: `InferenceConnectionError`, `InferenceTimeoutError`, `ModelUnavailableError`, `InferenceProtocolError`, `InferenceBackendError`, `UnsupportedInferenceFeatureError`, and base `InferenceError`;
- every thrown availability error yields one required `FAIL` and dependent `WARN`/`NOT_EVALUATED` results;
- `check_availability()` is called exactly once and `generate()` is never called;
- sentinel prompt, body, header, token, native exception, and traceback strings do not appear in summaries or facts.

- [ ] **Step 2: Run RED**

```powershell
uv run pytest tests/test_doctor_model_check.py -v
```

Expected: import fails because `aaos.doctor.model_check` does not exist.

- [ ] **Step 3: Implement fixed safe translation**

Implement one awaited `check_availability()` call. Use fixed summaries and allowlisted fields only. Map error categories to `CONNECTION_FAILED`, `TIMEOUT`, `MODEL_UNAVAILABLE`, `PROTOCOL_FAILED`, `BACKEND_FAILED`, `UNSUPPORTED_INFERENCE`, and `INFERENCE_FAILED`. Never inspect raw LM Studio payloads or call an endpoint directly.

Use:

```python
context_ok = any(
    loaded >= settings.lm_studio_context_length
    for loaded in availability.loaded_context_lengths
)
```

No literal `8192` is permitted in `src/aaos/doctor/model_check.py`.

- [ ] **Step 4: Run GREEN and static checks**

```powershell
uv run pytest tests/test_doctor_model_check.py -v
uv run ruff format --check src/aaos/doctor/model_check.py tests/test_doctor_model_check.py
uv run ruff check src/aaos/doctor/model_check.py tests/test_doctor_model_check.py
uv run mypy src/aaos/doctor/model_check.py tests/test_doctor_model_check.py
rg -n "8192|generate\(" src/aaos/doctor/model_check.py
```

Expected: tests/static checks pass; `rg` prints no matches.

- [ ] **Step 5: Review and commit**

Confirm only the existing protocol is used, exactly once, with no client ownership added here.

```powershell
git -c safe.directory="C:/Users/aljaf/aaos" add -- src/aaos/doctor/model_check.py tests/test_doctor_model_check.py
git -c safe.directory="C:/Users/aljaf/aaos" diff --cached --check
git -c safe.directory="C:/Users/aljaf/aaos" commit -m "feat: add model availability doctor checks"
```

---

### Task 8: Aggregate ordered diagnostics and guarantee gateway cleanup

**Files:**
- Create: `src/aaos/doctor/service.py`
- Create: `tests/test_doctor_service.py`
- Modify: `src/aaos/doctor/__init__.py`

**Interfaces:**
- Consumes: all frozen inspector protocols; `runtime_results`, `system_results`, `nvidia_result`, `compare_reference_profile`, `reference_result`, `model_diagnostic_results`; `AppSettings`; injected gateway closer.
- Produces: exact frozen `DoctorService` constructor and `async run() -> DoctorReport`.

- [ ] **Step 1: Write failing service tests**

Inject deterministic fakes and assert exact check order:

```python
EXPECTED_ORDER = (
    "python_runtime",
    "python_width",
    "platform",
    "cpu",
    "memory",
    "uv",
    "nvidia",
    "reference_profile",
    "lm_studio",
    "configured_model",
    "loaded_context",
)
```

Cover:

- all pass gives aggregate `PASS`/exit 0;
- optional uv/NVIDIA/reference warnings give aggregate `WARN`/exit 0;
- runtime or model failure gives aggregate `FAIL`/exit 1;
- `inspect_uv()` is translated to `uv` pass/warn without raw output;
- one failed mechanism already recovered inside a system observation does not add a failure;
- `check_availability()` occurs exactly once;
- closer runs exactly once after success and after every mapped inference error;
- an unexpected collector programming error still runs closer and then propagates;
- no collector executes during service construction.

- [ ] **Step 2: Run RED**

```powershell
uv run pytest tests/test_doctor_service.py -v
```

Expected: import fails because `aaos.doctor.service` does not exist.

- [ ] **Step 3: Implement sequential deterministic aggregation**

Collect local observations in the approved fixed order. Parse uv through its already typed `UvObservation`; convert success to a `PASS` result and unavailable status to `WARN`. Construct the reference observation from the already collected system and NVIDIA observations. Await `model_diagnostic_results()` once inside `try/finally`, then await `close_model_gateway()` exactly once.

Do not use concurrency, retries, persistence, logging of raw values, or exception-wide fallback. Update `doctor/__init__.py` to export only `DoctorService`, `DoctorReport`, `DiagnosticResult`, and `DiagnosticStatus`.

- [ ] **Step 4: Run GREEN and static checks**

```powershell
uv run pytest tests/test_doctor_service.py -v
uv run pytest tests -v
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
```

- [ ] **Step 5: Review and commit**

Confirm the order is stable, optional checks cannot fail, and cleanup is deterministic.

```powershell
git -c safe.directory="C:/Users/aljaf/aaos" add -- src/aaos/doctor/__init__.py src/aaos/doctor/service.py tests/test_doctor_service.py
git -c safe.directory="C:/Users/aljaf/aaos" diff --cached --check
git -c safe.directory="C:/Users/aljaf/aaos" commit -m "feat: aggregate doctor diagnostics"
```

---

### Task 9: Add deterministic rendering and the minimal CLI entry point

**Files:**
- Create: `src/aaos/doctor/rendering.py`
- Create: `src/aaos/cli.py`
- Create: `tests/test_doctor_cli.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock` only if `uv lock --check` proves entry-point metadata changed

**Interfaces:**
- Consumes: `DoctorReport`, `DiagnosticResult`, `DEFAULT_SETTINGS`, `LMStudioGateway`, `FixedCommandInspector`, production runtime/system/NVIDIA/reference inspectors, and `httpx2.AsyncHTTPTransport`.
- Produces: `render_report(report) -> str`, `run_cli(argv: Sequence[str], run_doctor: Callable[[], DoctorReport]) -> int`, `main() -> int`, and project script `aaos = "aaos.cli:main"`.

- [ ] **Step 1: Write failing renderer and CLI tests**

Cover:

- deterministic one-line header plus one line per result in report order;
- facts rendered in tuple order with stable `name=value` formatting;
- output contains statuses, safe summaries, and allowlisted facts only;
- sentinel raw stdout, stderr, exception, prompt, response body, token, and environment values never appear;
- `run_cli(["doctor"], fake_runner)` prints once and returns report exit code for PASS, WARN, and FAIL;
- empty argv, unknown command, or extra arguments prints fixed usage to stderr and returns `2` without invoking the doctor;
- `main()` production composition creates exactly one dedicated `AsyncHTTPTransport`, one `LMStudioGateway`, and passes `gateway.aclose` into `DoctorService`;
- imports alone do not construct the transport or run any inspector.

- [ ] **Step 2: Run RED**

```powershell
uv run pytest tests/test_doctor_cli.py -v
```

Expected: imports fail because rendering and CLI modules do not exist.

- [ ] **Step 3: Implement rendering and standard-library argv parsing**

Use only `sys.argv`, `print`, and `asyncio.run`; do not add `argparse` subcommand machinery unless it remains smaller than direct exact-argument comparison. Required behavior:

```python
def run_cli(argv: Sequence[str], run_doctor: Callable[[], DoctorReport]) -> int:
    if list(argv) != ["doctor"]:
        print("usage: aaos doctor", file=sys.stderr)
        return 2
    report = run_doctor()
    print(render_report(report))
    return report.exit_code
```

`main()` passes a private synchronous `_run_configured_doctor() -> DoctorReport` callback to `run_cli`. That callback uses `asyncio.run()` around a private async composition function which creates `LMStudioGateway(DEFAULT_SETTINGS, httpx2.AsyncHTTPTransport())`, injects `LocalRuntimeInspector`, `LocalSystemInspector`, `FixedCommandInspector`, `CommandNvidiaInspector`, and `LocalReferenceInspector` into `DoctorService`, and relies on the service's `finally` cleanup. It returns the integer from `run_cli`; the console-script wrapper handles process exit. Add only:

```toml
[project.scripts]
aaos = "aaos.cli:main"
```

Run `uv lock --check`. If it reports metadata drift, run `uv lock`, inspect the lock diff, and include `uv.lock`; do not change any dependency declaration.

- [ ] **Step 4: Run GREEN and CLI checks**

```powershell
uv run pytest tests/test_doctor_cli.py -v
uv run aaos
uv run aaos unknown
uv run ruff format --check src/aaos/cli.py src/aaos/doctor/rendering.py tests/test_doctor_cli.py
uv run ruff check src/aaos/cli.py src/aaos/doctor/rendering.py tests/test_doctor_cli.py
uv run mypy src/aaos/cli.py src/aaos/doctor/rendering.py tests/test_doctor_cli.py
uv lock --check
```

Expected: tests pass; both invalid invocations print exactly `usage: aaos doctor` and exit 2 without running diagnostics; lock passes.

- [ ] **Step 5: Review and commit**

Confirm no new dependency, framework, HTTP route, global client, import-time effect, or persistent output.

```powershell
git -c safe.directory="C:/Users/aljaf/aaos" add -- src/aaos/cli.py src/aaos/doctor/rendering.py tests/test_doctor_cli.py pyproject.toml
git -c safe.directory="C:/Users/aljaf/aaos" add -- uv.lock
git -c safe.directory="C:/Users/aljaf/aaos" diff --cached --check
git -c safe.directory="C:/Users/aljaf/aaos" diff --cached --stat
git -c safe.directory="C:/Users/aljaf/aaos" commit -m "feat: expose the AAOS doctor command"
```

If `uv.lock` is unchanged, do not stage it. Before committing, verify the staged file list contains no other path.

---

### Task 10: Prove import safety and whole Phase 0F behavior

**Files:**
- Create: `tests/test_doctor_imports.py`
- Modify: none

**Interfaces:**
- Consumes: installed `aaos` package, CLI and doctor imports, injected process/network/file probes.
- Produces: isolated-process evidence that imports are inert; no new public production interface.

- [ ] **Step 1: Write failing isolated import-safety tests**

Launch `sys.executable -I -B -c` with the repository `src` path inserted explicitly, following the existing persistence import-safety pattern. Import:

- `aaos.doctor`;
- every `aaos.doctor.*` module;
- `aaos.cli`;
- `aaos.main`.

Run in a disposable directory and assert:

- exit code zero;
- no stdout/stderr;
- no files/directories created;
- no database created;
- a sentinel fake `nvidia-smi`, `uv`, and LM Studio endpoint are never contacted.

Add a source-level AST test that fails if module-scope code calls `subprocess.run`, `shutil.which`, registry readers, `inspect_*`, `AsyncHTTPTransport`, `LMStudioGateway`, or `DoctorService.run`.

- [ ] **Step 2: Run RED**

```powershell
uv run pytest tests/test_doctor_imports.py -v
```

Expected: the test may pass immediately because Tasks 1–9 already established import safety. Record that outcome as regression coverage validating the existing behavior. If it fails naturally, identify the exact import effect and follow Step 3. Do not inject a production defect to manufacture a RED state.

- [ ] **Step 3: Resolve the RED result without broadening Task 10**

If the test fails naturally because of a genuine production import effect, stop Task 10 and report the exact source file and call before modifying it; revise the owning task's scoped test/fix steps under human review. Do not weaken the import test or silently expand this task's file list. If the test passes immediately, continue to the deterministic gates.

- [ ] **Step 4: Run focused and complete deterministic gates**

```powershell
uv run pytest tests/test_doctor_imports.py -v
uv lock --check
uv sync --locked --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest -v
git -c safe.directory="C:/Users/aljaf/aaos" diff --check
git -c safe.directory="C:/Users/aljaf/aaos" status --short --branch
git -c safe.directory="C:/Users/aljaf/aaos" ls-files data/auth data/credentials
```

Expected: every deterministic gate passes; the normal suite contacts no real hardware/network; the existing opt-in LM Studio smoke remains skipped unless separately enabled; credential query prints nothing.

- [ ] **Step 5: Whole-change scope and security review**

Review the complete Phase 0F range and prove:

```powershell
git -c safe.directory="C:/Users/aljaf/aaos" diff --stat 967d159bb3828212f7e01576bfa0b28d78325191..HEAD
git -c safe.directory="C:/Users/aljaf/aaos" diff --name-status 967d159bb3828212f7e01576bfa0b28d78325191..HEAD
rg -n "8192|subprocess|shell=True|lms|torch|cuda|generate\(|httpx2.AsyncClient|Typer|Click" src/aaos/doctor src/aaos/cli.py tests
```

Interpret matches rather than deleting valid test fixtures. Production policy must have no `8192`, `generate()`, `lms`, CUDA/PyTorch, CLI framework, arbitrary HTTP client, or `shell=True` match. Fixed subprocess calls must exist only in `commands.py`.

- [ ] **Step 6: Commit the import-safety evidence**

```powershell
git -c safe.directory="C:/Users/aljaf/aaos" add -- tests/test_doctor_imports.py
git -c safe.directory="C:/Users/aljaf/aaos" diff --cached --check
git -c safe.directory="C:/Users/aljaf/aaos" commit -m "test: enforce doctor import safety"
```

This commit contains only the import-safety test. A production import defect follows the stop-and-review rule in Step 3.

---

### Task 11: Run manual acceptance and produce Phase 0F evidence

**Files:**
- Modify: none by default
- Create ignored workflow report: `.superpowers/sdd/2026-08-13-phase-0f-doctor-hardware-implementation/final-evidence-report.md`

**Interfaces:**
- Consumes: installed `aaos doctor`, manually prepared LM Studio, committed Phase 0F implementation.
- Produces: acceptance evidence only; no product interface or persistent runtime doctor report.

- [ ] **Step 1: Verify external prerequisites without managing them**

The user must start LM Studio, disable JIT model loading, and manually load the configured model with the configured context and approved GPU offload. AAOS must not change these settings or model state.

- [ ] **Step 2: Run the real command once**

```powershell
uv run aaos doctor
$LASTEXITCODE
```

Expected on the validated machine: exit `0`; aggregate `PASS` or `WARN`; Python 3.11 and 64-bit pass; local hardware facts are safe; exact configured model is loaded; at least one loaded context is greater than or equal to `DEFAULT_SETTINGS.lm_studio_context_length`. A warning for optional inspection does not invalidate the run.

If prerequisites are absent, do not start/load/download/reconfigure anything. Record the precise external blocker and leave the manual sub-gate unpassed.

- [ ] **Step 3: Re-run final deterministic gates after the real command**

```powershell
uv lock --check
uv sync --locked --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest -v
git -c safe.directory="C:/Users/aljaf/aaos" diff --check
git -c safe.directory="C:/Users/aljaf/aaos" status --short --branch
```

Expected: all pass and the tracked tree remains clean, proving the doctor did not mutate repository state.

- [ ] **Step 4: Write the evidence report**

Record:

1. exact commits and files in Phase 0F;
2. deterministic command outputs and test counts;
3. real `aaos doctor` aggregate status and exit code;
4. allowlisted safe hardware/model facts only;
5. confirmation that configured context came from settings;
6. confirmation of one availability call, no generation, and cleanup evidence;
7. failures, warnings, or external blockers;
8. tracked Git status and credential check;
9. explicit statement that Phase 0F and Phase 0 remain unpassed until human acceptance.

- [ ] **Step 5: Stop at the human acceptance gate**

Do not create a tag, claim Phase 0 passed, update status documents to claim acceptance, or begin Phase 0G. Ask the user to review the implementation commits and evidence report.

---

## Final acceptance checklist

- [ ] The changed-file set matches the proposed structure and contains no Phase 0G+ file.
- [ ] `aaos doctor` uses the standard library/package entry point with no CLI dependency.
- [ ] All results use the frozen typed model and deterministic order.
- [ ] PASS/WARN/FAIL aggregation and exit codes match the approved policy exactly.
- [ ] Python 3.11 and 64-bit are required; no other hardware minimum exists.
- [ ] Reference-profile mismatch/unknown and missing optional NVIDIA data remain warning-only.
- [ ] Only fixed `uv --version` and fixed `nvidia-smi` queries cross the bounded subprocess seam.
- [ ] Subprocess discovery, fixed arrays, `shell=False`, timeout, termination, output bound, sanitized environment, and safe parsing are tested.
- [ ] No raw stdout/stderr, exception detail, environment value, credential, prompt, response body, or private data is exposed.
- [ ] Model diagnostics call `check_availability()` exactly once and never call `generate()`.
- [ ] Loaded context is compared only with `settings.lm_studio_context_length`; production doctor code contains no literal `8192` policy.
- [ ] `LMStudioGateway` remains the only LM Studio HTTP adapter and its owned transport is closed deterministically.
- [ ] Imports perform no network, subprocess, registry, hardware, persistence, or filesystem work.
- [ ] Default tests are offline and require no local executable, GPU, LM Studio, or network.
- [ ] `uv lock --check` and `uv sync --locked --all-groups` pass.
- [ ] `uv run ruff format --check .` and `uv run ruff check .` pass.
- [ ] `uv run mypy src tests` passes.
- [ ] `uv run pytest -v` passes with the real-machine checks opt-in/manual.
- [ ] `git diff --check`, credential tracking, and tracked status are clean.
- [ ] A real `aaos doctor` run passes or its exact external blocker is documented.
- [ ] The evidence report requests human acceptance and does not advance to Phase 0G.
