# Phase 0E Local Model Gateway Design

## Status and purpose

This specification defines Phase 0E only: a minimal, model-independent inference seam between AAOS and the externally managed LM Studio process. It is approved for design and is not an implementation plan.

Phase 0E establishes plain local chat/generation, connectivity diagnostics, model-availability checks, and explicit inference failures. It supplies the local-model foundation required by the Phase 0 acceptance gate without introducing agent behavior.

## Scope

Phase 0E will:

- extend immutable application settings with non-secret local-inference configuration;
- define model-independent chat request, response, and availability value types;
- define an async `ModelGateway` protocol used by future AAOS callers;
- implement one LM Studio adapter behind that protocol;
- verify that LM Studio is reachable and that `qwen/qwen3.5-4b` is available;
- support plain chat/generation with reasoning disabled by default;
- translate transport, protocol, model-availability, and backend failures into explicit AAOS inference errors;
- provide deterministic automated tests without requiring a live LM Studio process;
- provide an explicit manual smoke test against the locally managed service.

## Non-goals

Phase 0E does not include:

- tool definitions, tool schemas, native `tool_calls`, or tool-result messages;
- `ActionProposal`, an orchestrator, an agent loop, policy evaluation, or approval gates;
- Moodle, Playwright, document ingestion, RAG, Chroma, Calendar, GitHub, MCP, dashboard, REPL, VLM, or Phase 1 domain models;
- automatic LM Studio startup, shutdown, model download, model load, model unload, or GPU resource management;
- streaming responses, batching, retries, conversation persistence, or public inference HTTP routes;
- copying experimental model-screening scripts into the production package.

Native tool calling and orchestration are explicitly deferred to Phase 5. The successful screening results are evidence for later planning, not authorization to expose tool behavior in Phase 0E.

## Selected local runtime profile

The Phase 0E default profile is:

| Setting | Value |
|---|---|
| Runtime | LM Studio |
| Model identifier | `qwen/qwen3.5-4b` |
| Quantization | `Q4_K_M` |
| Context length | `8192` tokens |
| Reasoning | Disabled by default |
| GPU policy | 100% offload on RTX 4050 6GB |

Observed screening at an 8192-token context showed 4781 MiB used out of 6141 MiB total GPU memory, leaving an observed difference of approximately 1360 MiB (about 1.3 GiB). The same run produced approximately 50.54 tokens/second and had approximately 0.418 seconds time to first token. `nvidia-smi` reports total device usage, including other desktop processes, so these figures are operational observations rather than model-only allocations or runtime guarantees. Phase 0E must not add concurrent model loading or hidden GPU consumers.

## Architecture

```text
AAOS caller
  -> ModelGateway protocol
  -> LMStudioGateway adapter
  -> injected async HTTP transport
  -> externally managed LM Studio
  -> validated adapter response
  -> model-independent ChatResponse
```

The inference package is a deep module at the local-model seam. Callers learn the `ModelGateway` interface and AAOS value/error types; they do not learn LM Studio endpoint paths, payload shapes, or response fields.

The intended package structure is:

```text
src/aaos/
├── config.py
└── inference/
    ├── __init__.py
    ├── models.py
    ├── gateway.py
    └── lm_studio.py
```

### Component responsibilities

`config.py`

- Retains the existing immutable `AppSettings` pattern.
- Adds only the LM Studio base URL, model identifier, context length, and request timeout.
- Uses safe localhost defaults and contains no credentials.
- Performs no network or model-lifecycle work.

`inference/models.py`

- Owns immutable, model-independent value types for messages, generation requests, generation responses, and availability results.
- Validates caller-visible invariants before the adapter sends a request.
- Contains no LM Studio response types or transport objects.

`inference/gateway.py`

- Defines the async `ModelGateway` protocol.
- Defines the AAOS inference error hierarchy exposed to callers.
- Imports model-independent value types only.

`inference/lm_studio.py`

- Implements the `ModelGateway` protocol for LM Studio.
- Owns endpoint selection, request serialization, reasoning-disable behavior, response validation, and error translation.
- Receives settings and an async HTTP transport through construction and takes exclusive ownership of that transport.
- Requires the supplied transport to be dedicated to this gateway and not shared with other modules.
- Provides deterministic, idempotent async cleanup of its owned transport.
- Creates no global client at import time.
- Never starts LM Studio or changes model lifecycle state.

## Dependency direction

Dependencies point inward toward model-independent AAOS types:

```text
AAOS caller -> gateway.py -> models.py
LM Studio adapter -> gateway.py + models.py + config.py
HTTP transport -> used only by LM Studio adapter
```

`gateway.py` must not import the LM Studio adapter. `models.py` must not import transport or LM Studio types. Application code must receive a `ModelGateway`; it must not instantiate or call the LM Studio adapter implicitly.

This direction allows a later adapter to replace LM Studio without changing callers. Phase 0E implements only one production adapter; automated tests may supply a controlled transport at the adapter's internal seam rather than adding a speculative second gateway implementation.

Whichever concrete async HTTP client is selected during implementation is a normal AAOS runtime dependency. It must not exist only in a development or test dependency group. The implementation plan will select the package; this design does not select or add it.

## Model-independent interface

The protocol exposes two async operations:

```python
class ModelGateway(Protocol):
    async def check_availability(self) -> ModelAvailability: ...
    async def generate(self, request: ChatRequest) -> ChatResponse: ...
```

`check_availability()` is an explicit diagnostic operation only. It reports whether the service is reachable and whether the configured model identifier appears in the service's model inventory. A reachable service with the configured model absent is a valid availability result, not a successful generation state.

`generate()` accepts only model-independent chat data and returns visible assistant content plus minimal response metadata useful to AAOS. It sends the generation request directly: it must not call `check_availability()` or query model inventory before generation. It must not expose a raw LM Studio response object.

## Request and response boundary

### Chat message

A chat message contains:

- a role restricted to `system`, `user`, or `assistant`;
- non-empty textual content.

Tool-related roles are outside Phase 0E.

### Chat request

A chat request contains:

- one or more ordered chat messages;
- an optional positive output-token limit;
- a reasoning flag that defaults to `False`.

The selected model and context ceiling come from application settings rather than each caller. Phase 0E callers cannot enable reasoning accidentally: omitted configuration means reasoning is off. If a caller explicitly requests reasoning before a later phase authorizes it, the Phase 0E implementation rejects the request as unsupported rather than silently changing behavior.

### Chat response

A chat response contains:

- non-empty visible assistant text;
- the model identifier reported by the backend;
- token-usage counts only when the backend supplies valid counts.

Backend-specific identifiers, choices, reasoning traces, transport headers, and raw payloads remain private to the adapter. Empty, missing, or structurally invalid assistant content is a protocol failure.

### Availability result

An availability result contains:

- whether LM Studio was reachable;
- whether the configured model was available;
- the configured model identifier;
- the model identifiers returned by the service when reachable.

Availability results contain no credentials, request headers, prompts, or generated content.

## Configuration

The safe defaults are:

| Setting | Default | Constraint |
|---|---|---|
| LM Studio base URL | `http://127.0.0.1:1234` | Local HTTP URL; normalized without a trailing slash |
| Model identifier | `qwen/qwen3.5-4b` | Non-empty |
| Context length | `8192` | Positive; selected for the measured 6GB VRAM profile |
| Request timeout | `30` seconds | Positive and finite |

These values are non-secret. Phase 0E does not create `.env` or define API keys, authorization headers, or remote endpoints. Settings construction performs validation only and does not contact LM Studio.

The context setting is an upper operating profile, not permission to send unbounded requests. Without a tokenizer, Phase 0E deterministically validates only limits it can know:

- reject `max_output_tokens <= 0`;
- reject `max_output_tokens > context_length`.

The adapter must not estimate message or input token counts, silently truncate messages, or silently reduce `max_output_tokens`. Phase 0E introduces no tokenizer. If LM Studio reports that total input plus requested output exceeds the model context, the adapter translates that response into an explicit AAOS inference/backend failure.

## Connectivity and model availability

The availability flow is:

1. Send a bounded request to LM Studio's model-inventory endpoint.
2. Apply the configured timeout.
3. Validate the HTTP status and response structure.
4. Extract model identifiers.
5. Compare identifiers exactly with `qwen/qwen3.5-4b`.
6. Return a model-independent availability result.

The adapter performs no automatic remediation. An unreachable service remains stopped. An absent model remains unloaded. The result tells diagnostics what is true; it does not mutate LM Studio.

Generation does not preflight connectivity or model inventory. It sends the generation request directly. Connection and timeout failures are translated normally, and a backend response indicating that the configured model is unavailable is translated to `ModelUnavailableError`.

## Reasoning behavior

Routine AAOS generation runs with reasoning disabled. The adapter must send the explicit LM Studio-supported control for disabled reasoning and return only visible assistant content. It must not infer success merely because reasoning fields are absent.

If the installed LM Studio protocol cannot prove that reasoning is disabled for the configured model, generation fails explicitly with an inference protocol or unsupported-feature error. It must not silently enable reasoning, expose hidden reasoning text, or fold reasoning traces into visible content.

The exact LM Studio transport field belongs to the adapter implementation and must be verified against the installed LM Studio protocol during implementation. It is not part of the `ModelGateway` interface.

## Error model

All gateway failures derive from `InferenceError`. The caller-visible categories are:

- `InferenceConnectionError`: LM Studio cannot be reached or the connection fails.
- `InferenceTimeoutError`: the configured timeout expires.
- `ModelUnavailableError`: the configured model is not available for inference.
- `InferenceProtocolError`: the backend returns malformed, incomplete, or contract-incompatible data.
- `InferenceBackendError`: LM Studio returns a valid error response that does not fit a more specific category.
- `UnsupportedInferenceFeatureError`: a Phase 0E caller requests reasoning or another unsupported capability.

Errors contain safe diagnostic context such as operation, model identifier, and status code where present. They must not include prompt content, response bodies, authorization headers, cookies, tokens, or transport configuration containing secrets.

No error is converted into an empty successful response. Phase 0E adds no retry loop; callers receive the first explicit failure. Retry policy belongs to the later orchestrator phase.

## Import-time and lifecycle invariants

Importing `aaos.config`, `aaos.inference`, any inference submodule, or `aaos.main` must not:

- perform a network request;
- create an HTTP client;
- start LM Studio;
- download, load, or unload a model;
- allocate GPU model memory;
- create files or database state;
- read secrets.

Network-capable objects are created only through explicit construction. `LMStudioGateway` takes exclusive ownership of the transport supplied to its constructor; the caller must supply a dedicated transport and must not share it with another module. The gateway provides deterministic async cleanup, and repeated cleanup calls are safe and have no additional effect. Tests may inject a controlled transport under this same ownership contract. No global or import-time client is allowed. The FastAPI `/health` endpoint remains process-liveness only and does not call the gateway.

## Automated testing strategy

Normal tests run without LM Studio and without network access. They exercise public behavior through the model-independent interface and the adapter's injected transport seam.

Required automated coverage includes:

- settings defaults and validation;
- module and FastAPI imports produce no network calls or model lifecycle effects;
- availability reports reachable/available for an exact configured model match;
- availability reports reachable/unavailable when the model is absent;
- generation sends exactly one generation request and performs no availability or inventory preflight;
- plain generation serializes ordered messages and explicitly disables reasoning;
- generation returns model-independent visible text and valid optional usage;
- invalid or empty requests, non-positive output limits, and output limits above the configured context are rejected before transport execution;
- input token counts are not estimated, messages are not truncated, and output limits are not silently reduced;
- connection failure, timeout, missing model, non-success status, malformed JSON, malformed inventory, missing assistant text, and unsupported reasoning map to the correct AAOS errors;
- error strings do not contain prompts, response bodies, headers, tokens, or cookies;
- the gateway exclusively owns its injected transport, closes it deterministically, and treats repeated cleanup as a safe no-op;
- existing environment, database, migration, and `/health` tests remain unchanged and pass.

Tests must use complete representative LM Studio payloads at the transport seam. They assert AAOS results and errors rather than assertions about a mock's existence. No default test may depend on port 1234 or a loaded model.

## Manual LM Studio smoke test

The live smoke test is explicit and opt-in. Before running it, the user starts LM Studio, loads `qwen/qwen3.5-4b` with the approved 8192-token profile and full GPU offload, and confirms reasoning is disabled.

The smoke test then:

1. checks the configured base URL;
2. confirms the model inventory includes exactly `qwen/qwen3.5-4b`;
3. sends one short deterministic plain-chat request with reasoning disabled;
4. validates a non-empty visible response and the reported model identifier;
5. records elapsed time and safe usage metadata when available;
6. reports failures without starting, loading, unloading, or reconfiguring LM Studio.

The smoke test must not log the full prompt or response by default. It performs no tool call and no external write. Failure because LM Studio is stopped or the model is absent is explicit diagnostic evidence, not permission to manage the service automatically.

## Acceptance criteria

Phase 0E passes its sub-gate only when all of the following are demonstrated:

1. The repository contains the approved `inference` package and no Phase 1+ functionality.
2. Application settings contain only the approved non-secret inference fields and safe local defaults.
3. `ModelGateway` exposes only availability and plain generation through model-independent types.
4. The LM Studio adapter performs no import-time network or lifecycle work and exclusively owns its dedicated injected transport with idempotent async cleanup.
5. Automated tests pass without LM Studio running and cover success plus every defined error category.
6. Reasoning is disabled by default and unsupported enablement fails explicitly.
7. The live smoke test confirms LM Studio reachability, exact model availability, and schema-valid plain generation for `qwen/qwen3.5-4b`.
8. Existing Phase 0A-0D tests, linting, typing, lock verification, and Git security checks still pass.
9. No credentials, prompts, private academic data, raw backend bodies, or reasoning traces appear in normal logs or errors.
10. No tool schema, tool call, `ActionProposal`, orchestrator, policy engine, approval mechanism, or automatic model lifecycle behavior is introduced.
11. Generation performs no availability or inventory preflight, and context-limit validation follows the tokenizer-free rules in this design.
12. The selected concrete async HTTP client is recorded as a runtime dependency.

Passing Phase 0E does not pass Phase 0 as a whole. The remaining Phase 0 acceptance criteria and explicit proceed decision still govern subsequent work.

## Failure modes and operational limits

- LM Studio may be stopped, listening on a different address, or blocked locally; report a connection error.
- The service may be reachable while the selected model is not loaded; report model unavailable.
- Backend payloads may change across LM Studio versions; reject incompatible payloads as protocol errors.
- A requested output limit may be non-positive or exceed the configured context; reject those known violations before transport execution.
- Total input plus output may exceed the backend context even when the output limit alone is valid; translate LM Studio's rejection explicitly without token estimation or silent truncation.
- A 30-second request timeout may be too short during cold local-model startup; Phase 0E reports the timeout and does not add retry or lifecycle automation.
- The observed difference between 4781 MiB used and 6141 MiB total is approximately 1360 MiB (about 1.3 GiB). Because `nvidia-smi` includes other desktop processes, this is not a model-only allocation or runtime guarantee; concurrent model/VLM loading remains outside Phase 0E.

Phase 0E makes no concurrency or throughput guarantees. Concurrency control, batching, backpressure, and throughput service-level objectives remain outside this phase.
