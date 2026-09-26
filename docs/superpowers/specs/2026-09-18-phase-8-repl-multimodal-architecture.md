# Phase 8 Restricted REPL and Multimodal Vision Architecture

Status: DESIGN ONLY — implementation is not authorized by this document.

## 1. Decision summary

Phase 8 adds two independently gated capabilities after the frozen Phase 7
boundary:

1. a read-only, ephemeral IPython-compatible computation worker with an
   operating-system isolation boundary; and
2. selective visual-document interpretation with explicit provenance and
   resource controls.

The recommended architecture is an adapter-based design. AAOS remains the
trusted composition root. It materializes bounded inputs, chooses limits and
model resources, invokes a least-authority adapter, validates the result, and
projects sanitized observations. The worker and any vision model receive data,
never capability authority. A container or equivalently proven isolated Linux
worker is a prerequisite for accepting the REPL; an in-process interpreter,
AST blacklist, ordinary subprocess, or Python namespace restriction is not a
security boundary.

Vision is not a prerequisite for the REPL and the REPL is not a prerequisite
for vision. A visual analysis may produce bounded data for retrieval or a
computation, but it cannot execute a tool, approve an action, change policy,
or create a grounded citation by itself.

## 2. Frozen baseline and scope

- Phase 7 is accepted and frozen at commit
  `baca928d6b9e45aa2e26916f50f8f0a8dfc74db5`, tag `phase-07-passed`.
- Phase 7 worktree remains untouched.
- Phase 8 worktree is `C:\Users\aljaf\.codex\worktrees\aaos-phase-08` on
  branch `phase-08-repl-multimodal`.
- Phase 6 approval, capability, claim, execution, reconciliation, and
  `UNKNOWN_OUTCOME` semantics remain authoritative.
- RLM, Recursive Language Models, Deep Ask AAOS, multi-agent recursion, and
  arbitrary autonomous code generation are explicitly out of scope.
- Calendar, GitHub, MCP writes, browser mutations, credentials, network
  access, model downloads, and real external writes are out of scope.
- This document and its implementation plan are the only permitted Phase 8
  changes in the current authorization window.

The repository roadmap calls the phase “Optional Vision and Independently
Isolated Computation” and describes independent 8A (vision) and 8B
(computation) tracks. This specification uses the user-facing names
“Restricted IPython REPL” and “Multimodal Vision” while preserving that
independence and optionality.

## 3. Existing boundaries that Phase 8 must preserve

### 3.1 Phase 5 agent boundary

`AgentOrchestrator` receives an untrusted `ProposalSource`, validates a closed
`RetrieveProposal` or `TerminalProposal`, and uses the immutable
`ToolRegistry`, `PermissionPolicy`, `TrustedTaskScope`, runtime policy,
input/output validators, projectors, observations, and event sink. Model text,
retrieved text, and observations are data rather than authority. Disabled tools
remain registered and non-executable.

Phase 8 must not add a generic execution escape hatch to
`ProviderProposalEnvelope`. The current C8 proposal contract remains limited
to `RETRIEVE` and `TERMINAL_TEXT`; the initial Phase 8 scope does not add
model-selectable computation. A trusted Phase 8 composition service may invoke
the computation adapter only after its own capability decision. A future
model-directed computation contract would require a separately versioned
proposal schema, authority review, and security acceptance. Even in that
future contract, the model could not choose a path, network mode, credentials,
resource limits, host process, image source, or approval identity.

### 3.2 Phase 4 document and RAG boundary

PDF ingestion currently extracts embedded text and page geometry through
`PdfProcessor`, persists `DocumentPageORM`, chunks exact page spans, and
generates citations from `RagChunkSourceSpanORM`. The frozen RAG result contract
requires trusted source spans. Visual text or descriptions must not be stored
as if they were embedded-PDF character spans. A visual-derived record therefore
needs a distinct provenance kind and cannot satisfy a text citation validator
without an explicit, reviewed projection.

### 3.3 Phase 6 approval boundary

The durable Phase 6A/6B ledger owns preparation, approval, capability
revalidation, one-shot claims, dispatch, reconciliation, and external outcome
truth. Phase 8 has no permission to create an approval, consume a claim, call a
real external adapter, or convert model/VLM output into an `ExecutableAction`.

## 4. Goals and non-goals

### Goals

- Execute bounded, read-only computation in a fresh isolated worker.
- Enforce network, credential, filesystem, process, CPU, memory, output, and
  wall-clock limits outside the Python language runtime.
- Make worker creation, timeout, cancellation, crash, cleanup, and result
  validation deterministic and auditable.
- Detect image-heavy PDF pages deterministically and analyze only selected
  pages under explicit budgets.
- Preserve document/page/model/revision/hash provenance for visual-derived
  information.
- Reuse the existing model gateway and lifecycle concepts through typed
  adapters, without changing the frozen text-only contracts until a separate
  review authorizes a multimodal request contract.
- Keep computation and visual output untrusted and safe for the existing
  observation, RAG, and UI boundaries.

### Non-goals

- No general-purpose shell, filesystem browser, network client, package
  installer, credential reader, or host Python access.
- No arbitrary code authority for the LLM, VLM, user, or retrieved content.
- No recursive model delegation, RLM, Deep Ask, self-modifying prompts, or
  multi-agent orchestration.
- No automatic retries after an ambiguous worker/VLM outcome.
- No user-supplied image-query upload route in this phase.
- No acceptance run, benchmark run, model download, or migration creation in
  the design task.

## 5. Architectural alternatives

### Option A — OS-enforced isolated worker plus typed AAOS adapter (recommended)

AAOS starts one short-lived worker inside a container or an equivalently
proven isolated Linux boundary. The worker receives a versioned JSON request
over a local pipe/stdin, has no network, has no credentials, has only a
task-specific input/output directory, and is killed and destroyed after one
request. A Windows Job Object may supervise the host-side process tree, but is
an adjunct rather than the isolation boundary.

Advantages: the security claim is enforceable outside Python, cleanup is
explicit, limits map to cgroups/container flags, and the adapter can be
tested with a fake worker. Cost: a runtime prerequisite and a small protocol
surface.

### Option B — host subprocess with Windows Job Object and restricted imports

AAOS starts a subprocess, applies a Job Object, passes a restricted Python
namespace, and denies selected imports.

Advantages: minimal deployment friction on Windows and useful for development
diagnostics. Disadvantages: a Job Object does not provide filesystem or network
isolation, import/AST restrictions are bypassable, and credentials may remain
reachable. This option is not acceptable for a Phase 8 security acceptance
gate; it may only be a clearly non-production development harness.

### Option C — in-process IPython with AST/blacklist policy

AAOS executes cells in the application process after syntactic checks.

Advantages: smallest code footprint. Disadvantages: no credible process,
filesystem, network, memory, or credential boundary and a crash can corrupt
the service. Rejected.

Option A is selected because it is the only option that satisfies the roadmap
requirement that Python namespace restriction alone is not containment.

## 6. Trusted computation architecture

### 6.1 Closed contracts

The implementation plan will introduce typed immutable contracts under a new
Phase 8 package. The exact public shapes are:

```python
@dataclass(frozen=True)
class ComputationLimits:
    wall_time_seconds: int
    cpu_time_seconds: int
    memory_bytes: int
    output_bytes: int
    max_processes: int
    max_input_bytes: int


@dataclass(frozen=True)
class ComputationInput:
    input_id: str
    media_type: Literal["text/plain", "application/json", "image/png"]
    content_sha256: str
    bounded_bytes: bytes


@dataclass(frozen=True)
class ComputationRequest:
    request_id: str
    code: str
    inputs: tuple[ComputationInput, ...]
    limits: ComputationLimits
    contract_version: str


@dataclass(frozen=True)
class ComputationResult:
    request_id: str
    status: Literal["SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED", "UNKNOWN_OUTCOME"]
    stdout: str
    stderr: str
    result_json: str | None
    output_sha256: str | None
    duration_ms: int
    worker_id: str
    contract_version: str
```

The actual implementation may use Pydantic or existing immutable dataclass
conventions, but the field allowlists, closed status values, byte limits, and
version binding are mandatory. `code` is bounded text; it never carries a
filesystem path, credential, network mode, or executable command supplied by
the model. Limits and materialized inputs are selected by trusted code.

`ComputationResult.UNKNOWN_OUTCOME` means AAOS cannot prove whether the worker
completed its computation. It is not a successful result and cannot trigger a
retry automatically. A worker that proves it rejected code before executing
has `FAILED`; a process killed after execution could have begun is
`UNKNOWN_OUTCOME`.

### 6.2 Worker boundary

`SandboxExecutor.execute(request: ComputationRequest) -> ComputationResult`
is the only trusted entry point. Its implementation must:

1. validate the closed request and trusted limits;
2. create a fresh worker with a unique task directory;
3. mount only the task input directory read-only and an empty output directory
   with the bounded output quota;
4. disable network interfaces and DNS;
5. provide no credentials, environment secrets, host sockets, devices, or
   package cache;
6. run as a non-root identity with dropped capabilities and a read-only root;
7. apply CPU, memory, pids, output, and wall-time limits;
8. exchange one versioned JSON request/result; and
9. terminate and remove the worker and task directory in a `finally` path.

The adapter must reject a request if the required isolation runtime is not
available. It must never silently fall back to in-process execution or an
ordinary host subprocess. Docker/Linux-container availability, or an
equivalent reviewed boundary, is therefore a pre-implementation readiness
gate. The current discovery environment has Docker installed but no running
Docker daemon; WSL2 is present but its mount/network isolation is not yet
proven.

The host-side supervisor may use a Windows Job Object to reap descendants and
enforce a second CPU/memory ceiling. Job Objects do not satisfy the network or
filesystem requirements by themselves.

### 6.3 Capability and model authority

Trusted composition chooses whether to invoke computation and, when it does,
the `EffectiveRuntimePolicy` supplies:

- whether computation is allowed for the task;
- the code/input byte limits and quotas;
- which sanitized document/page or prior result IDs may be materialized;
- the worker image/runtime fingerprint;
- the timeout and cancellation budget; and
- whether visual analysis is permitted.

There is no model-owned computation proposal in the initial Phase 8 scope.
Any future proposal contains no path, mount, socket, credential, model
identifier, approval identity, or policy override. Computation is read-only
and cannot reach Phase 6 adapters. A visual result remains data and cannot
become `ExecutableAction` or a grounded citation without the existing trusted
validators.

## 7. Visual-document architecture

### 7.1 Current gap and selective signal

`PdfProcessor` records embedded text and page geometry but does not render
pages, detect diagrams, or analyze images. The Phase 8 visual selector will
operate after ordinary extraction and before optional visual analysis. It uses
deterministic signals such as embedded-text density, page area, image/object
count when exposed by the PDF renderer, and a fixed page budget. A normal text
page must not invoke a VLM merely because a document contains an image
elsewhere.

The selector returns a closed `VisualPageCandidate` with document version ID,
page number, reason code, estimated bytes, and selector version. It never
returns a path or provider payload.

### 7.2 Rendering and analysis contracts

Rendering is bounded and deterministic:

```python
@dataclass(frozen=True)
class RenderedPage:
    document_version_id: int
    page_number: int
    media_type: Literal["image/png", "image/jpeg"]
    width_px: int
    height_px: int
    content_sha256: str
    content_bytes: bytes


@dataclass(frozen=True)
class VisualAnalysis:
    document_version_id: int
    page_number: int
    source_content_sha256: str
    model_id: str
    model_revision: str
    prompt_revision: str
    status: Literal["SUCCEEDED", "FAILED", "TIMED_OUT", "UNAVAILABLE"]
    elements: tuple[VisualElement, ...]
    analysis_sha256: str | None


@dataclass(frozen=True)
class VisualElement:
    kind: Literal["TABLE", "EQUATION", "CHART", "DIAGRAM", "TEXT_REGION", "OTHER"]
    bounded_text: str | None
    structured_json: str | None
    confidence: float | None
```

Every visual analysis is tied to an exact document version/page and source
render hash, model/revision, prompt revision, and analysis hash. A failure is
persisted as a bounded status, not as an empty successful analysis.

Tables, equations, charts, and diagrams are represented as typed element
records. A table may carry bounded row/column JSON; an equation may carry
bounded plain text or LaTeX; a chart/diagram may carry a description plus
detected labels. None of these fields imply factual truth beyond the cited
page. Text generated by a VLM is never inserted into `RagChunkORM.exact_text`
as if it came from embedded PDF text.

### 7.3 Model and resource policy

The adapter must use an explicit model identity, revision, and capability
manifest. Model selection is a trusted runtime decision, not a model-proposed
field. Existing local inventory provides approximate candidates only:

| Candidate | Approximate Q4 artifact | Reported vision | Target-device decision |
|---|---:|---|---|
| `qwen/qwen3.5-4b` | 3.38 GB | yes | first probe; sequential residency |
| `mistralai/ministral-3-8b` | 6.06 GB | yes | not default; little 6 GB VRAM headroom |
| `qwen3.5-9b` | 6.55 GB | yes | exceeds target VRAM before runtime overhead |
| `granite-4.1-8b` | 5.35 GB | no | not a vision candidate |

Weights are not VRAM usage. Runtime buffers, image tensors, KV cache,
embedding/NLI residency, and offload determine actual memory. On the RTX 4050
6 GB target, vision, BGE-M3, and NLI must not be resident concurrently. The
required sequence is: release answer/vision model before BGE indexing, keep
NLI on CPU, load the selected VLM only for selected pages, then release it.
No model is downloaded by Phase 8 discovery or by the design acceptance.

The existing `ModelGateway` is text-only. A separate `VisionModelGateway`
must carry a typed image message and a bounded response; it must not widen
`ChatRequest` implicitly. LM Studio support must be proven for the selected
model before an adapter is accepted.

### 7.4 Provenance and RAG integration

The current `RagChunkSourceSpan` schema cannot represent visual-derived text
without corrupting citation semantics. The implementation therefore requires
an additive, separately reviewed persistence design, for example:

- `visual_analysis_run`: document version, page, source render hash, model
  identity/revision, prompt revision, selector version, status, analysis hash;
- `visual_element`: run ID, closed element kind, bounded structured/text
  payload hash, confidence, and sequence; and
- `visual_retrieval_span`: a trusted link from a visual element to a page
  provenance record, distinct from embedded-text character offsets.

The exact table names and Alembic revision must be approved before migration.
Visual evidence may be returned as a distinct citation provenance kind only
after the citation validator, projector, and UI contract are extended. Until
then, visual analysis may be stored and audited but must not appear as a
grounded text citation.

## 8. REPL and vision composition

Composition is one-way and bounded:

1. trusted code selects a page or visual-analysis record;
2. trusted code materializes a bounded `ComputationInput` containing the
   record's safe projection, never a host path;
3. the isolated worker computes over that input;
4. the result is validated and sanitized; and
5. the result can be shown as an ungrounded computation observation or passed
   to a separately authorized retrieval projection.

The worker cannot call the VLM, network, SQL, filesystem outside its task
mount, or Phase 6 adapters. A VLM cannot request computation. Any future
composition must preserve this direction and add no implicit authority edge.

## 9. Prompt-injection and untrusted-data handling

PDF text, rendered pixels, OCR/VLM output, computation stdout/stderr, and
retrieved documents are adversarial untrusted data. Prompt text must state
that visual/text content can contain instructions and must not alter tool
selection, limits, policy, approval, or citation ownership. Output schemas
must reject unknown fields and cap lengths. Sanitizers must redact paths,
secrets, stack traces, SQL, and arbitrary exception details using the existing
closed observation policy.

No content-derived string may be interpolated into a shell command, container
flag, environment variable, SQL statement, filesystem path, model identity, or
approval context.

## 10. API, persistence, and observability boundary

- The initial computation capability is internal and read-only; no browser
  route exposes arbitrary code execution.
- No external provider endpoint is added.
- Ephemeral worker state is not persisted as a second execution ledger.
- Existing safe `AgentEvent` and observation boundaries are reused. New event
  kinds, if needed, are closed enums with typed bounded payloads; no arbitrary
  dictionaries are accepted.
- Visual provenance requires an additive migration and API review. It must not
  mutate Phase 4 schema in place or make a visual record look like embedded
  text.
- Phase 6 `ActionRecord`, `ApprovalRecord`, `OperationRecord`, and
  `ExecutableAction` remain the only external-write authority. Phase 8 emits
  no action proposal for those records.

## 11. Failure and recovery semantics

### Computation

- Invalid request or policy denial: no worker starts, bounded `FAILED` result.
- Worker rejects code before execution: `FAILED` with a fixed reason code.
- Deadline/cancellation before execution: `CANCELLED` with no result.
- Timeout/crash after execution may have begun: `UNKNOWN_OUTCOME`; the worker
  is destroyed and no automatic retry occurs.
- Proven output-size or quota violation before execution: `FAILED`; after
  execution began: `UNKNOWN_OUTCOME`.
- A new attempt requires a new trusted request identity and explicit policy;
  the Phase 8 adapter never retries an ambiguous attempt automatically.

### Vision

- Selector/render failure: bounded page-level failure; text ingestion remains
  usable.
- VLM unavailable/timeout: page analysis status is `UNAVAILABLE` or
  `TIMED_OUT`; no fabricated visual result is created.
- Partial visual analysis retains successful element records and an explicit
  partial status with the exact source hash.
- Model identity/revision mismatch invalidates reuse and requires a fresh
  trusted analysis, never silent reinterpretation.

## 12. Acceptance invariants

The implementation and acceptance suites must prove all of the following:

1. A computation request cannot reach host network, credentials, host
   filesystem, host SQL, shell, or process creation beyond the isolated worker.
2. The acceptance runtime refuses to run when the reviewed OS isolation
   boundary is unavailable; it never falls back to an in-process interpreter.
3. Every worker is fresh, bounded, reaped, and destroyed after success,
   failure, timeout, cancellation, or crash.
4. Resource quotas and output caps are enforced, including a process-count
   limit and a deterministic timeout.
5. A late/crashed computation is `UNKNOWN_OUTCOME` and causes zero automatic
   retries.
6. Model proposals cannot select code, paths, mounts, credentials, limits,
   model identity, network mode, or approval context.
7. A disabled computation tool is registered but cannot execute.
8. Ordinary text-only pages do not invoke a VLM; selection is deterministic
   and page-budgeted.
9. Rendered page bytes and analysis output have bounded dimensions, size, and
   hashes.
10. Visual records carry document version, page, source hash, model/revision,
    prompt/selector revisions, and status.
11. Visual-derived text cannot masquerade as embedded-PDF text spans or create
    grounded citations without a reviewed provenance projection.
12. VLM output cannot grant tool, approval, policy, SQL, filesystem, network,
    credential, or external-write authority.
13. Qwen/BGE/NLI/VLM residency is sequential on the 6 GB target; no required
    acceptance path relies on simultaneous GPU residency.
14. Phase 4/5/6/7 tests remain green and no frozen worktree is modified.
15. Phase 8 acceptance executes zero external writes, zero model downloads,
    and zero heldout/production database mutations.

## 13. Evaluation and acceptance design

Phase 8 evaluation has no model-routing accuracy metric. It is deterministic
trusted-core evaluation plus explicitly labeled local-model capability probes.
The evaluation harness uses synthetic text, synthetic rendered pages, fake
vision gateways, fake sandbox workers, and isolated temporary storage.

Required test domains:

- contract closure, strict validation, and authority-negative tests;
- worker isolation probes for network, credentials, mounts, processes, and
  output/resource quotas;
- crash/timeout/cancellation and `UNKNOWN_OUTCOME` tests;
- cleanup and no-retry tests;
- deterministic visual selection and ordinary-page non-invocation;
- render hash, page provenance, model/revision binding, partial failure, and
  malformed VLM output tests;
- visual prompt-injection tests;
- REPL-to-vision bounded composition tests;
- Phase 4/5/6/7 regression suites and import-inert startup checks.

The acceptance split is frozen before any model probe: calibration fixtures
cover worker/security and visual semantics; a separate heldout fixture tests
generalization of deterministic selection/provenance. No current model
failure, case ID, or observed output may generate a fixture. Real heldout
execution requires a separate human authorization after implementation and
must not be performed by this design task.

## 14. Proposed implementation slices

1. 8A0 runtime qualification: prove the chosen OS isolation boundary and
   reject the task if Docker/container or an equivalent reviewed runtime is
   unavailable.
2. 8A1 closed computation contracts, capability policy, and fake worker.
3. 8A2 isolated worker supervisor, quotas, cleanup, and crash semantics.
4. 8A3 trusted Phase 5 tool integration and sanitized observations/events.
5. 8B1 visual page selection/render contracts and deterministic fake renderer.
6. 8B2 typed multimodal gateway, model lifecycle, and fake VLM.
7. 8B3 additive visual provenance persistence and citation boundary (separate
   migration approval gate).
8. 8B4 multimodal retrieval and bounded REPL composition.
9. 8B5 evaluation fixtures, independent review, and acceptance gates.

Each slice has its own RED → GREEN → reviewable commit cycle. No slice may
open a browser route, external write, Phase 6 approval path, or RLM behavior.

## 15. Open gates before implementation authorization

The following are explicit gates rather than implicit assumptions:

- a reviewed container/WSL isolation runtime is available and its network,
  mount, credential, process, memory, and CPU behavior is tested;
- the exact multimodal provider request/response contract is selected without
  changing the frozen text gateway silently;
- a visual provenance migration and citation projection are separately
  reviewed before any Alembic revision is created;
- acceptance fixtures and the calibration/heldout split are frozen before
  model probing;
- the human authorizes implementation after reviewing this spec and the
  companion plan.

## 16. Implementation stop gate

This specification authorizes no production implementation. The next
authorized action is human review of this document and the detailed plan.
Phase 8 remains `NOT_STARTED`; RLM remains explicitly excluded.
