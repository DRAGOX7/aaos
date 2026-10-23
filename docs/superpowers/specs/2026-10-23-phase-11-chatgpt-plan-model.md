# Phase 11 Spec — Optional ChatGPT-Plan Cloud Model (Sign in with ChatGPT)

**Status: DRAFT for owner approval. No code, and no probe, until it is approved.**

- **Authorization:** `docs/GOVERNANCE_LEDGER.md`, "Phase 11 authorization (2026-10-23)".
- **Design note:** `docs/superpowers/specs/2026-10-23-chatgpt-plan-model-gateway-design.md`.
  It holds the background, the OpenAI facts as read on 2026-10-23, and the
  reasons behind the choices below.
- **Owner decisions:** §2.
- **Scope:** configuration, credential store, sign-in command, owner-present
  probe, `ChatGPTPlanGateway`, model selection and UI, RAG wiring and evaluation.
- **Reused unchanged:**
  - the `ModelGateway` contract and `ChatRequest`/`ChatResponse`;
  - retrieval, embeddings and the local NLI claim checker;
  - prompts, the strict reply parsers, the approval core, and the local and
    Gemini gateways.

## 1. Invariants

1. **Local stays the default.** With nothing configured, AAOS behaves exactly
   as today. The ChatGPT-plan model answers only after the owner signs in,
   selects it, and confirms the cloud warning.
2. **A plain model call.** The gateway sends no tools, functions, web search,
   MCP servers or agent settings, and never runs the Codex CLI. AAOS's prompt is
   the only instruction it sends.
3. **Fixed hosts.** `auth.openai.com` and `api.openai.com/v1` are code constants,
   with HTTPS only, no redirects and no proxies from the environment
   (`trust_env=False`).
4. **The owner starts sign-in.** No question, scheduled job, web request or
   model output can start a sign-in.
5. **Secrets never leak.** The access, refresh and ID tokens, the PKCE verifier,
   `state` and `nonce` never appear in:
   - logs, telemetry, exceptions or `repr`;
   - API responses or the frontend;
   - model context or evaluation output;
   - Git.
6. **One store owns the tokens.** Only `ChatGPTCredentialStore` reads, writes or
   refreshes them. The gateway asks it for a valid access token and never sees
   the refresh token.
7. **Plan usage only.** A sign-in without the `chatgpt.tokens.use.direct` scope
   is rejected. A plan-limit error fails the request, and AAOS never retries it
   or moves it to other billing.
8. **The same data boundary as Gemini.** Only the question text and the selected
   course excerpts leave the machine, with `store=false`. No Moodle cookies,
   file paths or other credentials are ever sent.
9. **Replies stay untrusted.** Every reply goes through the existing strict
   parsers, coverage checks and entailment checks, exactly as for the other
   models.

## 2. Owner decisions (to approve or change)

| # | Decision | Recommended default |
|---|---|---|
| D1 | Where sign-in runs | **CLI only** in v1 (`aaos model chatgpt-plan sign-in`). No browser route can start a sign-in or receive a code. |
| D2 | Credential file location | `%LOCALAPPDATA%\AAOS\credentials\chatgpt-plan.json`, overridable with `AAOS_CHATGPT_PLAN_CREDENTIAL_PATH`. Any path inside the repository is refused. |
| D3 | ID-token verification | Add `PyJWT[crypto]`, pinned, as a direct dependency. This is the only new dependency; `httpx2` stays the HTTP client. No `pydantic-ai`. |
| D4 | When the probe runs | After the store and sign-in (11A–11B), before the gateway (11D). The probe then uses AAOS's own sign-in, and its findings shape only the gateway. |
| D5 | Switching between cloud providers | Moving from Gemini to the ChatGPT plan, or back, needs `confirm_cloud` again, because the data goes to a different company. |
| D6 | Default model | None. The owner sets `AAOS_CHATGPT_PLAN_MODEL` or picks one from the listed models, as with Gemini. |
| D7 | Usage cap | AAOS adds no cap of its own. The sign-in command tells the owner to set a weekly cap for AAOS at chatgpt.com/settings/usage. |

## 3. Configuration (11A)

- **`MODEL_PROVIDERS`** becomes `("lm_studio", "gemini", "chatgpt_plan")`.
- **`AppSettings`** gains `chatgpt_plan_model: str | None`. It is valid only when
  `model_provider == "chatgpt_plan"`, the same rule as `gemini_model`.
- **Environment:**
  - `AAOS_MODEL_PROVIDER=chatgpt_plan` selects it;
  - `AAOS_CHATGPT_PLAN_MODEL` names the model;
  - `AAOS_CHATGPT_PLAN_CREDENTIAL_PATH` optionally moves the credential file;
  - `AAOS_CHATGPT_PLAN_MIN_INTERVAL_SECONDS` optionally spaces requests, as
    Gemini's setting does.
  - No token is ever read from the environment.
- **Budget:** the prompt-budget check keeps using `lm_studio_context_length`, so
  retrieval and prompt sizes stay identical across providers.

## 4. Credential store (11A)

**File content**, as JSON with a version field:

- `access_token`;
- `refresh_token`;
- `expires_at` (UTC);
- `scopes`;
- `client_id` (issued at the first sign-in);
- `host_id` (the per-install `urn:uuid:…`);
- `created_at` and `refreshed_at`.

The ID token is **not** stored, and neither is the account email or name.

**Rules:**

- **Path check:** reuse the Telegram rule. The path must be absolute and outside
  the repository, or the store refuses it with a detail-free error.
- **Permissions:** on Windows the file's access list grants only the current
  user. On POSIX the mode is `0600`. A file readable by others is refused.
- **Atomic write:** write a temporary file in the same folder, flush and fsync
  it, then `os.replace`. A partial file is never left in place.
- **Single refresher:** take a lock file beside the credential file
  (`O_CREAT|O_EXCL`, nonce, owner-only release, as `RagIndexLock` does).
  - The lock covers the whole read → refresh → write sequence.
  - A process that finds the lock taken waits up to 20 s, then rereads the file.
  - A lock older than 60 s counts as stale and may be broken. The refresh
    request itself times out at 15 s.
- **When to refresh:** when the token expires within 120 s, inside
  `access_token()`.
- **Refresh failure:** the store's state becomes `SIGN_IN_REQUIRED`. The file is
  kept, unchanged, for diagnosis, and no refresh is retried until the owner
  signs in again.
- **Sign-out** deletes the file and its lock, and tells the owner to also revoke
  AAOS at chatgpt.com/settings/usage.
- **The store's view of itself** is limited to `SIGNED_IN`, `SIGN_IN_REQUIRED`,
  `PLAN_USAGE_NOT_GRANTED` or `NOT_CONFIGURED`, plus the expiry time. It never
  exposes a token value.

## 5. Sign-in command (11B)

**Commands:**

- `aaos model chatgpt-plan sign-in` runs the flow below.
- `aaos model chatgpt-plan status` prints the store's state, scopes and expiry.
  It contacts no server.
- `aaos model chatgpt-plan sign-out` deletes the credentials, as in §4.

**Flow:**

1. Create a fresh `state`, `nonce` and PKCE verifier (S256) for each attempt.
2. Bind a listener on `127.0.0.1` only (never `0.0.0.0` or `localhost`).
   - It uses the documented callback path, and a free port chosen per attempt
     if OpenAI accepts that (the docs' example is 1455; the probe confirms).
   - It accepts one request on the exact path, then closes.
   - It gives up after 5 minutes.
3. Open the authorize URL in the default browser, and also print it. Parameters:
   - scopes `openid profile email offline_access resource.invoke chatgpt.tokens.use.direct`;
   - `resource=https://api.openai.com/v1`;
   - on the first sign-in, `client_id=dynamic_agent_client`,
     `agent_name_hint=AAOS` and `ext_agent_host_id=<host_id>`;
   - afterwards, the saved issued `client_id`.
4. On the callback, refuse a missing or mismatched `state` with no retry. Then
   exchange the code using the issued `client_id` (never `dynamic_agent_client`),
   the same `redirect_uri`, the verifier and `resource`.
5. Validate before saving anything:
   - the ID token's signature against OpenAI's published keys;
   - its issuer, its audience (the issued `client_id`), its expiry and `nonce`;
   - that the granted scopes include `chatgpt.tokens.use.direct`.
   If plan usage was declined, print a plain message and save nothing.
6. Save through the store (§4), print that the sign-in worked, and print the
   weekly-cap advice (D7).

Errors print safe categories only. Raw bodies from the auth host are never shown.

## 6. Owner-present probe (11C)

- **Script:** `scripts/chatgpt_plan_probe.py`, modelled on `scripts/gemini_probe.py`.
  It runs only by hand, with the owner present, after a real `sign-in`.
- **What it sends:** short synthetic prompts only, never course material.
  - one plain request;
  - one request per structured-output shape AAOS uses: at least
    `grounded_answer`, `requirement_decomposition`, `evidence_spans` and
    `requirement_coverage`;
  - one request with the lowest reasoning effort;
  - one model listing.
- **What it reports,** in safe words:
  - per request, accepted / rejected with category / unreadable;
  - the model names on offer;
  - whether a free callback port was accepted at sign-in;
  - whether `ext_agent_host_id` was required.
- **What happens to the findings:** they are copied into §13 of this spec,
  "Probe findings", before 11D starts. Nothing token-bearing is saved, and any
  rule that a finding changes is noted there.

## 7. `ChatGPTPlanGateway` (11D)

**Request mapping:**

| `ChatRequest` field | What is sent |
|---|---|
| `messages` (system / user / assistant) | Responses API `input` items in order, with system messages as developer messages. The probe may switch the system text to `instructions`. |
| `max_output_tokens` | Not sent, because the preview drops it. AAOS enforces the cap itself (below). |
| `temperature` | Not sent, because the preview drops it. The loss of determinism is accepted and measured (§10). |
| `structured_output="json_schema"` | The strict schema from `_STRUCTURED_RESPONSE_FORMATS`, if the probe shows it is accepted; otherwise reduced, then retried without a schema, as Gemini does. |
| `reasoning=True` | Refused with `UnsupportedInferenceFeatureError`, as Gemini does. The lowest reasoning effort is requested, if accepted. |
| `request_timeout_seconds` | The total time limit for the whole stream. |
| (always) | `stream: true`, `store: false`, no `tools`, no `user`, no `truncation`. |

**Reading the reply:**

- A reply is accepted only after `response.completed`. A stream that is cut
  short raises `InferenceConnectionError`.
- **Output cap:**
  - If `usage.output_tokens` is reported and exceeds `max_output_tokens`, the
    reply is rejected with `InferenceProtocolError`.
  - As a hard stop, the stream is closed once the visible output passes
    `max_output_tokens × 8` UTF-8 bytes (8 bytes allows for Arabic text). That
    also counts as `InferenceProtocolError`.
  - A reply is never truncated and then used.
- The reply is named after the requested model, not the one the server echoes,
  so the semantic steps' identity check compares like with like. Gemini does
  the same.
- **Retry with less, never more,** on HTTP 400:
  1. drop the schema;
  2. then drop the reasoning setting.
  The strict parsers still check every reply.

**Error mapping:**

| Condition | Error | Safe category |
|---|---|---|
| No credentials, or refresh failed | `ModelUnavailableError` | `SIGN_IN_REQUIRED` |
| Scope missing or revoked | `ModelUnavailableError` | `PLAN_USAGE_NOT_GRANTED` |
| `subscription_sharing_usage_limit_exceeded` | `InferenceBackendError` | `PLAN_LIMIT_REACHED` (not retried) |
| HTTP 429 or a short overload | `InferenceBackendError` | waited out as Gemini does, then `RATE_LIMITED` |
| Unsupported setting after all retries | `UnsupportedInferenceFeatureError` | — |
| Timeout, or a stream cut short | `InferenceTimeoutError` / `InferenceConnectionError` | — |
| Over the cap, or unreadable | `InferenceProtocolError` | — |
| Prompt over the local budget | `InferenceContextLimitError` | raised before any network call |

**Availability and telemetry:**

- `check_availability()` lists the connection's models and reports whether the
  configured one is offered.
- Telemetry reuses `LocalInferenceTelemetryEvent`, with safe categories and
  timings only. It never includes tokens, the account email or prompt text.

## 8. Selection, routes and UI (11E)

- **`ModelSelection`** learns `chatgpt_plan`.
  - It is ready when the store is `SIGNED_IN` and a model is set. That check
    makes no network call.
  - Moving to it needs `confirm_cloud`, including from Gemini (D5).
  - A switch is refused while a question is running.
  - The startup log says `CLOUD MODEL ACTIVE` and names the provider.
- **Browser contracts** change only by addition, so the existing fields and
  routes keep their meaning:
  - `ModelProviderView` adds `"chatgpt_plan"`;
  - `ModelStatusResponse` adds `chatgpt_plan_model`, `chatgpt_plan_ready` and
    `chatgpt_plan_blocker`;
  - `ModelSelectRequest` adds `chatgpt_plan_model`.
- **Blockers:** `CHATGPT_PLAN_SIGN_IN_REQUIRED`, `CHATGPT_PLAN_USAGE_NOT_GRANTED`
  and `CHATGPT_PLAN_MODEL_MISSING`.
- **New route:** `GET /model/chatgpt-plan-models`, which returns names only. It
  is added to the Phase 7 route inventory and scope audit, and no sign-in route
  is added (D1).
- **Model badge:**
  - The label reads `Cloud · ChatGPT plan · <model>`.
  - The panel offers the option with the cloud warning.
  - When the owner isn't signed in, it says to run
    `aaos model chatgpt-plan sign-in` in a terminal.
  - The page never asks for or shows a token.
- **Schema files:** `frontend/openapi/aaos-v1.json` and
  `frontend/types/api.generated.ts` are regenerated through
  `scripts/export_openapi.py`.

## 9. RAG wiring (11D–11E)

The same as the Gemini composition in `src/aaos/rag/factory.py`:

- a `NoOpModelLifecycle`, since there is no local model to load;
- a summary cache keyed by the active model identifier;
- the three semantic steps built with `approved_identifiers` set to exactly the
  active ChatGPT-plan model name.

The Qwen-only default and refusal of any other name are unchanged. This extends
the owner-approved Phase 4 amendment recorded for Gemini.

## 10. Evaluation (11F)

- **Which evaluation:** the frozen Phase I4 manifest
  (`tests/evaluation/phase_i4_arabic_retrieval_cases_v1.json`), in the `answers`
  mode of `scripts/phase_i4/arabic_retrieval_eval.py`. That runs the complete
  production RAG path on the real index, in English and Arabic.
  - Not the A–H frozen set: that set is synthetic and fake-provider-only by its
    freeze rule, so it can't measure a live model. It stays a regression check
    in §11.
- **When:** with the owner present, after 11E. It runs at least three times with
  `AAOS_MODEL_PROVIDER=chatgpt_plan`, and the runner is unchanged.
- **Data:** this sends real course excerpts to OpenAI, as normal use would. The
  owner gives that consent at the time of the run.
- **What is recorded:**
  - per run, how many answerable rows cite the expected page;
  - how many unanswerable rows are correctly refused;
  - which cases flip between runs, because `temperature` can't be pinned;
  - the local model's figures, as the comparison: 80 of 96 answerable rows cite
    the expected page over 3 runs, per `docs/evaluations/rag-coverage-flips-2026-10-02.md`.
- **Where:** `docs/evaluations/`, with no tokens, account data or raw course text.
- **What it decides:** nothing automatically. The evidence is for the owner, and
  the default model does not change.

## 11. Tests (written first, per slice)

All automated tests use fake transports. No real OpenAI call runs in the suite.

- **11A Configuration and store:**
  - provider validation and environment parsing;
  - the store refuses a path inside the repository;
  - a file readable by others is refused;
  - the write is atomic (interrupted between the temporary write and `replace`);
  - two processes don't refresh at once: one refreshes, the other rereads;
  - a stale lock can be broken;
  - a refresh failure leads to `SIGN_IN_REQUIRED`;
  - the single-use refresh token is persisted on every refresh;
  - `repr`, `str` and errors contain no token.
- **11B Sign-in:**
  - fresh `state`, `nonce` and verifier per attempt;
  - a `state` mismatch is refused;
  - the listener binds `127.0.0.1` only and handles one request;
  - the timeout ends the attempt;
  - the exchange uses the issued `client_id`;
  - an ID-token signature, issuer, audience, expiry or `nonce` failure is refused;
  - a missing plan scope saves nothing;
  - the CLI output contains no token.
- **11C Probe:** the script's own unit tests, with a fake transport, as
  `tests/test_gemini_probe.py` does: safe-word reporting and no token in output.
- **11D Gateway:**
  - the request shape: no tools, `store=false`, streamed;
  - stream assembly up to `response.completed`;
  - a stream cut short;
  - both output-cap paths;
  - retry with less never sends more;
  - each row of the §7 error mapping;
  - reply naming;
  - a hostile backend can't make the token appear in an error.
- **11E Selection and UI:**
  - `confirm_cloud` is needed from local and from Gemini;
  - a switch is refused while a question runs;
  - each blocker;
  - the contracts are additive;
  - the Phase 7 route inventory and scope audit include the new route;
  - model-badge frontend tests.
- **Secret scan:** `scripts/phase9/public_release.py` also flags `id_token` and
  JWT-shaped strings. The public-release, log and browser-payload scans cover
  the credential file name.
- **Regression:**
  - the full backend suite with Docker running;
  - the Gemini and notification tests;
  - the A–H selectors;
  - ruff, mypy and the frontend tests.

## 12. Out of scope

- The Codex CLI, `codex exec`, Codex app-server, or reuse of the Codex CLI's login.
- Model-side tools, web search or an agent loop.
- Sign-in from the browser (D1).
- Hosted or multi-user use, and paid distribution.
- Changing the default model.
- Other providers, or API-key OpenAI access.
- Images or vision through this provider.

## 13. Probe findings

To be filled in by 11C before 11D starts. Empty in this draft.

## Acceptance gate

As listed under Phase 11 in `docs/PHASES.md`, plus:

- the 11A–11E tests pass;
- the §10 evaluation is recorded;
- §13 is filled in.
