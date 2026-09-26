# Phase 5 — Controlled Local Agent Loop Design Specification

Status: **IMPLEMENTED IN PART / CORRECTIVE REVIEW REQUIRED**. Slices 1–5
are implemented and human approved. Slice 6 is committed but is not yet
human approved. The roadmap amendment requires a separately authorized
corrective gate before Slice 7 or Slice 8 acceptance work proceeds.

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

The application supplies an immutable trusted task scope/grant. Every proposed
RAG scope must be a subset of that grant, checked before invocation; a valid
`GlobalScope` or `HistoricalScope` is not authorization. Cross-course/global or
historical expansion requires trusted application authorization, never model
text. Missing or incompatible grants fail closed at the Phase 5 consumer.
Phase 4 is not changed to implement this check.

Historical Phase 4 design sections 7 (Phase 5 ownership) and 38 assigned
conversation history, follow-up rewriting and approvals to Phase 5. Those
future-facing assignments are superseded for Phase 5 by this specification:
history/follow-up rewriting remain excluded; approvals belong to Phase 6.
The historical document and frozen implementation remain unchanged.

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

The human-approved baseline is:

| Limit | Approved value |
| --- | ---: |
| `MAX_AGENT_STEPS` | 8 |
| `MAX_STRUCTURED_PROPOSAL_REPAIRS` | 1 |
| `MAX_IDENTICAL_TOOL_CALL_REPETITIONS` | 2 |
| Model proposal timeout | 60 seconds |
| `rag_query` timeout | 120 seconds |
| `read_task_state`, `write_task_state`, `complete_task` timeout | 5 seconds each; metadata does not enable a handler |

Only the initial proposal and one schema-repair proposal may be requested per
step. Timeouts and transient failures follow an explicit bounded policy; there
is no hidden fallback or infinite retry. Exhausting any limit produces a safe
typed terminal failure (`STEP_LIMIT_EXCEEDED`, `LOOP_DETECTED`,
`MODEL_TIMEOUT`, or `TOOL_TIMEOUT`).

`LOOP_DETECTION_KEY` is the canonical serialization of the registered tool name
plus validated, normalized arguments. Raw model text, whitespace, or proposal
ids do not defeat repetition detection. The count is request-local and reset
only when a new `AgentTask` is created.

One application-owned `EFFECTIVE_RUNTIME_POLICY` is assembled and validated at
composition. It reconciles step, proposal-repair, identical-call, tool-call,
observation-size, model-timeout and per-tool-timeout limits. Conflicting
overlapping component settings are rejected, not silently selected. All
applicable limits are finite and versioned; acceptance reports the effective
configuration and its fingerprint (configuration only, no private payload).
The model cannot change policy. Additional numeric limits require explicit
review rather than guessed defaults.

`REQUEST_DEADLINE` and `CANCELLATION_REQUESTED` do not imply
`OPERATION_TERMINATED` or `RESOURCE_CLEANUP_COMPLETE`. A timed-out synchronous
worker may still be running. Its trusted owner must track termination and
cleanup and prevent conflicting resource reuse until both are verified.
Record `EXECUTION_OUTCOME_KNOWN` versus `EXECUTION_OUTCOME_UNKNOWN` independently
of caller timeout/task status. Do not retry an operation merely because the
wait timed out. Phase 6 additionally requires durable operation identity,
idempotency and unknown-outcome reconciliation before writes.

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

The registry stays small and static, not a generic schema framework. Each
executable entry requires a real trusted input validator, output validator,
tool-specific observation projection, permission, finite timeout and
tool/schema/audit version. A schema-name string alone is not output validation.
Bounded model-facing descriptions are derived from trusted definitions where
practical; model text cannot install or modify them.

### Initial conceptual tool inventory (not execution authorization)

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

Slice 7 is not authorized. A concrete useful domain operation and separate
human approval are prerequisites. Without them it remains disabled and
`NOT_IMPLEMENTED_BY_SCOPE`.

## 11. Observation sanitization and injection isolation

`ToolResult` is trusted application output. `ToolObservation` is a deliberately
limited model-visible projection:

- bounded total and per-field text (implementation constants are versioned);
- structured fields restricted to an allowlist and primitive values;
- safe error code and retryability only;
- operational secrets, cookies, tokens, authorization state, stack traces and
  exception internals excluded at source; errors use fixed safe templates;
- filesystem paths replaced by opaque resource identifiers or omitted;
- document/evidence text labelled as data and bounded without becoming a
  prompt instruction;
- deterministic bounded text and truncation markers, never hidden truncation
  of a control field; identity uses opaque application-owned references.

Tool-specific allowlisted projections are the primary boundary, not broad
semantic text rewriting. In particular, legitimate RAG prose containing SQL
keywords must not be corrupted. Hashes of unsanitized private payloads must
not be exposed as supposedly anonymous observations or audit identifiers.
Private evaluation fingerprints have a separate restricted retention purpose;
they are not permission to expose payload hashes to models or public logs.

Slice 6 at `e0f468d` is IMPLEMENTED, NOT_YET_HUMAN_APPROVED and
CORRECTIVE_REVIEW_REQUIRED. Existing tests/commit do not establish acceptance
of its sanitizer. C4 must correct and independently review this boundary.

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
message. Audit records may retain timestamps, opaque task/step ids, tool name,
permission decision, state transitions and resource durations. They must not
expose unsanitized private argument/result fingerprints or retain credentials, raw prompts, full
document text, raw model output, arbitrary paths, or stack traces.

Before the Phase 7 UI, define a minimal versioned backend event envelope:
`event_schema_version`, `task_id`, optional `step_id` and `operation_id`,
`event_kind`, safe outcome/status, tool/schema/policy versions, duration and
timestamp. IDs are application-owned opaque references. No raw prompts,
document bodies, credentials, cookies, tokens, authorization headers or
secret-bearing exceptions are permitted. Phase 7 consumes these backend
events; durable storage is not implemented by this amendment.

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

The approved split is 72 calibration cases (36 English, 36 Arabic) and 24 final
held-out cases (12 English, 12 Arabic). Both include normal tool tasks,
invalid/unknown tools, malformed arguments, permission attacks, injection,
loops, forbidden host-file/code execution and safe failures. Slice 8 freezes
membership before tuning, not in this documentation task.

Slice 8 defines a minimal reusable envelope: manifest and split identities,
dataset/case fingerprints, ordered case IDs, trace schema/version, metric
version, retention policy and exact private post-hoc inputs. Domain metrics
remain separate. Denominators and necessary retained inputs must be frozen
before tuning/held-out execution; missing inputs cannot justify a rerun.
Passing 24 cases is not evidence of 99% population reliability.

## 14. Human-approved acceptance targets

These targets were explicitly approved before Slice 1. Tool-selection accuracy
counts only manifest-defined opportunities with a unique correct action/tool;
refusal, ambiguity and non-tool termination are not incorrect tool choices
unless the manifest defines an expected tool.

| Target | Approved threshold | Rationale |
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

The reviewed tool schemas and numeric limits remain governed by their slice
approvals. Local-write enablement, persistence, audit retention duration and
any new CLI/API exposure still require separate authorization. This amendment
does not authorize their implementation. Future-phase internal gates are
defined in [the roadmap](../../PHASES.md).

## 17. Design acceptance gate

The original design/plan approval and Slice 1–5 approvals remain historical
evidence. Current status is owned by [the governance ledger](../../GOVERNANCE_LEDGER.md).
The C1–C7 corrective gate in the companion plan must be separately authorized,
implemented, verified and human approved before Slice 7 or Slice 8 acceptance
work. This documentation amendment stops for human review, not implementation.

## 18. Trusted final-output contract (roadmap amendment)

The trusted orchestrator owns a discriminated final outcome:

- `GROUNDED_RAG_RESPONSE`: retain the exact trusted Phase 4 rendered response,
  public status, citations/provenance and request/result lineage. A model may
  reference an existing task-owned result but cannot construct or relabel it.
- `CONTROL_OR_CONVERSATIONAL_TERMINAL`: bounded model text, explicitly not
  presented as citation-grounded evidence. It cannot replace a retained RAG
  answer or invent citations.
- `FUTURE_APPROVAL_ACTION_REFERENCE`: reserved for Phase 6 application-owned
  action records; rejected/disabled in Phase 5, not an executable tool now.

`terminal_text` and conceptual `complete_task` are proposals into the same
trusted completion validator, requiring TERMINAL capability and a legal state
transition. Neither directly sets authoritative state. `complete_task` has no
additional side effects and does not introduce a second completion authority.
Only the orchestrator commits completion. No second answer generator or
model-rendered citation trust is introduced. Exact contract changes and
regressions belong to separately authorized C2, not this amendment.

## 19. C8 least-authority proposal contract (approved amendment)

The model-facing proposal is versioned as `RETRIEVE_OR_TERMINAL`. The model
chooses only `retrieve` or bounded non-grounded `terminal_text`; it cannot
select a tool name, scope, permission, runtime policy, tool contract,
arguments, answer language, grounded status, citations or provenance. Trusted
AAOS composition supplies `rag_query`, the exact `AgentTask.initial_scope`,
the bounded original request text and the existing trusted/default language
derivation. The existing registry, permission policy, C5 runtime policy, C6
validators/projectors, C7 events and C2 lineage checks remain authoritative.

After a successful `rag_query`, the trusted Phase 4 response is returned
directly. No second model proposal may rewrite or mint grounded output, and
at most one useful RAG execution is permitted per request. Legacy provider
envelopes containing authority-bearing fields fail closed; the provider
schema is strict and infrastructure-owned.

The historical `phase5-agent-benchmark-v1` contract and artifacts are
immutable. A uniform contract-only migration defines
`phase5-agent-benchmark-v2`, whose model-facing metrics cover the
retrieve-versus-terminal decision, terminal correctness, format/repair,
language and end-to-end outcome. Trusted execution oracles remain private and
are not scored as model-selected tool/scope/permission fields. V2 calibration
and held-out manifests retain the approved 72 (36 English/36 Arabic) and 24
(12 English/12 Arabic) memberships and receive distinct fingerprints before
any model calibration. Held-out consumption remains one-time and is not
authorized by this amendment.

## 20. C9 trusted decision ownership and V3 evaluation contract

The V1 and V2 contracts, manifests, fingerprints and historical results remain
immutable. `phase5-agent-benchmark-v3` is a new contract-only projection of V2.
Each case has exactly one architecture-derived owner from the closed set
`MODEL_SEMANTIC_ROUTING`, `TRUSTED_PRE_MODEL_CONTROL`,
`TRUSTED_POST_MODEL_VALIDATION`, `TRUSTED_RUNTIME_OR_LIFECYCLE`,
`TRUSTED_POST_TOOL_RESULT` or `EVALUATION_SYNTHETIC_FAULT_INJECTION`.
Ownership is determined from category, architecture and case intent, never from
model output or observed performance.

V3 reports separate model-routing, trusted-system, security-hard-gate,
runtime-environment and proposal-format-efficiency domains. Model-routing
metrics score only retrieve-versus-terminal behavior and explicitly applicable
terminal-kind oracles. Trusted-system cases score the deterministic condition
owned by AAOS; security hard gates remain independent zero-tolerance gates and
cannot be averaged away by routing accuracy. No blended agent-accuracy metric
is a primary acceptance result. The historical V2 result remains reportable,
unchanged, as `V2_ARCHITECTURALLY_MIXED_HISTORICAL_METRIC=42/72`; proposal
format/repair counts are secondary model-routing diagnostics, not semantic
routing accuracy.

V3 calibration (72 cases, 36 English/36 Arabic) and V3 held-out (24 cases,
12 English/12 Arabic) are derived uniformly from the frozen V2 manifests before
any V3 model run. The V2 held-out remains unconsumed and V3 held-out results
remain invisible until a separately authorized run. C9 changes evaluation
ownership/scoring only; it does not add a production pre-model gate or change
the C8 orchestrator.

## 21. C10 numeric routing policy and V4 contract

C10 supersedes no historical artifact. It defines a new
`phase5-agent-benchmark-v4` evaluation contract while keeping V1, V2 and V3
schemas, manifests, reports, fingerprints and held-out data immutable. C10 is
an engineering evaluation policy for this local/portfolio system, not
population-reliability certification or a statistical confidence guarantee.
The policy is explicitly independent of model results:
`C10_POLICY_SELECTED_FROM_CURRENT_MODEL_RESULTS=NO`.

The model-routing domain must have at least 64 cases in calibration and at
least 64 in held-out. Global routing accuracy must be `>=0.95`, evaluated by
exact numerator/denominator comparison (61/64 passes; 60/64 fails at N=64).
Each split must structurally cover at least 16 English/Retrieve, 16
English/Terminal, 16 Arabic/Retrieve and 16 Arabic/Terminal cases. Coverage is
a hard structural gate; subgroup accuracy is diagnostic, not an independent
hard gate. No asymmetric false-terminal gate is authorized. Trusted-system and
security gates remain independent zero-tolerance domains, while proposal
format/repair efficiency remains separate from routing quality.

The frozen authoring specification `c10-routing-matrix-v1` uses only the C8
semantic boundary: retrieve when a clear answerable information need requires
trusted local knowledge, otherwise use bounded terminal text. Calibration and
held-out instances are deterministic, balanced, independently instantiated,
ID/content-disjoint under the predeclared normalized-equality rule, and never
filtered by model outputs, known failures, prompts or language-specific
heuristics. Existing C9 trusted-system cases are migrated deterministically
without changing semantic intent or inflating the routing denominator.

V4 manifests and fingerprints are created before inference. V4 held-out is
created before any calibration run and remains unconsumed/invisible until a
separate authorization. For V4, the exact consumption boundary, atomic
AVAILABLE / RESERVED / CONSUMED transitions, pre-semantic recovery and
fail-closed stale-state handling are normative in the
[C10 V4 evaluation contract](2026-09-10-phase-5-c10-v4-evaluation-contract.md#one-time-held-out-consumption-and-recovery).
No model or RAG execution, candidate freeze, or Phase 5 acceptance is implied
by this contract.
