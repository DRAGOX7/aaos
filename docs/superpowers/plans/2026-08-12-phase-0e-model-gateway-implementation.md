# Phase 0E Model Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a minimal, local-only, model-independent asynchronous gateway for diagnostic model availability and plain LM Studio chat generation, with explicit reasoning disablement, safe failures, offline tests, and deterministic transport cleanup.

**Architecture:** AAOS callers depend on immutable value types and the two-operation `ModelGateway` protocol. `LMStudioGateway` is the sole Phase 0E adapter: it wraps an exclusively owned `httpx2.AsyncBaseTransport` in a private `httpx2.AsyncClient`, uses LM Studio's native model inventory for loaded-state diagnostics, uses the OpenAI-compatible chat endpoint for generation, validates responses, and returns only model-independent values. LM Studio remains an externally managed process; normal tests replace the transport and never contact the network.

**Tech Stack:** Python 3.11, frozen slotted dataclasses, `typing.Protocol`, `httpx2>=2.10.0`, LM Studio native REST (`GET /api/v1/models`), LM Studio OpenAI-compatible REST (`POST /v1/chat/completions`), pytest with `asyncio.run`, Ruff, mypy, uv.

## Global Constraints

- This plan implements Phase 0E only. Phase 0 as a whole remains open after this sub-gate.
- Do not add tools, native `tool_calls`, `ActionProposal`, an orchestrator, policy/approval code, retries, streaming, batching, persistence, a public inference route, or model lifecycle management.
- The normal test suite must pass with LM Studio stopped and without network access.
- `generate()` sends one generation request directly. It must not call `check_availability()` or query model inventory.
- Every generation request sends `stream: false` and `reasoning_effort: "none"`. A caller request with `reasoning=True` fails before transport execution.
- Do not add a tokenizer, estimate input tokens, truncate messages, or reduce `max_output_tokens`.
- Reject only deterministically known output-limit violations: `max_output_tokens <= 0` and `max_output_tokens > settings.lm_studio_context_length`.
- `LMStudioGateway` exclusively owns the transport passed to its constructor. The transport must not be shared. `aclose()` closes it deterministically and is idempotent.
- Do not construct an HTTP client, make network calls, read secrets, manage models, allocate GPU resources, or create files at import time.
- Do not include prompts, response bodies, cookies, authorization headers, tokens, reasoning traces, or private academic content in errors or normal logs.
- Use the measured local profile only: `qwen/qwen3.5-4b`, Q4_K_M, 8192-token context, full GPU offload on the RTX 4050 6GB. The observed approximately 1.3 GiB difference is not a concurrency or runtime guarantee.
- LM Studio Just in Time (JIT) model loading must be disabled for the AAOS runtime profile. The user manually loads the approved model and AAOS never changes this external setting.
- Keep `/health` process-only; it must not contact the model gateway.
- Each task uses test-first red-green-refactor discipline, stages only its listed files, and pauses for review before its commit.

---

## Dependency decision

Use `httpx2>=2.10.0` and import it in production as `import httpx2`.

Rationale:

- `httpx2` 2.10.0 is already resolved in `uv.lock` and used by the existing FastAPI health test, so Phase 0E does not introduce a second HTTP stack.
- It provides the required async client, explicit `aclose()`, typed `AsyncBaseTransport`, production `AsyncHTTPTransport`, offline `MockTransport`, response types, and distinct timeout/request exceptions.
- The adapter needs `httpx2` at runtime, so leaving it only in `[dependency-groups].dev` would make a production installation incomplete.

Implementation-time dependency edits:

- move `"httpx2>=2.10.0"` from `[dependency-groups].dev` to `[project].dependencies` in `pyproject.toml`;
- run `uv lock` so `uv.lock` records `httpx2` under the AAOS runtime dependencies and removes it from AAOS's dev dependency metadata;
- do not change the chosen lower bound or introduce the separate `httpx` package;
- verify with `uv lock --check`.

These files are deliberately unchanged during this planning task.

### Mandatory implementation environment preflight

Before creating a test or changing `pyproject.toml`, `uv.lock`, or any production file, the implementation execution must run:

```powershell
uv lock --check
uv sync --locked --all-groups
uv run python -c "import httpx2; print(httpx2.__version__)"
uv run python -c "import httpx2; required = ('AsyncClient', 'AsyncBaseTransport', 'AsyncHTTPTransport', 'MockTransport', 'TimeoutException', 'RequestError'); missing = [name for name in required if not hasattr(httpx2, name)]; assert not missing, f'Missing httpx2 APIs: {missing}'; print('httpx2 API preflight passed')"
```

Expected: the locked environment synchronizes successfully, reports an installed `httpx2` version satisfying `>=2.10.0`, and exposes all six required APIs. This preflight is mandatory because the planning environment previously had an unusable `.venv` Python launcher. If any command fails, the version is incompatible, or an API is absent, stop and report the exact mismatch before modifying dependency metadata, tests, or production code. Do not silently replace `httpx2`, change the selected lower bound, or continue against a different interpreter.

## Protocol and endpoint decisions

Use the native inventory endpoint for loaded-state diagnostics and the OpenAI-compatible chat endpoint for generation. Phase 0E accepts ordered `system`, `user`, and `assistant` messages, while LM Studio's native `/api/v1/chat` endpoint does not accept assistant messages in request history. The chosen endpoints and fields are:

- `GET /api/v1/models`
  - require a top-level `models` array and validate native `key`, `type`, `loaded_instances`, `loaded_instances[].config.context_length`, and optional `capabilities.reasoning` structures internally;
  - preserve valid model keys in backend order as `available_models` without exposing raw native payloads;
  - set `reachable=True` only after the complete response is schema-valid;
  - set `available=True` only when one entry has `key == settings.lm_studio_model`, `type == "llm"`, and at least one loaded instance;
  - expose only the configured model's validated loaded-instance context lengths as model-independent diagnostic metadata;
  - treat a matching downloaded but unloaded model, a matching non-LLM model, or an absent key as reachable but unavailable;
  - never load, unload, download, or reconfigure a model.
- `POST /v1/chat/completions`
  - send `model`, ordered `messages`, `stream: false`, and `reasoning_effort: "none"`;
  - send `max_tokens` only when `ChatRequest.max_output_tokens` is not `None`;
  - never send tools, tool choice, response persistence, retry, or lifecycle fields;
  - accept visible text only from `choices[0].message.content`;
  - accept model identity from top-level `model`;
  - accept optional `usage.prompt_tokens`, `usage.completion_tokens`, and `usage.total_tokens` only when all three are non-negative integers and total equals input plus output;
  - reject non-empty `reasoning`, `reasoning_content`, or any `tool_calls` as `InferenceProtocolError`.

LM Studio 0.4.8 or newer is required for `reasoning_effort` support on `/v1/chat/completions`. A successful response to a request containing `reasoning_effort: "none"`, with no reasoning payload, is the Phase 0E proof that reasoning was disabled. A backend rejection of that setting maps to `UnsupportedInferenceFeatureError`; it must not be retried without the field.

Implementation references: [LM Studio native model listing](https://lmstudio.ai/docs/developer/rest/list), [LM Studio OpenAI-compatible Chat Completions](https://lmstudio.ai/docs/developer/openai-compat/chat-completions), [LM Studio JIT behavior](https://lmstudio.ai/docs/developer/core/headless), [LM Studio server settings](https://lmstudio.ai/docs/developer/core/server/settings), and [LM Studio 0.4.8 reasoning support](https://lmstudio.ai/changelog/lmstudio/lmstudio-v0.4.8).

## Exact file map

| Path | Action | Responsibility |
|---|---|---|
| `pyproject.toml` | Modify | Promote `httpx2>=2.10.0` from dev-only to runtime. |
| `uv.lock` | Modify | Record the dependency-group move reproducibly. |
| `src/aaos/config.py` | Modify | Add validated immutable local LM Studio settings. |
| `src/aaos/inference/__init__.py` | Create | Re-export model-independent values, protocol, and errors only. |
| `src/aaos/inference/models.py` | Create | Define immutable request, response, usage, and availability values. |
| `src/aaos/inference/gateway.py` | Create | Define the two-operation protocol and safe error hierarchy. |
| `src/aaos/inference/lm_studio.py` | Create | Implement endpoint calls, validation, error translation, ownership, and cleanup. |
| `tests/test_config.py` | Create | Cover inference defaults and validation without network access. |
| `tests/test_inference_models.py` | Create | Cover immutable value invariants. |
| `tests/test_model_gateway.py` | Create | Cover protocol shape and error inheritance. |
| `tests/test_lm_studio_gateway.py` | Create | Cover offline adapter behavior through controlled transports. |
| `tests/test_inference_imports.py` | Create | Prove imports have no client/network/file side effects. |
| `tests/live/test_lm_studio_smoke.py` | Create | Provide one explicitly opted-in real LM Studio diagnostic and generation smoke test. |

`tests/live/test_lm_studio_smoke.py` is the only small file beyond the approximate scope named in the approved design. It is necessary to keep live local-service verification explicit, isolated, skipped by default, and separate from deterministic tests.

## Exact public interfaces

Implement these signatures consistently across all tasks:

```python
# src/aaos/config.py
@dataclass(frozen=True, slots=True)
class AppSettings:
    application_name: str = "aaos"
    application_version: str = "0.1.0"
    environment_name: str = "development"
    lm_studio_base_url: str = "http://127.0.0.1:1234"
    lm_studio_model: str = "qwen/qwen3.5-4b"
    lm_studio_context_length: int = 8192
    lm_studio_request_timeout_seconds: float = 30.0
```

```python
# src/aaos/inference/models.py
ChatRole = Literal["system", "user", "assistant"]

@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: ChatRole
    content: str

@dataclass(frozen=True, slots=True)
class ChatRequest:
    messages: tuple[ChatMessage, ...]
    max_output_tokens: int | None = None
    reasoning: bool = False

@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int

@dataclass(frozen=True, slots=True)
class ChatResponse:
    content: str
    model_identifier: str
    usage: TokenUsage | None = None

@dataclass(frozen=True, slots=True)
class ModelAvailability:
    reachable: bool
    available: bool
    configured_model: str
    available_models: tuple[str, ...]
    loaded_context_lengths: tuple[int, ...] = ()
```

`loaded_context_lengths` contains only validated context lengths from loaded instances of the exact configured LLM. It is empty when the configured model is absent, is not an LLM, or is downloaded but unloaded. This model-independent tuple is the minimum diagnostic metadata needed for the explicit smoke test; native model objects, instance IDs, quantization payloads, and capability payloads remain private to the adapter.

```python
# src/aaos/inference/gateway.py
@runtime_checkable
class ModelGateway(Protocol):
    async def check_availability(self) -> ModelAvailability: pass
    async def generate(self, request: ChatRequest) -> ChatResponse: pass

class InferenceError(RuntimeError): pass
class InferenceConnectionError(InferenceError): pass
class InferenceTimeoutError(InferenceError): pass
class ModelUnavailableError(InferenceError): pass
class InferenceProtocolError(InferenceError): pass
class InferenceBackendError(InferenceError): pass
class UnsupportedInferenceFeatureError(InferenceError): pass
```

The protocol remains exactly the approved two-operation boundary. Cleanup is intentionally a concrete adapter lifecycle method, not a third inference capability on the model-independent protocol.

```python
# src/aaos/inference/lm_studio.py
class LMStudioGateway:
    def __init__(
        self,
        settings: AppSettings,
        transport: httpx2.AsyncBaseTransport,
    ) -> None: pass

    async def check_availability(self) -> ModelAvailability: pass
    async def generate(self, request: ChatRequest) -> ChatResponse: pass
    async def aclose(self) -> None: pass
```

The constructor creates one private `httpx2.AsyncClient` with the supplied transport, `base_url=settings.lm_studio_base_url`, `timeout=settings.lm_studio_request_timeout_seconds`, `follow_redirects=False`, and `trust_env=False`. Construction transfers exclusive ownership of the transport to the gateway. Production callers supply a fresh `httpx2.AsyncHTTPTransport()`; tests supply a fresh controlled `AsyncBaseTransport`. Callers must not reuse either transport after construction.

## Safe error policy

- `httpx2.TimeoutException` -> `InferenceTimeoutError`.
- Any other `httpx2.RequestError` -> `InferenceConnectionError`.
- A structured backend error with `error.code == "model_not_found"` or `error.type == "model_not_found"` during generation -> `ModelUnavailableError`.
- A structured backend rejection where `error.param == "reasoning_effort"` -> `UnsupportedInferenceFeatureError`.
- Other non-2xx responses, including context-window rejection -> `InferenceBackendError`.
- Invalid JSON or an invalid success payload -> `InferenceProtocolError`.
- `check_availability()` is diagnostic-only but still raises explicit connection, timeout, backend, or protocol errors; a completed inventory response returns `reachable=True`, with `available` reflecting exact identifier presence.

Use these exact safe message templates:

```text
LM Studio request timed out during {operation} for model '{model}'
LM Studio connection failed during {operation} for model '{model}'
LM Studio model '{model}' is unavailable during generation
LM Studio returned invalid data during {operation} for model '{model}'
LM Studio backend failed during {operation} for model '{model}' (status {status_code})
LM Studio does not support required reasoning-off generation for model '{model}'
```

Never append exception text or backend data. Raise translated errors `from None` so transport exception text cannot leak through a chained traceback presented to callers. Treat `error.param == "reasoning_effort"` as the structured unsupported-reasoning signal; do not classify errors by searching the human-readable backend message.

---

### Task 1: Runtime dependency and immutable LM Studio settings

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `src/aaos/config.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Produces: the exact `AppSettings` fields shown above and a runtime `httpx2` dependency.
- Preserves: existing application metadata and `DEFAULT_SETTINGS = AppSettings()`.

- [ ] **Step 1: Pass the mandatory `httpx2` environment preflight**

Run the four commands in **Mandatory implementation environment preflight** exactly as written. Confirm the installed version satisfies `httpx2>=2.10.0` and all six required symbols exist.

Expected: PASS. On any failure or mismatch, stop and report it with a clean working tree; do not write the failing test, move the dependency, update the lock, or modify production code.

- [ ] **Step 2: Write failing configuration tests**

Add tests asserting exact defaults, frozen behavior, trailing-slash normalization, and rejection of non-loopback/non-HTTP URLs, blank model identifiers, non-positive/bool context lengths, and non-positive/non-finite timeouts.

```python
def test_inference_settings_have_safe_local_defaults() -> None:
    settings = AppSettings()
    assert settings.lm_studio_base_url == "http://127.0.0.1:1234"
    assert settings.lm_studio_model == "qwen/qwen3.5-4b"
    assert settings.lm_studio_context_length == 8192
    assert settings.lm_studio_request_timeout_seconds == 30.0

def test_base_url_is_normalized_without_trailing_slash() -> None:
    settings = AppSettings(lm_studio_base_url="http://localhost:1234/")
    assert settings.lm_studio_base_url == "http://localhost:1234"
```

- [ ] **Step 3: Run the focused test and confirm red**

Run: `uv run pytest tests/test_config.py -v`

Expected: FAIL because the four LM Studio settings and validation do not exist.

- [ ] **Step 4: Promote `httpx2` and update the lock**

Move `"httpx2>=2.10.0"` into `[project].dependencies`, remove it from the dev group, then run:

```powershell
uv lock
uv lock --check
```

Expected: both commands succeed; `uv.lock` lists `httpx2` as an AAOS runtime dependency and no longer as an AAOS dev dependency. This dependency edit occurs only after the mandatory environment preflight above has passed.

- [ ] **Step 5: Implement minimal settings validation**

Use `urllib.parse.urlsplit`, `math.isfinite`, and `object.__setattr__` inside `AppSettings.__post_init__`. Accept only `http` loopback hosts (`127.0.0.1`, `localhost`, `::1`), with no credentials, query, fragment, or non-root path. Strip only the base URL's trailing slash and surrounding model-identifier whitespace. Do not contact LM Studio.

- [ ] **Step 6: Run focused and regression tests**

Run:

```powershell
uv run pytest tests/test_config.py tests/test_environment.py tests/test_health.py -v
uv run ruff check src/aaos/config.py tests/test_config.py
uv run mypy src/aaos/config.py tests/test_config.py
```

Expected: PASS; `/health` retains the existing metadata contract.

- [ ] **Step 7: Review checkpoint and commit**

Confirm the diff contains only settings, dependency metadata, lock changes, and tests; no client construction or network call exists.

```powershell
git diff --check
git add pyproject.toml uv.lock src/aaos/config.py tests/test_config.py
git commit -m "feat: add local inference configuration"
```

---

### Task 2: Immutable model-independent values

**Files:**
- Create: `src/aaos/inference/models.py`
- Create: `tests/test_inference_models.py`

**Interfaces:**
- Produces: `ChatRole`, `ChatMessage`, `ChatRequest`, `TokenUsage`, `ChatResponse`, and `ModelAvailability` with the exact signatures above.
- Consumed later by: `ModelGateway` and `LMStudioGateway`.

- [ ] **Step 1: Write failing invariant tests**

Cover valid ordered messages, tuple immutability, whitespace-only content rejection, invalid roles, empty requests, `max_output_tokens <= 0`, non-bool reasoning, non-negative exact usage totals, non-empty response fields, and availability consistency.

```python
def test_chat_request_preserves_ordered_messages() -> None:
    messages = (
        ChatMessage(role="system", content="Be concise."),
        ChatMessage(role="user", content="Hello"),
        ChatMessage(role="assistant", content="Hi"),
    )
    assert ChatRequest(messages=messages).messages == messages

def test_non_positive_output_limit_is_rejected() -> None:
    with pytest.raises(ValueError, match="max_output_tokens must be positive"):
        ChatRequest(
            messages=(ChatMessage(role="user", content="Hello"),),
            max_output_tokens=0,
        )
```

- [ ] **Step 2: Run the focused test and confirm red**

Run: `uv run pytest tests/test_inference_models.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'aaos.inference'`.

- [ ] **Step 3: Implement the minimal frozen dataclasses**

Use `@dataclass(frozen=True, slots=True)` throughout. Preserve message content verbatim after checking `content.strip()`. Reject bool values where integer counts are required. `TokenUsage.total_tokens` must equal `input_tokens + output_tokens`. `ModelAvailability` must reject `available=True` when `reachable=False`, when `configured_model` is absent from `available_models`, or when `loaded_context_lengths` is empty. Every loaded context length must be a positive non-bool integer; unavailable results must use an empty tuple.

- [ ] **Step 4: Run focused checks**

Run:

```powershell
uv run pytest tests/test_inference_models.py -v
uv run ruff check src/aaos/inference/models.py tests/test_inference_models.py
uv run mypy src/aaos/inference/models.py tests/test_inference_models.py
```

Expected: PASS.

- [ ] **Step 5: Review checkpoint and commit**

Confirm no LM Studio or `httpx2` type appears in `models.py`.

```powershell
git diff --check
git add src/aaos/inference/models.py tests/test_inference_models.py
git commit -m "feat: add model-independent inference values"
```

---

### Task 3: Two-operation protocol and safe error hierarchy

**Files:**
- Create: `src/aaos/inference/gateway.py`
- Create: `src/aaos/inference/__init__.py`
- Create: `tests/test_model_gateway.py`

**Interfaces:**
- Consumes: all values from `aaos.inference.models`.
- Produces: the exact `ModelGateway` protocol and seven error classes shown above.

- [ ] **Step 1: Write failing protocol tests**

Use a local fake implementing only the approved calls. Assert runtime protocol conformance, method results, and that every specific error is an `InferenceError`.

```python
class FakeGateway:
    async def check_availability(self) -> ModelAvailability:
        return ModelAvailability(
            reachable=True,
            available=True,
            configured_model="qwen/qwen3.5-4b",
            available_models=("qwen/qwen3.5-4b",),
        )

    async def generate(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(content="ready", model_identifier="qwen/qwen3.5-4b")

def test_two_operation_gateway_satisfies_protocol() -> None:
    assert isinstance(FakeGateway(), ModelGateway)
```

- [ ] **Step 2: Run the focused test and confirm red**

Run: `uv run pytest tests/test_model_gateway.py -v`

Expected: FAIL because `gateway.py` and public exports do not exist.

- [ ] **Step 3: Implement the protocol and errors**

Define exactly the two async protocol methods. Keep errors behavior-free and derive all six specific categories directly from `InferenceError`. Re-export only the protocol, errors, and model-independent values from `aaos.inference`; do not import `lm_studio` there.

- [ ] **Step 4: Run focused checks**

Run:

```powershell
uv run pytest tests/test_model_gateway.py tests/test_inference_models.py -v
uv run ruff check src/aaos/inference tests/test_model_gateway.py
uv run mypy src/aaos/inference tests/test_model_gateway.py
```

Expected: PASS.

- [ ] **Step 5: Review checkpoint and commit**

Confirm `ModelGateway` has no cleanup, tool, streaming, retry, or lifecycle-management method.

```powershell
git diff --check
git add src/aaos/inference/__init__.py src/aaos/inference/gateway.py tests/test_model_gateway.py
git commit -m "feat: define model gateway contract"
```

---

### Task 4: Offline availability diagnostics

**Files:**
- Create: `src/aaos/inference/lm_studio.py`
- Create: `tests/test_lm_studio_gateway.py`
- Create: `tests/test_inference_imports.py`

**Interfaces:**
- Consumes: `AppSettings`, `ModelAvailability`, gateway errors, and a fresh `httpx2.AsyncBaseTransport`.
- Produces: `LMStudioGateway.__init__`, `check_availability()`, and the initial `aclose()` implementation.

- [ ] **Step 1: Write failing availability tests**

Use a fresh `httpx2.MockTransport` per gateway and invoke async methods through `asyncio.run`. Assert one `GET /api/v1/models`, no other request, exact key matching, `type == "llm"`, loaded-instance detection, loaded context extraction, backend-order preservation, absent-model and downloaded-but-unloaded results, malformed payload errors, safe non-success errors, timeout translation, and request-error translation.

```python
def test_availability_matches_exact_configured_model() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200,
            json={
                "models": [
                    {
                        "key": "qwen/qwen3.5-4b",
                        "type": "llm",
                        "loaded_instances": [
                            {
                                "id": "qwen/qwen3.5-4b",
                                "config": {"context_length": 8192},
                            }
                        ],
                        "capabilities": {
                            "reasoning": {
                                "allowed_options": ["off", "on"],
                                "default": "on",
                            }
                        },
                    },
                    {
                        "key": "another/downloaded-model",
                        "type": "llm",
                        "loaded_instances": [],
                    },
                ]
            },
        )

    async def scenario() -> ModelAvailability:
        gateway = LMStudioGateway(AppSettings(), httpx2.MockTransport(handler))
        try:
            return await gateway.check_availability()
        finally:
            await gateway.aclose()

    result = asyncio.run(scenario())

    assert result.reachable is True
    assert result.available is True
    assert result.available_models == (
        "qwen/qwen3.5-4b",
        "another/downloaded-model",
    )
    assert result.loaded_context_lengths == (8192,)
    assert [request.url.path for request in seen] == ["/api/v1/models"]


def test_downloaded_but_unloaded_model_is_unavailable() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "models": [
                    {
                        "key": "qwen/qwen3.5-4b",
                        "type": "llm",
                        "loaded_instances": [],
                    }
                ]
            },
        )

    async def scenario() -> ModelAvailability:
        gateway = LMStudioGateway(AppSettings(), httpx2.MockTransport(handler))
        try:
            return await gateway.check_availability()
        finally:
            await gateway.aclose()

    result = asyncio.run(scenario())

    assert result.reachable is True
    assert result.available is False
    assert result.available_models == ("qwen/qwen3.5-4b",)
    assert result.loaded_context_lengths == ()
```

- [ ] **Step 2: Write the failing import-side-effect test**

In a fresh isolated Python process, replace `httpx2.AsyncClient` and `httpx2.AsyncHTTPTransport` with callables that raise, import `aaos.config`, `aaos.inference`, `aaos.inference.models`, `aaos.inference.gateway`, `aaos.inference.lm_studio`, and `aaos.main`, then assert exit code zero and no `data/` tree was created in `tmp_path`.

```python
script = """
import httpx2
def forbidden(*args, **kwargs):
    raise AssertionError("HTTP construction during import")
httpx2.AsyncClient = forbidden
httpx2.AsyncHTTPTransport = forbidden
import aaos.config
import aaos.inference
import aaos.inference.models
import aaos.inference.gateway
import aaos.inference.lm_studio
import aaos.main
"""
```

- [ ] **Step 3: Run the focused tests and confirm red**

Run:

```powershell
uv run pytest tests/test_lm_studio_gateway.py -k availability -v
uv run pytest tests/test_inference_imports.py -v
```

Expected: FAIL because `LMStudioGateway` and `aaos.inference.lm_studio` do not exist.

- [ ] **Step 4: Implement construction and availability minimally**

Create a private `httpx2.AsyncClient` with the exact constructor policy in this plan. Call `/api/v1/models` and require a JSON object with a `models` list. Require each model to have a non-empty string `key`, a supported `type`, and a `loaded_instances` list; require each loaded instance to contain a `config` object with a positive non-bool integer `context_length`. When `capabilities.reasoning` is present, validate its object shape, allowed-option strings, and default internally, then discard it rather than exposing native data.

Preserve model keys in backend order. For the exact configured key, set `available=True` only if its type is `llm` and at least one instance is loaded, and populate `loaded_context_lengths` from those instances. Otherwise return a reachable but unavailable result with an empty context tuple. A response is not reachable evidence until all required schema validation succeeds; malformed native data raises `InferenceProtocolError`. Translate other failures using safe fixed messages and never include `response.text` or exception text.

- [ ] **Step 5: Run focused checks**

Run:

```powershell
uv run pytest tests/test_lm_studio_gateway.py -k availability -v
uv run pytest tests/test_inference_imports.py -v
uv run ruff check src/aaos/inference/lm_studio.py tests/test_lm_studio_gateway.py tests/test_inference_imports.py
uv run mypy src/aaos/inference/lm_studio.py tests/test_lm_studio_gateway.py tests/test_inference_imports.py
```

Expected: PASS with no live server.

- [ ] **Step 6: Review checkpoint and commit**

Confirm availability is observational only: it uses one native inventory GET and contains no POST, load, unload, download, reconfiguration, retry, or remediation path.

```powershell
git diff --check
git add src/aaos/inference/lm_studio.py tests/test_lm_studio_gateway.py tests/test_inference_imports.py
git commit -m "feat: add LM Studio availability diagnostics"
```

---

### Task 5: Direct plain generation with reasoning disabled

**Files:**
- Modify: `src/aaos/inference/lm_studio.py`
- Modify: `tests/test_lm_studio_gateway.py`

**Interfaces:**
- Consumes: `ChatRequest` and configured model/context settings.
- Produces: `LMStudioGateway.generate(request: ChatRequest) -> ChatResponse`.

- [ ] **Step 1: Write failing direct-generation tests**

Assert exactly one POST to `/v1/chat/completions`; ordered messages remain unchanged; payload contains `model`, `stream: false`, and `reasoning_effort: "none"`; optional `max_tokens` is omitted/present correctly; no `/api/v1/models` request occurs; and a valid response maps to `ChatResponse` and `TokenUsage`.

```python
def test_generate_sends_one_direct_non_reasoning_request() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "model": "qwen/qwen3.5-4b",
                "choices": [{"message": {"role": "assistant", "content": "Hello"}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
            },
        )

    request = ChatRequest(messages=(ChatMessage(role="user", content="Hello"),))
    async def scenario() -> ChatResponse:
        gateway = LMStudioGateway(AppSettings(), httpx2.MockTransport(handler))
        try:
            return await gateway.generate(request)
        finally:
            await gateway.aclose()

    result = asyncio.run(scenario())

    assert result.content == "Hello"
    assert [item.url.path for item in requests] == ["/v1/chat/completions"]
    payload = json.loads(requests[0].content)
    assert payload["reasoning_effort"] == "none"
    assert payload["stream"] is False
```

- [ ] **Step 2: Run the focused tests and confirm red**

Run: `uv run pytest tests/test_lm_studio_gateway.py -k "generate and not error" -v`

Expected: FAIL because `generate()` is not implemented.

- [ ] **Step 3: Implement minimal serialization and response parsing**

Before transport execution:

```python
if request.reasoning:
    raise UnsupportedInferenceFeatureError("Reasoning is not supported in Phase 0E")
if (
    request.max_output_tokens is not None
    and request.max_output_tokens > self._settings.lm_studio_context_length
):
    raise ValueError("max_output_tokens exceeds the configured context length")
```

Build the exact payload described above. Do not calculate message tokens or mutate the request. Require a non-empty visible `choices[0].message.content`, non-empty model identifier, and valid optional usage. Reject tool calls or non-empty reasoning fields as protocol failures.

- [ ] **Step 4: Add tokenizer-free boundary tests**

Test `max_output_tokens == context_length` succeeds unchanged, `> context_length` fails before the handler, very long message text is sent unchanged, and no `max_tokens` is silently lowered.

- [ ] **Step 5: Run focused checks**

Run:

```powershell
uv run pytest tests/test_lm_studio_gateway.py -k generate -v
uv run ruff check src/aaos/inference/lm_studio.py tests/test_lm_studio_gateway.py
uv run mypy src/aaos/inference/lm_studio.py tests/test_lm_studio_gateway.py
```

Expected: PASS; request capture proves no preflight and no truncation.

- [ ] **Step 6: Review checkpoint and commit**

Confirm no tokenizer, token estimator, retry, stream handler, tool schema, or inventory call exists in generation.

```powershell
git diff --check
git add src/aaos/inference/lm_studio.py tests/test_lm_studio_gateway.py
git commit -m "feat: add plain LM Studio generation"
```

---

### Task 6: Failure translation, redaction, and transport lifecycle

**Files:**
- Modify: `src/aaos/inference/lm_studio.py`
- Modify: `tests/test_lm_studio_gateway.py`

**Interfaces:**
- Completes: all error mappings and idempotent `aclose()`.
- Ownership rule: each test creates a new transport and transfers it to exactly one gateway.

- [ ] **Step 1: Write failing error-matrix tests**

Parametrize generation cases for timeout, connection failure, structured `model_not_found`, reasoning-field rejection, context-window backend rejection, generic non-success status, malformed JSON, missing choices, empty content, malformed usage, reasoning content, and tool calls.

Use sentinel values in the prompt, backend JSON, `set-cookie`, and `authorization` headers. Assert none appears in `str(error)` and `error.__cause__ is None`.

```python
@pytest.mark.parametrize(
    ("backend_error", "expected_type"),
    [
        ({"error": {"code": "model_not_found", "message": "SECRET_BODY"}}, ModelUnavailableError),
        ({"error": {"code": "context_length_exceeded", "message": "SECRET_BODY"}}, InferenceBackendError),
    ],
)
def test_backend_errors_are_typed_without_body_leakage(
    backend_error: dict[str, object],
    expected_type: type[InferenceError],
) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(400, json=backend_error, headers={"set-cookie": "SECRET_COOKIE"})

    request = ChatRequest(messages=(ChatMessage(role="user", content="SECRET_PROMPT"),))

    async def scenario() -> None:
        gateway = LMStudioGateway(AppSettings(), httpx2.MockTransport(handler))
        try:
            await gateway.generate(request)
        finally:
            await gateway.aclose()

    with pytest.raises(expected_type) as caught:
        asyncio.run(scenario())

    assert "SECRET_BODY" not in str(caught.value)
    assert "SECRET_COOKIE" not in str(caught.value)
    assert "SECRET_PROMPT" not in str(caught.value)
    assert caught.value.__cause__ is None
```

- [ ] **Step 2: Run the error tests and confirm red**

Run: `uv run pytest tests/test_lm_studio_gateway.py -k "error or timeout or connection or malformed or reasoning or tool" -v`

Expected: at least one FAIL for each not-yet-translated category.

- [ ] **Step 3: Implement one centralized safe translation path**

Inspect structured error codes internally only. Build fixed caller messages from operation, model identifier, and status code. Never log or interpolate response content, headers, request content, or exception strings. Map a reasoning-setting rejection to `UnsupportedInferenceFeatureError` and never retry without `reasoning_effort`.

- [ ] **Step 4: Write failing lifecycle tests**

Define a test-only `RecordingTransport(httpx2.AsyncBaseTransport)` with `request_count` and `close_count`. Assert construction does not send requests, first `aclose()` closes once, second `aclose()` leaves `close_count == 1`, and a closed gateway raises `InferenceError("LM Studio gateway is closed")` before transport execution. Never share the instance between gateways.

- [ ] **Step 5: Implement idempotent cleanup**

Guard the private client's `aclose()` with a private `_closed` flag. The first call closes the client and its exclusively owned transport; subsequent calls return without another close. Do not add a finalizer or global cleanup hook.

- [ ] **Step 6: Run the full adapter suite**

Run:

```powershell
uv run pytest tests/test_lm_studio_gateway.py -v
uv run ruff check src/aaos/inference/lm_studio.py tests/test_lm_studio_gateway.py
uv run mypy src/aaos/inference/lm_studio.py tests/test_lm_studio_gateway.py
```

Expected: PASS offline, including the full failure matrix and close-count assertions.

- [ ] **Step 7: Review checkpoint and commit**

Review every error string and confirm there is no logging call, raw payload exposure, hidden fallback, retry, or shared transport.

```powershell
git diff --check
git add src/aaos/inference/lm_studio.py tests/test_lm_studio_gateway.py
git commit -m "test: harden model gateway failures and cleanup"
```

---

### Task 7: Import-side-effect and regression boundary

**Files:**
- Verify unchanged: `tests/test_inference_imports.py`
- Verify unchanged: `src/aaos/main.py`
- Verify unchanged: existing tests

**Interfaces:**
- Proves: importing configuration, inference modules, or FastAPI does not construct clients, contact LM Studio, or create local state.

- [ ] **Step 1: Re-run the import-side-effect regression test**

Run: `uv run pytest tests/test_inference_imports.py -v`

Expected: PASS. A failure with `HTTP construction during import` is a Phase 0E regression and blocks the checkpoint.

- [ ] **Step 2: Make only the minimal test-first correction if red**

Retain the failing regression test, move any client/transport construction it finds into `LMStudioGateway.__init__`, and rerun the focused test. Do not change `main.py` or wire the gateway into FastAPI.

- [ ] **Step 3: Run all deterministic tests**

Run:

```powershell
uv run pytest -v
uv run ruff check .
uv run mypy src tests
```

Expected: PASS with LM Studio stopped; the live test introduced next will be skipped by default.

- [ ] **Step 4: Review checkpoint**

Confirm `/health` remains process-liveness only and no public inference route was added.

If the checkpoint required a production correction, stage only that correction and its already-failing regression test, then commit `fix: preserve inference import safety`. If no correction was required, do not create an empty commit.

---

### Task 8: Explicit opt-in live LM Studio smoke test

**Files:**
- Create: `tests/live/test_lm_studio_smoke.py`

**Interfaces:**
- Consumes: `DEFAULT_SETTINGS`, a fresh `httpx2.AsyncHTTPTransport`, and `LMStudioGateway`.
- Produces: one skipped-by-default test that checks loaded availability and the approved context profile, then sends one short plain generation request.

- [ ] **Step 1: Write the skipped-by-default smoke test**

At module scope, skip unless `AAOS_RUN_LM_STUDIO_SMOKE == "1"`. In the test, create a fresh transport, transfer it to one gateway, call the explicit availability diagnostic, require an exact loaded LLM with at least one loaded instance whose context length is at least the configured 8192 tokens, then generate a fixed non-sensitive prompt with `reasoning=False` and a small output limit. Assert non-empty content and the expected model identifier, record only elapsed seconds and numeric usage with pytest's captured output, and close in `finally`.

```python
import asyncio
import logging
import os
import time

import httpx2
import pytest

from aaos.config import DEFAULT_SETTINGS
from aaos.inference.lm_studio import LMStudioGateway
from aaos.inference.models import ChatMessage, ChatRequest, ChatResponse

LOGGER = logging.getLogger(__name__)

pytestmark = pytest.mark.skipif(
    os.environ.get("AAOS_RUN_LM_STUDIO_SMOKE") != "1",
    reason="set AAOS_RUN_LM_STUDIO_SMOKE=1 for the local LM Studio smoke test",
)


def test_live_lm_studio_plain_generation() -> None:
    async def scenario() -> ChatResponse:
        gateway = LMStudioGateway(DEFAULT_SETTINGS, httpx2.AsyncHTTPTransport())
        try:
            availability = await gateway.check_availability()
            assert availability.reachable
            assert availability.available
            assert any(
                context_length >= DEFAULT_SETTINGS.lm_studio_context_length
                for context_length in availability.loaded_context_lengths
            )
            started = time.perf_counter()
            response = await gateway.generate(
                ChatRequest(
                    messages=(ChatMessage(role="user", content="Reply with exactly: AAOS ready"),),
                    max_output_tokens=16,
                    reasoning=False,
                )
            )
            elapsed_seconds = time.perf_counter() - started
            LOGGER.info(
                "LM Studio smoke completed elapsed_seconds=%.3f usage=%s",
                elapsed_seconds,
                response.usage,
            )
            return response
        finally:
            await gateway.aclose()

    response = asyncio.run(scenario())
    assert response.content.strip()
    assert response.model_identifier == DEFAULT_SETTINGS.lm_studio_model
```

- [ ] **Step 2: Prove normal execution does not contact LM Studio**

Run: `uv run pytest tests/live/test_lm_studio_smoke.py -v`

Expected: SKIPPED with the explicit opt-in reason, even when LM Studio is stopped.

- [ ] **Step 3: Run the opt-in live test manually**

Prerequisites: the user starts LM Studio 0.4.8+, disables **Just in Time Model Loading** in LM Studio Server Settings, and manually loads `qwen/qwen3.5-4b` Q4_K_M with context length 8192 and full GPU offload. The user also confirms the approved reasoning configuration. AAOS and the smoke test must not change the JIT setting or load/reconfigure the model automatically.

Run:

```powershell
$env:AAOS_RUN_LM_STUDIO_SMOKE = "1"
uv run pytest tests/live/test_lm_studio_smoke.py -v --log-cli-level=INFO
Remove-Item Env:AAOS_RUN_LM_STUDIO_SMOKE
```

Expected: PASS; one explicit `GET /api/v1/models` diagnostic proves the exact LLM is loaded with at least one `context_length >= 8192`, followed by one direct `POST /v1/chat/completions`. The GET belongs to the smoke diagnostic; `generate()` itself performs no GET or availability preflight. The test must not start, load, unload, download, reconfigure the model, or alter JIT. If the user has not prepared the service and approved profile, record the explicit connection/model/profile failure and do not treat the sub-gate as passed.

- [ ] **Step 4: Review checkpoint and commit**

Confirm the prompt/response are not printed, the opt-in variable contains no secret, and default pytest remains offline.

```powershell
git diff --check
git add tests/live/test_lm_studio_smoke.py
git commit -m "test: add opt-in LM Studio smoke test"
```

---

### Task 9: Full Phase 0E verification and acceptance report

**Files:**
- Modify only if verification finds a Phase 0E defect: files already listed in this plan.
- Do not create Phase 1+, orchestration, tooling, or public API files.

- [ ] **Step 1: Reverify dependency, lock, and selected `httpx2` API consistency**

Run:

```powershell
uv lock --check
uv sync --locked --all-groups
uv run python -c "import httpx2; print(httpx2.__version__)"
uv run python -c "import httpx2; required = ('AsyncClient', 'AsyncBaseTransport', 'AsyncHTTPTransport', 'MockTransport', 'TimeoutException', 'RequestError'); missing = [name for name in required if not hasattr(httpx2, name)]; assert not missing, f'Missing httpx2 APIs: {missing}'; print('httpx2 API preflight passed')"
```

Expected: PASS; a production install includes `httpx2>=2.10.0` through `[project].dependencies`, reports its installed version, and exposes every selected API. A mismatch blocks Phase 0E acceptance and must not be hidden by changing dependencies or tests.

- [ ] **Step 2: Run formatting/lint/type/test gates**

Run:

```powershell
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest -v
```

Expected: all checks PASS; the live smoke test is SKIPPED unless explicitly enabled.

- [ ] **Step 3: Re-run import-side-effect verification explicitly**

Run: `uv run pytest tests/test_inference_imports.py tests/test_health.py -v`

Expected: PASS with no HTTP construction, network call, model action, database creation, or `/health` behavior change.

- [ ] **Step 4: Run repository safety checks**

Run:

```powershell
git diff --check
git status --short --branch
git ls-files data/auth data/credentials
```

Expected: no whitespace errors; only intended Phase 0E changes before the final commit; no tracked auth/credential files.

- [ ] **Step 5: Review the final diff against the approved design**

Confirm every checklist item below with file/test evidence. If any item fails, fix only the smallest Phase 0E defect, add or retain the failing regression test first, and rerun the relevant gate. Never weaken a test.

- [ ] **Step 6: Create the final implementation commit only after all gates pass**

If Task 9 required no corrective changes, do not create an empty commit. If it required reviewed fixes, stage only those exact files and use a focused commit message such as:

```powershell
git commit -m "fix: complete Phase 0E gateway acceptance"
```

## Phase 0E acceptance checklist

- [ ] Only the approved inference package, focused tests, dependency metadata, and explicit smoke mechanism were added.
- [ ] `AppSettings` contains only the four approved non-secret inference fields with safe validated defaults.
- [ ] `ModelGateway` exposes exactly diagnostic availability and plain generation using model-independent types.
- [ ] `LMStudioGateway` receives one dedicated transport, takes exclusive ownership, and closes it exactly once through idempotent async cleanup.
- [ ] No global/import-time client, network call, model lifecycle action, GPU allocation, file creation, or secret read occurs.
- [ ] Availability uses exactly one `GET /api/v1/models`, validates the native schema, compares the configured key exactly, requires `type == "llm"` plus at least one loaded instance, performs no remediation, and returns explicit safe errors.
- [ ] Availability differentiates a downloaded-but-unloaded model from a loaded model and reports the former as reachable but unavailable.
- [ ] Native `loaded_instances[].config.context_length` and optional `capabilities.reasoning` are validated internally; raw native payloads are not exposed to callers.
- [ ] Generation performs exactly one `POST /v1/chat/completions` and never performs availability/model-inventory preflight.
- [ ] Every generation sends `reasoning_effort: "none"` and `stream: false`; caller reasoning enablement fails closed before transport.
- [ ] No successful response exposes reasoning text or tool calls.
- [ ] `max_output_tokens <= 0` and `> context_length` are rejected; no prompt-token estimation, tokenizer, truncation, or silent reduction exists.
- [ ] Backend total-context rejection remains an explicit `InferenceBackendError`.
- [ ] Connection, timeout, model unavailable, backend, protocol, and unsupported-feature categories have offline tests.
- [ ] Errors and logs expose no prompt, backend body, headers, cookies, tokens, reasoning traces, or private academic content.
- [ ] The mandatory pre-modification environment preflight passed before any test, dependency, or production edit; the locked interpreter reports `httpx2>=2.10.0` and all six required APIs.
- [ ] `httpx2>=2.10.0` is a normal runtime dependency, `uv.lock` is current, and production imports use `httpx2`.
- [ ] Existing Phase 0A-0D tests, `/health`, Ruff, mypy, pytest, lock, whitespace, and Git credential checks pass.
- [ ] Default tests pass with LM Studio stopped; the live smoke is skipped unless explicitly opted in.
- [ ] JIT model loading is explicitly disabled by the user as an external AAOS runtime prerequisite; AAOS does not inspect, toggle, or bypass that setting.
- [ ] The opt-in smoke verifies reachability, exact loaded-LLM availability, at least one loaded context length `>= 8192`, and schema-valid plain generation without managing LM Studio.
- [ ] No Phase 1+ or Phase 5 capability, public inference endpoint, retry, streaming, batching, concurrency promise, or speculative abstraction was introduced.
- [ ] Phase 0E passing is reported as a sub-gate only; Phase 0 remains in progress pending its remaining acceptance criteria and explicit proceed decision.

## Final task report

After implementation, report exactly:

1. files changed;
2. commands executed;
3. tests executed and results, including whether the live smoke ran or remained skipped;
4. failures or limitations, including LM Studio/version/model availability and the 6GB VRAM operational constraint;
5. recommended next action: review Phase 0E evidence and decide whether its sub-gate passes. Do not proceed to another phase automatically.
