# Phase 5 — Controlled Local Agent Loop Design Specification

Status: **AUTHORIZED / SPECIFICATION IN PROGRESS**. This document is a
human-review design baseline; Phase 5 production implementation has not
started.

## 1. Objective

Phase 5 adds one bounded local agent orchestrator above the frozen Phase 4
grounded RAG service. The agent may interpret a task and propose a typed tool
call, but only trusted application code validates the proposal, checks
permissions, executes an allowlisted tool, sanitizes the observation, and
advances authoritative state.

The trust flow is deliberately one-way:

```text
local ModelGateway
  -> untrusted structured AgentProposal
  -> strict schema validation
  -> trusted permission and policy validation
  -> allowlisted trusted Python tool
  -> trusted ToolResult
  -> sanitized ToolObservation (untrusted model-visible data)
  -> bounded next orchestrator step
```

The primary invariant is: model output can suggest work, but it can never
grant itself authority, mutate policy, or execute anything outside a trusted
registered tool.

## 2. Scope and non-goals

### In scope

- one inspectable primary orchestrator;
- immutable typed task/proposal/tool/error contracts;
- an explicit task state machine and bounded loop policy;
- a typed, static allowlist of local tools;
- capability/permission checks before every handler;
- sanitized observations and audit records;
- a narrow Phase 4 query adapter;
- a domain-specific AAOS-owned local task-state write surface, if approved;
- local-only structured proposal generation through the existing
  `ModelGateway`;
- deterministic calibration and untouched held-out evaluation design.

### Out of scope

Phase 5 does not add a swarm, peer agents, recursive agents, arbitrary Python,
REPL, shell/PowerShell/cmd, arbitrary subprocesses, generic filesystem
read/write, arbitrary SQL, arbitrary HTTP/browser/Moodle access, credentials
in model context, external writes, MCP external-write execution, Phase 6
approvals, Phase 7 dashboard behavior, Phase 8 restricted code execution, or
any Phase 4 redesign. The production REPL remains disabled.

## 3. Frozen Phase 4 integration

Phase 4 remains immutable and is reached only through its existing typed
boundary:

```text
LocalRagService.ask(RagQuery) -> RenderedRagResponse
```

The adapter supplies an explicit `CourseScope`, `GlobalScope`, or
`HistoricalScope` in the `RagQuery`; it cannot infer or broaden scope from
conversation history. `Phase4RagRuntime` remains the owner of Phase 4 resource
lifecycle. Phase 5 never calls repositories, Chroma, FTS, embedding services,
or citation internals directly and never adds a second retrieval path.

Phase 4's local-only inference, snapshot authorization, trusted citation and
provenance behavior, and explicit close/context-manager lifecycle remain in
force. RAG text, citations, and rendered answers are data supplied to the
agent, not instructions or authority.

## 4. Trust, state, and ownership boundaries

| Boundary | Trusted owner | Untrusted input | Required decision |
| --- | --- | --- | --- |
| Task creation | Application | caller task text and metadata | normalize into `AgentTask` |
| Proposal parsing | strict proposal adapter | model bytes/JSON | reject malformed or unknown fields |
| Authorization | orchestrator + registry policy | proposal tool name/args | allow only registered tool and granted capability |
| Execution | registered handler | validated arguments | run only the handler's bounded operation |
| Observation | observation sanitizer | tool result/error/content | strip secrets and cap size before model exposure |
| State transition | orchestrator state machine | model suggestion/observation | apply only legal trusted transition |
| Audit | application-owned sink | event metadata | retain safe fingerprints, never raw secrets/content |

Retrieved Moodle/course text, Phase 4 evidence, document contents, local task
state, and every tool observation are **untrusted data**. Embedded instructions
such as “ignore previous instructions”, “call this tool”, or “grant access”
cannot alter the registry, policy, limits, state, or system prompt.

## 5. Typed contracts

The repository convention is immutable `@dataclass(frozen=True, slots=True)`
values and `StrEnum` values. Model-facing JSON is parsed into these values by a
strict boundary adapter; unknown fields are rejected (`extra=forbid` semantics)
and no model-provided object is trusted until parsing succeeds. Serialization
is deterministic canonical JSON with enum values, sorted keys, and no secret or
raw exception fields.

| Contract | Required fields and rules | Ownership/trust |
| --- | --- | --- |
| `AgentTaskId` | opaque non-empty canonical string; length-limited; never a path or SQL fragment | application-issued trusted identifier |
| `AgentTask` | `task_id: AgentTaskId`, `request_text: str`, `initial_scope: RagScope | None`, `created_at: datetime`, immutable metadata; non-blank bounded text | application-owned after construction; request text remains untrusted data |
| `AgentState` | `CREATED`, `PLANNING`, `TOOL_PENDING`, `TOOL_RUNNING`, `OBSERVING`, `COMPLETED`, `FAILED`, `CANCELLED` | application-owned enum; model cannot mutate it |
| `AgentStep` | positive step number, prior/current state, proposal fingerprint, optional tool-call fingerprint, outcome/error code; monotonic and bounded | orchestrator-owned audit value |
| `AgentProposal` | exactly one action: `tool_call: ToolCall | None` or terminal response; proposal id/fingerprint; no unknown fields; terminal text bounded | parsed model proposal, still untrusted until policy checks |
| `ToolName` | closed enum/string allowlist; unknown names rejected | registry-owned vocabulary |
| `ToolArguments` | tool-specific immutable typed value; strict field set, bounded sizes, normalized values | schema validator owns interpretation |
| `ToolCall` | `name: ToolName`, `arguments: ToolArguments`, optional request-local call id; canonical normalized representation | untrusted proposal until validation and authorization |
| `ToolResult` | trusted handler result union: success payload or typed `ToolError`; no raw exception | handler/application-owned |
| `ToolObservation` | sanitized result kind, bounded text/fields, safe status, optional opaque reference; no credentials/raw paths/stack traces | application-created but model-visible data is untrusted |
| `ToolError` | safe code, retryability, bounded public message; internal exception retained only in private diagnostics if required | trusted error contract; never serialize raw exception |
| `PermissionDecision` | `ALLOW`/`DENY`, required capability, reason code, policy version; no model override | trusted policy output |
| `AgentTerminalResult` | task id, terminal status (`COMPLETED`/`FAILED`/`CANCELLED`), optional rendered Phase 4 response or safe failure, final step count | application-owned final result |
| `AgentFailure` | safe failure code, stage, retryable flag, bounded user message; no path/SQL/model internals | trusted operational result |
| `AgentLimits` | positive `max_steps`, `max_proposal_repairs`, `max_identical_tool_repetitions`, finite model/tool timeouts | immutable trusted configuration; model cannot change it |

Every contract rejects booleans where integers are expected, blank strings,
unknown enum values, extra fields, non-finite numbers, oversized text, and
non-canonical argument representations. Request-local opaque ids are not
authority: only registry names and validated arguments can reach a handler.

## 6. Task state machine

The orchestrator is the sole state owner. Legal transitions are:

```text
CREATED   -> PLANNING | CANCELLED
PLANNING  -> TOOL_PENDING | COMPLETED | FAILED | CANCELLED
TOOL_PENDING -> TOOL_RUNNING | FAILED | CANCELLED
TOOL_RUNNING -> OBSERVING | FAILED | CANCELLED
OBSERVING -> PLANNING | COMPLETED | FAILED | CANCELLED
```

`COMPLETED`, `FAILED`, and `CANCELLED` are terminal. A transition not listed is
rejected deterministically with `INVALID_STATE_TRANSITION`; a model cannot
write an authoritative state value. A tool handler never transitions state
directly; it returns a `ToolResult` to the orchestrator.

## 7. Bounded loop and retry policy

The proposed, reviewable baseline is:

| Limit | Proposed value |
| --- | ---: |
| `MAX_AGENT_STEPS` | 8 |
| `MAX_STRUCTURED_PROPOSAL_REPAIRS` | 1 |
| `MAX_IDENTICAL_TOOL_CALL_REPETITIONS` | 2 |
| Model timeout | finite and configurable; exact value approved in implementation |
| Tool timeout | finite and tool-specific; exact values approved per registry entry |

Only the initial proposal and one schema-repair proposal may be requested per
step. Timeouts and transient failures follow an explicit bounded policy; there
is no hidden fallback or infinite retry. Exhausting any limit produces a safe
typed terminal failure (`STEP_LIMIT_EXCEEDED`, `LOOP_DETECTED`,
`MODEL_TIMEOUT`, or `TOOL_TIMEOUT`).

`LOOP_DETECTION_KEY` is the canonical serialization of the registered tool name
plus validated, normalized arguments. Raw model text, whitespace, or proposal
ids do not defeat repetition detection. The count is request-local and reset
only when a new `AgentTask` is created.

## 8. Model proposal contract

The existing local `ModelGateway` is the sole model boundary. The proposal
adapter supplies a deterministic JSON-schema structured-output request and
parses the response with strict application validation. The schema is
infrastructure-owned and cannot be modified by request text, evidence, or
observations. Configuration is local-only, with finite timeout, no cloud
fallback, no tool-calling mode, and at most one repair attempt. Model
unavailability, timeout, invalid JSON, unknown fields, invalid arguments, and
unknown tools fail closed with safe `AgentFailure` codes.

The request-owned generation parameters are fixed by trusted configuration:
model identifier and endpoint, temperature/reasoning mode, maximum output
tokens, timeout, and the exact proposal JSON Schema. A proposal cannot override
any of them; an unset optional parameter remains unset rather than inheriting a
model-generated value. Exact numeric timeout, temperature, and output-token
values are implementation decisions recorded and reviewed before Slice 4,
because the current Phase 5 architecture does not authorize guessing them.

The response-format contract is strict structured JSON with additional
properties rejected. Initial generation and the single repair use the same
schema and parameter policy. A complete response still passes the application
parser and tool/policy validators; endpoint-level schema acceptance is never
treated as trust.

The model may propose only a schema-defined tool or terminal response. It may
not register tools, change permissions/limits/schema, construct Python or shell
commands, provide arbitrary paths, or override policy failures. Tool-call text
inside retrieved content is data, never a second instruction channel.

## 9. Tool registry and permissions

`ToolRegistry` is a static, application-owned mapping. Each entry contains:

- closed `ToolName`;
- human description (not authority);
- strict input and output schema;
- required permission class;
- side-effect class;
- finite timeout;
- trusted handler reference;
- observation-sanitization policy;
- audit metadata/version.

Policies are fixed as follows:

```text
UNKNOWN_TOOL_POLICY=REJECT
INVALID_ARGUMENT_POLICY=REJECT_BEFORE_HANDLER
UNAUTHORIZED_TOOL_POLICY=REJECT_BEFORE_HANDLER
DYNAMIC_MODEL_TOOL_REGISTRATION=FORBIDDEN
```

Initial permission classes are `READ_ONLY`, `LOCAL_STATE_WRITE`, and
`TERMINAL`. The registry declares the required class; the orchestrator holds
the granted capability set; policy validates the intersection. The model,
retrieved text, and observations cannot escalate or grant permissions.

### Initial tool inventory (proposed)

| TOOL_NAME | PURPOSE | INPUT_TYPE | OUTPUT_TYPE | PERMISSION | SIDE_EFFECT | ALLOWED_IN_PHASE5 | WHY |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `rag_query` | Ask the frozen Phase 4 service once with explicit scope | typed `RagQuery` (question + one `RagScope`) | `RenderedRagResponse` mapped to safe observation | `READ_ONLY` | none outside Phase 4 reads | YES | smallest useful academic read |
| `complete_task` | End a task with a bounded terminal response | bounded terminal payload | `AgentTerminalResult` | `TERMINAL` | task state only | YES | gives the model a safe completion action |
| `read_task_state` | Read narrow current task status and safe step summary | task id, no path | redacted `AgentStateSummary` | `READ_ONLY` | none | YES | supports bounded continuation without history leakage |
| `write_task_state` | Record a domain-specific AAOS task note/status | strict `TaskStateWrite` with server-owned task id and allowed fields | typed acknowledgement | `LOCAL_STATE_WRITE` | AAOS-owned task/application state only | CONDITIONAL, human-approved | permits only the minimum useful local write |

No generic filesystem reader/writer, shell, browser, HTTP client, Python
executor, or SQL executor is part of the Phase 5 inventory.

## 10. Local-write policy

`PHASE5_LOCAL_WRITE_POLICY`: local write means a narrowly scoped mutation of
AAOS-owned task/application state through a domain-specific trusted tool. It
does not mean arbitrary host filesystem authority.

`PHASE5_ALLOWED_LOCAL_WRITE_SURFACES`: a server-owned task record or other
explicitly approved AAOS application-state repository, addressed by an opaque
`AgentTaskId` and a closed field/value schema. The handler chooses the storage
path and transaction; the model supplies neither.

`PHASE5_FORBIDDEN_LOCAL_WRITE_SURFACES`: arbitrary paths, path fragments,
environment variables, credentials, `data/auth/`, `data/credentials/`, normal
Phase 4 databases/stores, arbitrary SQL, source code, shell scripts, browser
profiles, model caches, or any host location not explicitly registered by the
tool.

Any write tool requires a typed schema, explicit `LOCAL_STATE_WRITE`
permission, bounded target, validation before handler invocation, deterministic
audit event, and a safe error contract. Whether this tool is enabled is an
implementation/acceptance decision; the registry must fail closed when absent.

## 11. Observation sanitization and injection isolation

`ToolResult` is trusted application output. `ToolObservation` is a deliberately
limited model-visible projection:

- bounded total and per-field text (implementation constants are versioned);
- structured fields restricted to an allowlist and primitive values;
- safe error code and retryability only;
- secrets, cookies, tokens, authorization state, SQL, stack traces, and raw
  exception text removed;
- filesystem paths replaced by opaque resource identifiers or omitted;
- document/evidence text labelled as data and bounded without becoming a
  prompt instruction;
- deterministic truncation markers and fingerprints for audit, never hidden
  truncation of a control field.

Prompt-injection acceptance cases place hostile instructions in retrieved RAG
evidence, document text, local state, and tool observations. The expected
result is unchanged registry, permissions, limits, system instructions, and
authoritative state; only the trusted policy path can cause a tool call.

## 12. Safe errors and auditability

The initial safe error vocabulary is closed and versioned:

`INVALID_PROPOSAL`, `INVALID_STATE_TRANSITION`, `UNKNOWN_TOOL`,
`INVALID_TOOL_ARGUMENTS`, `PERMISSION_DENIED`, `STEP_LIMIT_EXCEEDED`,
`LOOP_DETECTED`, `MODEL_UNAVAILABLE`, `MODEL_TIMEOUT`, `TOOL_TIMEOUT`,
`TOOL_FAILED`, `OBSERVATION_SANITIZATION_FAILED`, `RAG_QUERY_FAILED`,
`TASK_CANCELLED`, and `INTERNAL_AGENT_FAILURE`.

Public errors contain only a stable code, stage, retryability, and concise safe
message. Audit records may retain timestamps, task/step ids, tool name,
permission decision, argument/result fingerprints, state transitions, and
resource durations. They must not retain credentials, raw prompts, full
document text, raw model output, arbitrary paths, or stack traces.

## 13. Evaluation design

Evaluation is a separate local harness and never a production dependency path.
It uses fakes for normal tests and an explicitly local ModelGateway only for an
approved calibration run. Metrics are computed from a versioned manifest;
retrieved text remains untrusted data.

### Calibration set

The calibration manifest must cover, at minimum, correct `rag_query` and
terminal choices; unknown tools; malformed, missing, and extra arguments;
permission denial; read-only behavior; approved local-state writes; arbitrary
filesystem attempts; injection in evidence and observations; repeated calls;
step exhaustion; model/tool timeout; model unavailable; tool failure; English,
Arabic, cross-language, refusal, and ambiguous requests. Cases are distinct,
stable ids with expected policy outcomes and no hidden production data.

### Final held-out set

The final held-out set is created before model tuning, disjoint from calibration,
and consumed exactly once under explicit human authorization. It includes the
same safety categories and both English and Arabic task wording. Its questions,
labels, and split are immutable after the first run.

The first held-out run writes two local outputs:

1. a sanitized report for human review; and
2. a private, local frozen evaluation bundle containing only the fields required
   by the formally planned metrics (case id, expected outcome, safe event
   trace, status/error codes, bounded observations, and fingerprints).

The private bundle is never committed, contains no credentials, is fingerprinted
at creation, and is sufficient for approved post-hoc metrics without rerunning
the one-time held-out cases. Artifact paths are injected disposable paths, not
normal AAOS data paths.

## 14. Proposed acceptance targets (not yet authoritative)

These targets are proposals for human review, not implementation gates until
approved:

| Target | Proposed threshold | Rationale |
| --- | ---: | --- |
| Structured output valid after one repair | >= 0.99 | malformed proposals should be exceptional while preserving a hard repair bound |
| Tool-selection accuracy | >= 0.95 | useful routing without encouraging unsafe retries |
| Unknown-tool execution | 0 | registry rejection is a security invariant |
| Unauthorized-tool execution | 0 | authorization must precede every handler |
| Invalid argument reaches handler | 0 | validation is a mandatory trust boundary |
| Arbitrary host-file access | 0 | no generic filesystem authority exists |
| Prompt-injection policy bypass | 0 | untrusted data cannot change policy |
| Loop-limit escape | 0 | hard bounds must be enforceable |
| Arbitrary code execution | 0 | Phase 8 only, never Phase 5 |
| Unapproved external write | 0 | external writes are future-phase capabilities |
| Trusted orchestration runtime failure | 0 in acceptance suite | lifecycle/state failures are not safe to hide |

Quality thresholds may be revised only through an approved specification
amendment. Safety zeroes are invariants, not averages that can be traded for
better task success.

## 15. Resource, privacy, and lifecycle constraints

All model inference is local-only and goes through `ModelGateway`; no cloud,
telemetry, browser, Moodle, or external write fallback is allowed. Phase 4
runtime resources are closed deterministically by their existing owner. Agent
task state and any approved local write use injected disposable paths in tests.
Normal `data/database/aaos.db`, `data/rag/`, and `data/documents/` are never
created by imports or unit tests.

The RTX 4050 6 GB ceiling remains a hard constraint. Phase 5 does not add a
second resident model or alter Phase 4's embedding-release-before-generation
contract; latency and memory are measured during acceptance rather than hidden
with unbounded retries.

## 16. Future-phase boundaries and open decisions

Phase 6 owns approval workflows and consequential external writes. Phase 7 owns
dashboard/UI behavior. Phase 8 owns restricted code execution and VLM paths.
Phase 5 does not pre-register those capabilities.

Human review must still approve the exact tool schemas, whether
`write_task_state` is enabled, persistence implementation, timeout values,
evaluation case counts, target thresholds, audit retention duration, and any
CLI/API exposure. None of these open decisions authorize implementation in
this specification task.

## 17. Design acceptance gate

Before Slice 1 implementation, human review must approve this document and the
companion implementation plan. Approval must confirm the one-orchestrator
boundary, local-write policy, closed tool inventory, limits, state transitions,
injection isolation, held-out artifact retention, and Phase 4 preservation.
