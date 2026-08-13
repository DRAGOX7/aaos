# Phase 0F Doctor and Hardware Diagnostics Design

## Status and purpose

This specification defines the approved design for Phase 0F only. It is not an implementation plan and does not authorize production code, tests, dependencies, or later-phase work.

Phase 0F introduces a read-only `aaos doctor` command that establishes repository-defined runtime and local-inference invariants and reports useful local hardware and environment facts. The doctor diagnoses the environment; it does not manage, repair, or mutate it.

## Scope

Phase 0F will:

- expose one minimal `aaos doctor` command;
- run a fixed, ordered set of local diagnostics;
- represent each result with typed status and safe structured facts;
- validate the supported Python runtime and interpreter width;
- report platform, CPU, memory, and NVIDIA information where safely available;
- report `uv` availability and version as development-environment information;
- reuse `ModelGateway.check_availability()` to establish LM Studio reachability, configured-model availability, and loaded-context compatibility;
- compare observed hardware with the validated development profile without treating that profile as a portable requirement;
- convert expected inspection failures into controlled diagnostic results;
- remain deterministic and offline-testable where possible.

## Non-goals

Phase 0F does not include:

- environment repair, package installation, or OS configuration changes;
- hardware minimum recommendations or invented compatibility thresholds;
- model generation, benchmarking, throughput measurement, or concurrency guarantees;
- CUDA toolkit, PyTorch, or `lms` CLI validation;
- LM Studio startup, shutdown, JIT configuration, model download, load, unload, or reconfiguration;
- GPU configuration or resource-management actions;
- Docker validation before Docker configuration is introduced;
- database migration execution, writable-directory probes, or filesystem creation;
- report persistence, telemetry, dashboard, UI, HTTP routes, or background monitoring;
- Moodle, RAG, MCP, tools, agents, orchestration, approval flows, or any Phase 0G+ capability.

## Selected architecture

Phase 0F uses a typed diagnostic service with injected adapters:

```text
aaos doctor
  -> minimal standard-library CLI boundary
  -> Doctor service
       -> runtime inspector
       -> system/platform inspector
       -> fixed-command adapter
            -> uv --version
            -> fixed nvidia-smi query
       -> ModelGateway.check_availability()
  -> typed observations
  -> classification policy
  -> ordered DoctorReport
  -> local console rendering and process exit status
```

The doctor service owns check ordering, fallback policy, classification, aggregation, and cleanup. Inspectors establish facts but do not assign product policy. Rendering is separate from collection so tests can assert structured results without scraping console text.

The implementation must use the smallest standard-library and package-entry-point design that exposes `aaos doctor`. Phase 0F must not add Typer, Click, or another CLI framework. A later implementation may add only the necessary project script or entry-point configuration; that configuration is not a new runtime dependency.

### Dependency direction

Dependencies point toward typed, environment-independent results:

```text
CLI -> Doctor service -> result models
Doctor service -> inspector protocols + ModelGateway
platform adapters -> inspector protocols
subprocess adapter -> fixed command specifications
LMStudioGateway -> existing ModelGateway boundary
```

The result models and doctor policy must not import Windows, subprocess, HTTP, or LM Studio implementation details. The doctor depends on `ModelGateway`, not directly on LM Studio payloads or endpoints.

## Diagnostic result model

Each check result contains:

- a stable check identifier;
- one status: `PASS`, `WARN`, or `FAIL`;
- a short, safe summary;
- allowlisted typed facts relevant to that check;
- an optional stable diagnostic code for expected conditions such as missing command, permission denied, timeout, unsupported platform, malformed output, or dependency not evaluated.

The complete `DoctorReport` contains results in a deterministic order plus an aggregate status. It does not contain timestamps or timing measurements because Phase 0F has no performance requirement and those fields would weaken deterministic evidence.

### Status semantics

`PASS` means a required invariant or capability was positively established, or an informational check successfully established its fact.

`WARN` means AAOS may remain operational, but an optional diagnostic or capability is absent, the machine differs from the validated reference profile, or useful information could not be established.

`FAIL` means a repository-defined required Phase 0 invariant is violated, or a required capability cannot be established after all approved safe fallbacks.

A failed inspection mechanism is not automatically a failed diagnostic. For example, denied Windows CIM access does not fail a check if a safe standard-library, .NET, or registry fallback establishes the required fact. Failed intermediate mechanisms are not emitted as separate failures once the fact is established.

Expected environmental conditions become typed results rather than uncaught exceptions. Unexpected programming defects remain visible and must not be converted into a misleading healthy result.

### Aggregate status and exit behavior

- Any `FAIL` produces aggregate `FAIL` and process exit code `1`.
- Otherwise, any `WARN` produces aggregate `WARN` and process exit code `0`.
- Otherwise, the aggregate is `PASS` and the process exit code is `0`.
- Command-line usage errors use exit code `2`.

Reference-profile differences never control a failing exit status.

## Diagnostic checks

### Required invariants

| Check | Required condition | Failure behavior |
|---|---|---|
| Python runtime | Runtime is Python 3.11, matching `pyproject.toml` and the existing Phase 0 contract | Any other major/minor version is `FAIL` |
| Interpreter width | Python process is 64-bit, matching the existing Phase 0 contract | Any other width, or inability to establish this required fact, is `FAIL` |
| LM Studio reachability | The configured inference runtime returns a schema-valid availability result | Connection, timeout, protocol, or backend failure is `FAIL` |
| Configured model | The exact configured model is available and loaded | Absent or unloaded configured model is `FAIL` |
| Loaded context | At least one loaded instance has `loaded_context_length >= settings.lm_studio_context_length`, or the equivalent configured value passed through the approved configuration boundary | No qualifying loaded instance is `FAIL` |

The doctor must not encode `8192` as a policy constant. The configured context value is authoritative. The current default is 8192 tokens, but a valid configuration change automatically changes the required comparison without a doctor-code change.

When LM Studio is unreachable, dependent model and context checks are marked not evaluated rather than producing misleading duplicate failures. The reachability result already determines aggregate failure.

### Optional and informational checks

| Check | Purpose | Status policy |
|---|---|---|
| OS and architecture | Identify the local runtime platform | Established fact is `PASS`; unavailable fact is `WARN` |
| CPU model and logical processor count | Describe available compute resources | Differences and unavailable facts are `WARN`; no minimum exists |
| Total system RAM | Describe available memory | Differences and unavailable facts are `WARN`; no minimum exists |
| `uv` executable and version | Describe the development environment | Missing, denied, timed out, or malformed result is `WARN` |
| NVIDIA inspection | Describe NVIDIA runtime visibility | Missing or unavailable information is normally `WARN` |
| GPU model and total VRAM | Describe local accelerator resources | Any identity or capacity difference is warning-only |
| NVIDIA driver visibility | Describe the visible driver | Unavailable information is `WARN` |
| Current GPU resource data | Provide an operational snapshot when the approved query supports it | Unavailable information is `WARN`; no SLO follows from it |
| Reference-profile comparison | Compare with the validated development machine | Match may be `PASS`; mismatch or unknown is `WARN` |

`uv` is development and environment information. Ordinary AAOS runtime health must not depend on a globally installed `uv`. Lock reproducibility remains an explicit acceptance command outside the doctor.

## Validated reference profile

The validated development profile is:

| Resource | Observed value |
|---|---|
| CPU | Intel Core i7-12700H |
| System RAM | Approximately 16 GB |
| GPU | NVIDIA RTX 4050 Laptop GPU |
| Total VRAM | 6141 MiB |

This profile is operational evidence, not mandatory hardware identity and not evidence of portable minimum requirements. GPU, VRAM, CPU, or RAM differences do not fail the doctor. RAM cannot fail without a repository-supported minimum introduced through a later approved design.

The report may expose an equivalent of `reference_profile_match = match | mismatch | unknown` and may include per-resource comparison facts. Approximate RAM representation must not obscure otherwise useful observations.

## Platform inspection boundary

Portable information uses Python standard-library runtime and platform facilities. Platform-specific mechanisms remain isolated behind an injected system-inspector protocol.

Approved Windows read-only mechanisms may include:

- standard-library platform and runtime inspection;
- `ctypes` access to `GlobalMemoryStatusEx` for total RAM;
- read-only registry access for CPU identification;
- safe standard-library or .NET fallbacks where required.

WMI/CIM is neither mandatory nor a single source of truth. An access-denied or unavailable mechanism triggers the next approved safe fallback. The final result reflects whether the fact was established, not whether every mechanism succeeded.

On unsupported platforms, optional facts become typed `WARN` results. Required Python and inference invariants retain their normal policy. Platform adapters must not mutate OS state or require elevated permissions.

## Subprocess boundary

The fixed-command adapter may run only:

- `uv --version`;
- an approved fixed `nvidia-smi` query for allowlisted GPU facts.

Every invocation must use:

- a controlled executable discovery mechanism;
- a fixed executable and fixed argument array;
- `shell=False`;
- no caller-controlled command or argument;
- a short fixed timeout, initially five seconds;
- bounded stdout and stderr capture;
- explicit version or CSV parsing;
- a minimal, sanitized environment appropriate to executable discovery;
- deterministic termination on timeout.

Raw command output and stderr do not enter diagnostic results or logs. Missing executable, access denied, timeout, unsupported query, output beyond the allowed bound, non-success exit, or malformed output becomes a typed observation.

The `nvidia-smi` query may allowlist GPU name, total VRAM, driver version, and current resource fields required by the final approved implementation plan. NVIDIA information is valuable but not a universal AAOS hardware requirement. The doctor does not invoke CUDA toolkit utilities, PyTorch, `lms`, PowerShell scripts, or arbitrary shell commands.

## ModelGateway interaction and lifecycle

The doctor receives or constructs the existing approved configuration and uses the existing `ModelGateway` abstraction. The LM Studio composition boundary creates one dedicated async HTTP transport, supplies it exclusively to one `LMStudioGateway`, and guarantees cleanup in `finally`.

The model diagnostic flow is:

1. Call `check_availability()` exactly once.
2. Translate its typed value or safe inference exception into doctor results.
3. Compare returned loaded context lengths with the configured `lm_studio_context_length` value.
4. Close the gateway deterministically.

The doctor sends no generation request and makes no additional inventory request. It does not access native LM Studio payloads, duplicate the HTTP adapter, override the configured timeout, or perform model lifecycle actions.

The existing configuration boundary is the single source of truth for LM Studio base URL, model identifier, required context length, and request timeout. Phase 0F must not duplicate those values.

## Timeout and error behavior

Subprocess commands use the fixed five-second diagnostic timeout. LM Studio calls use the existing configured inference timeout.

The doctor converts these expected conditions into typed results:

- command missing;
- access or permission denied;
- subprocess timeout;
- subprocess non-success exit;
- unsupported platform or inspection mechanism;
- excessive or malformed subprocess output;
- inference connection, timeout, protocol, backend, and model-availability failures;
- malformed injected observations.

Phase 0F adds no retries. A retry could hide unstable availability, duplicate work, and lengthen an explicit local diagnostic without repository evidence.

## Security and privacy

All diagnostics are local, read-only, and explicitly invoked. Phase 0F must not:

- modify files, database state, application data, LM Studio state, GPU settings, or OS configuration;
- inspect `data/auth/`, `data/credentials/`, browser profiles, or academic documents;
- enumerate or render environment variables;
- read, emit, or persist credentials, cookies, authorization headers, tokens, prompts, completions, reasoning traces, or private academic content;
- transmit hardware or environment information externally;
- expose raw exceptions, subprocess output, HTTP bodies, or native backend payloads.

Hardware facts are rendered only to the invoking local console. Subprocess output and environment observations are untrusted inputs and must pass explicit parsing and allowlisting before they become report facts.

## Import-time invariants

Importing CLI, doctor, inspector, configuration, inference, or FastAPI modules must not:

- execute a subprocess;
- query hardware or the Windows registry;
- contact LM Studio or another network service;
- construct a network client;
- create files, directories, or database state;
- inspect credentials or private data.

All diagnostic effects begin only after explicit `aaos doctor` invocation. The FastAPI `/health` endpoint remains process-liveness only and does not run the doctor.

## Automated testing strategy

The later implementation plan must require deterministic tests for:

- diagnostic-result validation, stable ordering, aggregation, and exit codes;
- Python version and pointer-width boundaries;
- platform fallback success and exhausted-fallback behavior;
- CPU and RAM facts plus unavailable-result handling;
- fixed subprocess executable, arguments, `shell=False`, timeout, output bounds, and sanitized environment;
- missing, denied, timed-out, non-success, oversized, and malformed subprocess results;
- NVIDIA parsing, including zero, one, and multiple GPUs;
- reference-profile match, mismatch, and unknown behavior;
- fake `ModelGateway` outcomes for reachable, unreachable, unloaded, absent, sufficient-context, and insufficient-context states;
- context comparison against injected configuration values other than 8192, proving no duplicated policy constant;
- inference error translation and gateway cleanup;
- report redaction and exclusion of raw observations;
- minimal CLI output and exit behavior;
- isolated-process import safety.

Default tests must not depend on actual NVIDIA hardware, a global `uv` installation, Windows-only services, LM Studio, port 1234, network access, or mutable application data. Tests inject controlled inspectors, command results, and a fake `ModelGateway`.

## Manual acceptance criteria

With LM Studio manually started and the configured model manually loaded under the approved runtime profile:

1. `aaos doctor` executes without modifying system, model, or application state.
2. Python 3.11 and 64-bit runtime checks pass.
3. System and optional NVIDIA facts are reported or produce safe warnings.
4. Reference-profile comparison is visible and never controls failure.
5. LM Studio reachability is positively established.
6. The exact configured model is reported available and loaded.
7. At least one loaded instance reports a context length greater than or equal to `settings.lm_studio_context_length`.
8. With the current default profile, the displayed configured-context requirement is 8192 tokens without a duplicated doctor-policy constant.
9. The aggregate is `PASS` or `WARN`, and the process exits with code `0`.
10. Deterministic tests, Ruff formatting and linting, strict mypy, lock verification, import-safety, whitespace, and Git security checks pass.
11. Git tracks no credentials or authentication state.

Hard-failure behavior is demonstrated through deterministic injected tests. The doctor must not stop or reconfigure LM Studio to manufacture manual negative cases.

## Acceptance boundary

Phase 0F passes its sub-gate only when the approved implementation and evidence demonstrate:

1. One minimal `aaos doctor` entry point exists without a new CLI dependency.
2. Required runtime and configured-inference invariants use the approved `PASS`/`WARN`/`FAIL` policy.
3. Optional hardware and reference-profile differences cannot produce aggregate failure.
4. Safe fallbacks prevent an individual inspection-mechanism failure from becoming a false diagnostic failure.
5. Every expected environmental failure becomes a typed result rather than an uncaught exception.
6. LM Studio diagnostics reuse `ModelGateway.check_availability()` once and compare loaded context against the configured context value.
7. The implementation performs no generation, environment repair, model lifecycle action, external write, or import-time I/O.
8. Automated and manual acceptance evidence passes without exposing secrets or private data.

Passing Phase 0F does not pass Phase 0 as a whole. Remaining Phase 0 work and an explicit acceptance decision still govern subsequent work.

## Operational limits

- Hardware facts may be unavailable because the platform lacks an inspection mechanism or denies access; optional facts warn rather than crash.
- `nvidia-smi` output and supported query fields may vary by driver; incompatible data is a controlled warning.
- Current GPU utilization and free memory are transient operational observations, not guarantees.
- LM Studio may be stopped, blocked, or serving a different loaded profile; required inference checks fail explicitly without remediation.
- Concurrent doctor invocations duplicate subprocess and LM Studio diagnostic work. Phase 0F is an explicit local command and provides no concurrency coordination or throughput guarantee.
