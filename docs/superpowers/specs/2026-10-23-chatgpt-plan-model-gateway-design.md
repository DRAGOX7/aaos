# ChatGPT-plan model gateway (Sign in with ChatGPT) — design note

**Status:** PROPOSAL / NOT AUTHORIZED. Design only; no code. Nothing here may be
implemented until the owner authorizes it as a phase (see §9).

**Date:** 2026-10-23

## 1. Summary

Add a third, opt-in `ModelGateway` implementation, `ChatGPTPlanGateway`. It
answers AAOS model requests with OpenAI models paid for by the owner's ChatGPT
Plus/Pro plan, using OpenAI's official **Sign in with ChatGPT** plan-usage flow.

It slots into the same boundary as `GeminiGateway`. Retrieval, embeddings,
prompt construction, coverage checks, entailment checks, approvals and
evaluation stay unchanged. Only the step that turns a prepared `ChatRequest`
into text moves.

This is **not** a wrapper around the Codex CLI. A 2026-10-23 experiment showed
why. A locked-down `codex exec` still tried two shell commands, and both were
blocked by its sandbox. Those attempts appeared only on stderr. It started an
outside MCP connection despite `--ignore-user-config`. It also used about 72k
input tokens for a one-line prompt. Sign in with ChatGPT avoids all four,
because it is a plain model call.

## 2. Goals and non-goals

**Goals**

- Use the owner's ChatGPT plan instead of a pay-per-token key, within OpenAI's
  published terms for local personal and open-source apps.
- Keep the `ModelGateway` contract (`check_availability`, `generate`) unchanged.
- Meet the AGENTS.md credential rules: tokens never reach Git, normal logs,
  the frontend or model context.
- Give the cloud path the same warnings and confirmation as Gemini.

**Non-goals**

- Codex CLI, `codex exec`, Codex app-server, or reuse of the Codex CLI's
  saved login.
- Any model-side tools, web search or agent loop. The gateway sends no tools.
- Hosted or multi-user deployment. That needs OpenAI's approval and is out of scope.
- Changing the local model as the default.

## 3. Facts this design rests on

Facts from the code (verified 2026-10-23):

- `ModelGateway` has only `check_availability()` and `generate(ChatRequest)`
  (`src/aaos/inference/gateway.py`).
- `ChatRequest` carries `max_output_tokens`, `reasoning`, `structured_output`
  plus `structured_output_schema`, `temperature`, and `request_timeout_seconds`
  (`src/aaos/inference/models.py`).
- RAG and agent callers depend on two settings:
  - `temperature=0.0`, in all `semantic_coverage.py` classifiers, `proposal.py`,
    `academic_orchestrator.py` and `study_planning.py`;
  - `max_output_tokens`. Generation defaults to 2048 and is capped at 4096
    (`src/aaos/rag/generation.py:43`, `:98-107`).
- `GeminiGateway` sets the pattern to copy:
  - a fixed HTTPS base URL, `follow_redirects=False`, `trust_env=False`;
  - the secret held in one private attribute and header, absent from `repr`, telemetry and errors;
  - a local context-budget check;
  - "retry with less, never more" on HTTP 400;
  - strict local validation of every reply.
- Provider selection is closed: `MODEL_PROVIDERS = ("lm_studio", "gemini")`
  (`src/aaos/config.py:16`). Runtime switching asks for cloud confirmation
  (`src/aaos/inference/selection.py`).
- There is precedent for storing credentials:
  - Telegram credentials are JSON loaded through `_checked_path()`, which rejects
    relative paths and any path inside the repository (`src/aaos/notifications/telegram.py`).
    Errors are deliberately detail-free (`TelegramUnavailable`).
  - The existing save is a plain `write_text`, with no atomic replace and no
    permission tightening.
- There is precedent for single-writer locking: `RagIndexLock` uses
  `O_CREAT|O_EXCL` with a nonce (`src/aaos/rag/lock.py`).
- Dependencies include `httpx2` but no JWT/JOSE library and no `pydantic-ai`.

Facts from OpenAI's published docs (read 2026-10-23; the feature is a preview and
may change):

- Who can use it: "ChatGPT plan usage is available to open-source projects,
  personal projects that run locally, and selected private apps." Paid or
  remotely hosted apps must join a waitlist.
- Sign-in: OpenID Connect authorization code flow with PKCE (S256).
  - Redirect to a loopback listener on `127.0.0.1`; the docs example uses
    `http://127.0.0.1:1455/auth/callback`. Never `localhost`.
  - Endpoints: `https://auth.openai.com/api/accounts/authorize` and
    `https://auth.openai.com/api/accounts/oauth/token`.
  - Scopes: `openid profile email offline_access resource.invoke chatgpt.tokens.use.direct`,
    with `resource=https://api.openai.com/v1`.
- Client registration is dynamic. There is no pre-registered client ID and no secret.
  - The first sign-in sends `client_id=dynamic_agent_client`, `agent_name_hint`,
    and a stable per-install `ext_agent_host_id` (for example `urn:uuid:…`).
  - The callback returns an issued `client_id`, which must be saved and reused.
- Validating the result:
  - Check the ID token's signature (JWKS), issuer, audience, expiry and nonce.
  - A valid ID token alone does not grant plan usage. The granted scopes must
    include `chatgpt.tokens.use.direct`.
- Refresh tokens are **single-use**: every refresh must persist the new pair.
- Model calls go to the public Responses API at `https://api.openai.com/v1`,
  with the access token as the bearer.
  - Requests use `stream: true`, `store: false`, the app's own `instructions`,
    and no built-in agent prompt.
  - The available models come from a model listing for the connection.
- Preview limits, as documented for the pydantic-ai provider:
  - `store=false` is forced;
  - `max_tokens`, `service_tier`, `temperature` and `top_p` are dropped;
  - `truncation` and `user` are rejected;
  - prompt caching has no effect, and token counting is unavailable.
- When the plan limit is hit, requests fail with `subscription_sharing_usage_limit_exceeded`.
  OpenAI never moves the request to another billing method. Users set per-app
  weekly caps at chatgpt.com/settings/usage.

## 4. Architecture

```
AAOS harness (unchanged: retrieval, prompts, coverage, entailment, approvals)
 └─ ModelGateway.generate(ChatRequest)
     ├─ LmStudioGateway        (default, local)
     ├─ GeminiGateway          (opt-in cloud, API key from environment)
     └─ ChatGPTPlanGateway     (opt-in cloud, Sign in with ChatGPT)  ← new
          ├─ ChatGPTCredentialStore   (load / save / refresh, single writer)
          └─ fixed-URL httpx2 client → https://api.openai.com/v1/responses (streamed)
     Sign-in (separate, owner-initiated)
          └─ ChatGPTSignInFlow  (PKCE, one-shot loopback listener, validation)
```

New pieces and their duties:

- **`ChatGPTSignInFlow`** runs only when the owner starts it. It never runs from
  `generate()`, a scheduled job or the model.
- **`ChatGPTCredentialStore`** is the only code that reads or writes the
  credential file, and the only code that refreshes tokens.
- **`ChatGPTPlanGateway`** gets an access token from the store per request and
  never sees the refresh token.
- **Config** adds `"chatgpt_plan"` to `MODEL_PROVIDERS`, plus a
  `chatgpt_plan_model` setting validated like `gemini_model`.
- **Selection:** `selection.py` treats `chatgpt_plan` as cloud. Moving to it needs
  `confirm_cloud`, the startup log says `CLOUD MODEL ACTIVE`, and the badge reads
  `Cloud · ChatGPT plan · <model>`.

Implementation choice, to settle in the spec:

| Option | For | Against |
|---|---|---|
| Hand-roll it on `httpx2`, plus one vetted JOSE library for ID-token checks | Matches how Gemini was built: small, closed contracts, no new model framework | AAOS owns security-sensitive OAuth code |
| Use pydantic-ai's `openai-chatgpt` provider only for sign-in and refresh | Maintained OAuth code, refresh handled | Large new dependency tree, and its credential-source interface must still meet §5 |

Recommendation: hand-roll the HTTP and use a well-known JOSE library. The
credential rules in §5 must be enforced by AAOS code either way, and a model
framework would sit beside `ModelGateway` rather than behind it.

## 5. OAuth and token storage under the AAOS security rules

### 5.1 What counts as a secret

The access token, the refresh token, the ID token, the PKCE verifier, `state`
and `nonce` are secrets. The issued `client_id`, the `ext_agent_host_id`, the
granted scopes and the expiry are not secret, but they live in the same file.

### 5.2 Sign-in flow invariants

1. Only the owner starts it, from the CLI (for example `aaos model chatgpt
   login`) or a UI button that calls a backend route.
   - The frontend only opens the OpenAI URL the backend returns.
   - It never receives a code, a token or a verifier.
2. The loopback listener follows strict rules:
   - it binds `127.0.0.1` only, never `0.0.0.0`;
   - it accepts exactly one callback on the exact path, then closes;
   - it gives up after a short timeout, such as 5 minutes.
3. Each attempt uses a fresh `state`, `nonce` and PKCE verifier. A mismatched or
   missing `state` is a hard failure with no retry.
4. The token exchange uses the issued `client_id` and never `dynamic_agent_client`.
   It sends the same `redirect_uri` and the `resource` parameter.
5. Before storing anything, the flow validates the ID token: signature through
   JWKS, issuer, audience equal to the issued `client_id`, expiry and nonce. It
   also requires `chatgpt.tokens.use.direct` in the granted scopes.
   - If the owner declined plan usage, the sign-in is rejected with a plain message.
   - Nothing is stored in that case.
6. The auth and API hosts are code constants, never settings.
   - Redirects are not followed, and proxies from the environment are ignored
     (`trust_env=False`), the same as Gemini.

### 5.3 Where the file lives

- The file is outside the repository, for example
  `%LOCALAPPDATA%\AAOS\credentials\chatgpt-plan.json`. An environment variable
  can override the location.
- It reuses the Telegram `_checked_path()` rule: the path must be absolute and
  must not be inside the repository. This is stricter than relying on the
  `.gitignore` entries for `data/credentials/`, which also stay in place.
- On Windows, the file's access list allows only the current user. On POSIX it is `0600`.
- One file holds one account. Signing out deletes the file. The owner can also
  revoke the app at chatgpt.com/settings/usage.

### 5.4 Writing and refreshing

Refresh tokens are single-use, so a lost write signs the owner out. Three AAOS
processes can be alive at once (the backend, the CLI and `aaos monitor run-once`).

- **Atomic write:** write a temporary file in the same directory, flush and
  fsync it, then use `os.replace`. Never leave a partial file in place.
- **Single refresher:** use a lock file with the same pattern as `RagIndexLock`
  (`O_CREAT|O_EXCL`, nonce, owner-only release) around the whole
  read → refresh → write sequence.
  - A process that finds the lock taken waits briefly, then rereads the file. It
    never refreshes in parallel.
- **Refresh failure:** move to a `SIGN_IN_REQUIRED` state and keep the old file
  for diagnosis. AAOS stays on that state until the owner signs in again; it does
  not retry in a loop.
- **When to refresh:** shortly before expiry, inside the store. The gateway only
  ever asks the store for a valid access token.

### 5.5 Where secrets must never appear

- **Never in `repr` or `str`:** use `field(repr=False)` as `TelegramCredentials` does.
- **Never:** log lines, telemetry events, `InferenceError` messages, API
  responses, the browser, model prompts, the evaluation output or Git.
- **Errors:** detail-free like `TelegramUnavailable`. Safe categories such as
  `SIGN_IN_REQUIRED`, `PLAN_USAGE_NOT_GRANTED`, `PLAN_LIMIT_REACHED` and
  `CONNECTION` are allowed; raw response bodies from the auth host are not.
- **Tests:** the existing secret scans (tree, logs, browser) must cover the new
  file name and token shapes.

### 5.6 Data leaving the machine

The same boundary as Gemini: the question text and the chosen course excerpts
go to OpenAI, with `store=false`. No Moodle credentials, cookies, file paths or
other tokens are ever included.

## 6. Preview limits that affect AAOS

| Preview behaviour | AAOS dependency | Effect | Mitigation in the gateway |
|---|---|---|---|
| `max_tokens` dropped | `max_output_tokens` (generation 2048, capped at 4096; classifiers bounded) | The model can write past AAOS's budget. That costs plan usage and latency, and can produce over-long JSON. | Keep the local prompt-budget check. Read the stream with a hard output cap and stop after it. Treat an over-cap or incomplete reply as `InferenceProtocolError`, then let the existing strict parsers decide. |
| `temperature` dropped | `temperature=0.0` in every classifier and decision step | Coverage, decomposition, entailment and decision outputs are no longer deterministic. The same question can flip between answered and refused (compare `docs/evaluations/rag-coverage-flips-2026-10-02.md`). | Can't be fixed in the gateway. State it in the UI and docs. The acceptance gate needs repeated-run stability on the frozen evaluation set (§8), not a single run. |
| Prompt caching has no effect | Several model calls per question share long system prompts | Each call spends full plan usage, and weekly caps arrive sooner. | Keep prompts lean. Optionally pace requests the way `AAOS_GEMINI_MIN_INTERVAL_SECONDS` does. |
| Token counting unavailable | AAOS already estimates locally (`_estimate_prompt_tokens`) | None | Keep the local estimate. |
| `store=false` forced | AAOS wants no server-side history | Good for privacy | None |
| Streaming required | Gemini and LM Studio use non-streamed replies | The gateway must assemble a stream and detect when it is cut short | Accept a reply only after `response.completed`. A dropped stream raises `InferenceConnectionError`. |
| Structured output in preview: **unknown** | `json_schema` for ~27 schemas | It may be rejected or loosened | Reuse Gemini's "retry with less" ladder: schema → no schema. AAOS validates every reply itself, so loosening weakens nothing. |
| `reasoning` / reasoning effort: **unknown** | `reasoning=True` is rejected by Gemini today | Hidden reasoning may spend plan usage | Reject `reasoning=True` as Gemini does. Request the lowest reasoning effort if accepted, with a retry-with-less fallback. |
| Plan limit reached | None | Hard failure, never billed elsewhere | Map `subscription_sharing_usage_limit_exceeded` to a safe `PLAN_LIMIT_REACHED` category, and show it the way Gemini rate limits are shown. Do not retry automatically. |
| Models are per account | `check_availability` | The configured model may not be offered | List the models for the connection, the same as Gemini's `check_availability`, and offer them in the model panel. |

## 7. Error mapping

| Condition | AAOS error |
|---|---|
| No credential file, or refresh failed | `ModelUnavailableError` (category `SIGN_IN_REQUIRED`) |
| Scope `chatgpt.tokens.use.direct` missing or revoked | `ModelUnavailableError` (category `PLAN_USAGE_NOT_GRANTED`) |
| `subscription_sharing_usage_limit_exceeded` | `InferenceBackendError` (category `PLAN_LIMIT_REACHED`) |
| HTTP 400 for an unsupported setting | Retry with less, then `UnsupportedInferenceFeatureError` |
| Timeout or a stream cut short | `InferenceTimeoutError` / `InferenceConnectionError` |
| Unreadable or over-cap reply | `InferenceProtocolError` |
| Prompt over the local budget | `InferenceContextLimitError`, raised before any network call |

## 8. Verification and proposed acceptance gate

Automated, with fake transports only and no real OpenAI calls in tests:

- **Sign-in flow:**
  - PKCE, state and nonce checks;
  - the listener binds `127.0.0.1` only and handles one shot;
  - the timeout ends the attempt;
  - a missing scope is rejected;
  - an ID-token audience, issuer or expiry mismatch is rejected.
- **Credential store:**
  - a path inside the repository is rejected;
  - the write is atomic (kill-between-steps test);
  - two processes don't refresh at the same time;
  - a refresh failure leads to `SIGN_IN_REQUIRED`;
  - `repr`, `str` and errors contain no secrets.
- **Gateway:**
  - the stream assembly reaches `response.completed`;
  - the output cap is enforced;
  - retry with less never sends more;
  - each row of the §7 mapping;
  - no tools are ever sent.
- **Selection:**
  - moving to `chatgpt_plan` requires `confirm_cloud`;
  - the startup log shows `CLOUD MODEL ACTIVE`;
  - the badge text is correct.
- **Secret scans:** tree, logs and browser payloads include the new credential shapes.

Manual, with the owner present:

- One sign-in, one question, one sign-out, then revocation in ChatGPT settings.
- A comparison against the local model on the frozen evaluation set
  (`docs/evaluations/agentic-aaos-ah-frozen-evaluation-v1.md`), with **at least
  three repeated runs**, because temperature can't be pinned.
- The result is evidence for the owner, not an automatic switch of the default.

Proposed gate:

- Tokens never appear in Git, logs, the frontend, model context or evaluation output.
- No tools or agent features are ever requested.
- The local model remains the default. The cloud path needs confirmation.
- Plan-limit and sign-out states fail clearly and never fall back to paid billing.
- All earlier gates still pass.

## 9. Where this fits in PHASES.md

Current state (`docs/PHASES.md`, `docs/GOVERNANCE_LEDGER.md`):

- Phase 10 is **IMPLEMENTED / VERIFIED; HUMAN ACCEPTANCE PENDING**, with no freeze tag.
- Phase 0 acceptance (0G/0H) and Phase 5 Slice 6 are explicitly deferred.

Placement:

- This is new provider functionality. Under "a phase may not begin until every
  blocking acceptance criterion from the previous phase passes", it **cannot
  start before Phase 10 is human-accepted** (ideally tagged `phase-10-passed`).
- It does not belong inside an existing phase. Phases 0–9 are frozen or have
  their own open gates, and amending a frozen phase would blur its evidence.
- Proposed home: **Phase 11 — Optional subscription-backed cloud model (ChatGPT
  plan)**. It needs its own owner authorization in the ledger, a spec derived
  from this note, a plan, and the gate in §8.
  - If the owner wants it, the authorization can again explicitly defer the
    Phase 0 and Phase 5 gates, as the Phase 10 authorization did.

**Governance gap to settle first.** The optional Gemini provider is merged on
`main`: commits `e2bbb794` through `0e1d6749`, `MODEL_PROVIDERS` in `config.py`,
the README section and the model panel. Yet neither `PHASES.md` nor
`GOVERNANCE_LEDGER.md` records it. Phase 11 would build directly on that cloud
selection path. The ledger should first record Gemini's status (what was
authorized, what was verified), so that Phase 11 builds on documented ground.

## 10. Open questions for the spec

1. Does the preview Responses API accept `text.format` JSON-schema structured
   output and a reasoning-effort setting for the models on the owner's plan?
   Settle this with one owner-present probe, like `scripts/gemini_probe.py`.
2. Is `ext_agent_host_id` required or optional? OpenAI's pages say required;
   pydantic-ai treats it as optional. Assume required.
3. Port 1455 is the docs' example and may clash with other local sign-in tools
   (the Codex CLI's login also uses a loopback port). Pick a free port per
   attempt if the issued client allows it; OpenAI's docs say later sign-ins may
   use other ports.
4. Should one sign-in serve the backend, CLI and monitor? This note assumes one
   shared credential file and the single-refresher lock.
5. AAOS is published openly (the Phase 9 public release). Confirm that other
   people running their own local copies falls under OpenAI's "open-source
   projects" eligibility, and add a line to the README.

## 11. Recommended follow-up (outside this design)

- Record the Gemini provider in `GOVERNANCE_LEDGER.md` (see §9).
- Observed, not verified: `notifications/cli.py` defaults the Telegram
  credential path to `<cwd>/data/credentials/telegram.json`, but
  `telegram.py:_checked_path()` rejects any path inside the repository. When run
  from the repository root, the default would always be refused unless
  `AAOS_TELEGRAM_CREDENTIAL_PATH` is set. Worth a quick check.
- The Telegram credential save is not atomic and does not restrict file
  permissions. The §5.4 store could become a shared helper for both.

## Sources (OpenAI, read 2026-10-23)

- https://developers.openai.com/siwc/quickstart
- https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt
- https://developers.openai.com/siwc/token-sharing-open-source
- https://developers.openai.com/siwc/token-sharing-open-source/sign-in
- https://developers.openai.com/siwc/request-client-id
- https://help.openai.com/en/articles/20001542
- https://pydantic.dev/docs/ai/models/openai-chatgpt/
