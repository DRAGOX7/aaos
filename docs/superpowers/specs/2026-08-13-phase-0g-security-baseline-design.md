# Phase 0G Security Baseline Verification Design

## 1. Purpose

Phase 0G answers one bounded question:

> Does the current Phase 0 system demonstrably satisfy the security properties
> AAOS already claims?

This is a verification gate, not a security-feature phase. It may add focused
regression tests, static repository checks, and an evidence report, but it must
not add a new runtime security subsystem, change public behavior, or pull a
later phase forward. Phase 0H remains the separate final integration and human
acceptance gate.

## 2. Threat and scope boundary

The applicable Phase 0 system consists of the local configuration boundary,
FastAPI health foundation, SQLite foundation, the LM Studio gateway, and the
read-only `aaos doctor` diagnostics. Inputs crossing those boundaries are
untrusted: model output, backend payloads, subprocess output, tool results, and
environment observations.

0G verifies that trusted application code validates those inputs, exposes only
fixed safe results, and does not perform hidden work. It does not attempt to
sandbox hostile code, defend against a compromised operating system, or add
credential management.

The following remain owned by later phases and are excluded from 0G:

- Moodle authentication, browser sessions, cookies, and authentication-state
  storage;
- academic-file ingestion, document permissions, and RAG prompt-injection
  defenses;
- agent tools, approval workflows, external writes, Calendar/GitHub/MCP
  secrets, and dashboard authentication;
- restricted REPL isolation and multimodal/vision security.

## 3. Existing security claims to reconcile

The source requirements are `AGENTS.md`, `docs/DEVELOPMENT_RULES.md`,
`docs/PROJECT_SPEC.md`, `docs/ARCHITECTURE.md`, `docs/PHASES.md`, the accepted
Phase 0E design/evidence, and the accepted Phase 0F design/evidence. They
already claim that:

1. credentials, tokens, cookies, authorization headers, Moodle state, and
   secrets are not committed, logged, exposed to the frontend, or placed in
   model-visible context;
2. sensitive academic data remains local and the model has no unrestricted
   access to accounts, storage, or the host shell;
3. Phase 0 defaults are local-first and non-secret;
4. importing application modules performs no network, subprocess, model
   lifecycle, file, database, GPU, or secret-reading side effect;
5. errors and normal diagnostic output expose fixed safe information rather
   than raw prompts, response bodies, headers, environment values, stderr, or
   native exception text;
6. model, tool, subprocess, and environment observations are untrusted and
   must be schema-validated or explicitly parsed before becoming AAOS facts;
7. execution is deterministic and trusted code controls it: the doctor uses
   only module-owned command specifications, and the gateway owns a dedicated
   transport without hidden availability or lifecycle actions;
8. failures and approved reader behavior are explicit; no silent fallback or
   remediation bypasses a validation or security boundary;
9. dependencies and lock data are reproducible; and
10. the Phase 0F subprocess boundary uses fixed executable/argument identity,
    `shell=False`, `stdin=DEVNULL`, `stderr=DEVNULL`, a sanitized environment,
    a five-second timeout, strict decoding, and typed safe outcomes.

Claims about private academic data are architectural boundaries with no Phase
0 academic-data implementation behind them. They are recorded as later-owned,
not treated as evidence that future protections already exist.

## 4. Verification matrix

The implementation should use this matrix as the authoritative 0G checklist.
Accepted 0E/0F reports may be reused for provenance, previously approved
architecture decisions, and historical acceptance evidence. They do not
replace current verification: deterministic correctness and security gates
must be freshly rerun against the current `main` HEAD. Old architecture
experiments and model benchmarks must not be rerun merely to recreate
historical evidence.

| Property | Source requirement | Existing implementation/evidence | 0G verification | PASS / FAIL condition | Automated test vs static/manual evidence |
|---|---|---|---|---|---|
| Secret and auth-state Git hygiene | `AGENTS.md`; Phase 0 gate | `.gitignore` protects `data/auth/` and `data/credentials/`; accepted 0E/0F reports recorded no tracked paths | Run `git ls-files -- data/auth data/credentials`, inspect tracked `.env`-style credential/config files where prohibited, inspect explicitly documented credential/auth-state locations, and perform a focused static review for obvious violations of existing governance | PASS only when no protected auth/credential paths or obvious prohibited tracked files are found; FAIL on any tracked protected path or obvious governance violation. This is not an exhaustive scan for arbitrary secrets in all source content. | Focused static repository check; do not expose secret contents; no new scanner or dependency; no runtime test needed |
| Safe error and output boundaries | `AGENTS.md`; development security rules; 0E/0F specs | Gateway translation tests reject prompt/body/header/cookie/token leakage; doctor command and renderer tests reject raw stderr/output and terminal controls | Re-run focused redaction tests and inspect all user-facing Phase 0 output paths for raw native data | PASS when fixed messages/facts are exposed and sentinel secrets are absent; FAIL on raw sensitive or untrusted payload exposure | Existing regression tests plus narrow static review; manual evidence only for the final report |
| Local-first configuration and network boundary | Project spec and architecture | `AppSettings` accepts loopback HTTP only; gateway uses `follow_redirects=False` and `trust_env=False`; no credentials or remote defaults | Test accepted loopback defaults/rejections and inspect construction for remote, proxy, or credential behavior | PASS when defaults remain local and non-secret and no unapproved remote path is reachable; FAIL on a remote/credential-bearing default or implicit proxy use | Existing config/gateway tests; static review |
| Import and startup side-effect safety | 0E/0F specs; development rules | `tests/test_inference_imports.py` and `tests/test_doctor_imports.py` probe network/client/subprocess construction and file/database effects | Run both isolated import suites and inspect module top levels for new side effects | PASS when imports complete without forbidden calls or state creation; FAIL on any forbidden import-time effect | Existing automated tests are authoritative; static review confirms scope |
| Untrusted model/backend validation | Project spec; 0E design | LM Studio parser validates native inventory, loaded contexts, roles, visible content, reasoning/tool metadata, usage counts, and safe error translation | Re-run gateway malformed-payload, exact-model, no-preflight, and redaction tests | PASS when malformed, reasoning, tool, unavailable, and backend-error cases fail with typed safe errors while valid payloads return only model-independent values; FAIL on acceptance of invalid data or raw payload exposure | Existing regression suite; no duplicate test unless a listed invariant lacks coverage |
| Untrusted subprocess/environment validation | 0F design and accepted evidence | Doctor models enforce typed facts; system/NVIDIA parsers validate values; command runner has canonical specs, fixed args, `shell=False`, `DEVNULL`, bounded accepted output, strict UTF-8, and typed mappings | Re-run doctor model/system/NVIDIA/command tests and inspect the exact command kwargs and allowlist | PASS when only canonical commands execute and malformed, oversized, denied, timeout, nonzero, and invalid-encoding results become safe typed observations; FAIL on arbitrary command acceptance, raw output/error exposure, or hidden execution | Existing automated tests plus static command-boundary review |
| Deterministic execution and lifecycle | Architecture; 0E/0F designs | Gateway has one injected dedicated transport, idempotent `aclose`; doctor is read-only, fixed-order, and closes the gateway in `finally` | Re-run lifecycle/order tests and inspect for retries, model lifecycle actions, external writes, or hidden generation | PASS when operations are explicit, bounded, read-only, and cleanup is deterministic; FAIL on implicit retries/remediation, generation from doctor, external writes, or leaked lifecycle ownership | Existing tests plus static review |
| No hidden fallback behavior | Project spec principle 9; 0F reader policy | System facts use the frozen reader sequence; command/model failures use explicit diagnostic codes; service preserves WARN/FAIL policy | Compare implementation readers against the approved 0F sequence and exercise unavailable/unsupported paths | PASS when each mechanism and fallback is documented and typed, with no silent bypass; FAIL on an invented reader, swallowed failure, or unreported remediation | Static source review plus existing failure-path tests |
| Dependency and lock reproducibility | Development rules; Phase 0 gate | `pyproject.toml` declares runtime dependencies and explicit `uv_build`; `uv.lock` is committed; accepted reports show locked sync | Run `uv lock --check`, `uv sync --locked --all-groups`, and inspect dependency diff/status | PASS when lock and environment resolve without mutation and no unapproved dependency is introduced; FAIL on drift, unlocked install, or unexplained dependency change | Deterministic commands and static lock review |
| Private academic-data boundary | Project spec and architecture | No Moodle, document, RAG, or academic-data implementation exists in Phase 0; architecture says such data remains local and outside model-visible context | Confirm no Phase 0 code reads academic stores, credentials, or browser profiles and record this as a deferred ownership boundary | PASS for 0G only when the boundary is preserved and no Phase 0 path exposes such data; FAIL if Phase 0 introduces an access path or claims later-phase protection as implemented | Static scope review; no artificial runtime test |

## 5. Identified verification gaps

The reconciliation found no authorized need for new production security code. The
gaps are evidence gaps:

- the protected-path and tracked-secret checks must be rerun and recorded for
  the 0G acceptance report rather than relying only on historical 0E/0F text;
- the complete Phase 0 user-visible output surface should receive one focused
  static review tied to the existing redaction tests;
- the accepted 0E/0F deterministic commands should be rerun or their exact
  reports referenced, with any environmental limitation recorded;
- the approved 0F reader sequence and fixed subprocess contract need an
  explicit checklist result so a later implementation change cannot be hidden
  behind a generic “doctor passed” statement.

These are verification and evidence tasks. They do not justify new logging,
secret scanners with broad policy, sandboxing, credential stores, or runtime
security middleware. Historical accepted evidence remains provenance and
context; current deterministic gates must be freshly rerun against `main`.

## 6. Proposed tests, checks, and evidence

0G implementation should add only the smallest missing regression coverage after
confirming existing tests do not already prove the invariant. Candidate work is:

- a focused repository-hygiene check/report for tracked `data/auth/`,
  `data/credentials/`, prohibited `.env`-style files, and explicitly
  documented credential/auth-state locations, with no secret contents copied
  into fixtures or output. This is not a new exhaustive secret scanner;
- a focused assertion that every Phase 0 CLI/report path emits only allowlisted
  facts and fixed safe diagnostics, reusing existing gateway and doctor tests;
- a static checklist test or review artifact for the frozen 0F reader sequence,
  command identity, environment allowlist, and no-generation doctor path;
- deterministic reruns of the existing Phase 0E/0F suites, lock checks, import
  probes, Ruff, mypy, and whitespace checks;
- a signed-off 0G evidence report containing command results, commit/status
  identity, reused 0E/0F evidence references, fresh checks, and limitations.

No test should contact Moodle, access browser profiles, require academic files,
send a real external write, start or reconfigure LM Studio, or depend on a
future agent/approval/REPL subsystem.

## 7. Explicit exclusions

0G must not implement or verify as if already present:

- Moodle login/session security or browser credential storage;
- ingestion/document authorization, RAG prompt-injection handling, or source
  privacy policy;
- agent tool permissions, human approval engine behavior, Google/GitHub/MCP
  secrets, dashboard authentication, or external-write security;
- restricted REPL sandboxing, process containment beyond the accepted 0F
  trusted-command threat model, or multimodal/vision security;
- new encryption, secret-management, audit-log, policy-engine, firewall, or
  sandbox subsystems;
- Phase 0H whole-phase acceptance or the `phase-00-passed` tag.

## 8. Acceptance criteria

Phase 0G may be proposed as passed only when a human can inspect one complete
evidence report showing:

1. every applicable Phase 0 security requirement in the matrix has a PASS;
   a genuinely non-applicable or future-owned property has an explicit
   ownership classification. A documented limitation is not a pass and blocks
   0G unless a human explicitly accepts that specific limitation;
2. deterministic Phase 0E/0F tests, import probes, Ruff, strict mypy, lock
   checks, and whitespace checks have been freshly rerun against the current
   `main` HEAD and pass;
3. the focused repository-hygiene check finds no tracked protected credential
   or auth-state paths and no obvious prohibited tracked files, without
   claiming exhaustive arbitrary-secret detection;
4. the local-first configuration, safe error/output, untrusted-input parsing,
   fixed subprocess boundary, lifecycle, and no-hidden-fallback claims are
   supported by current code/tests/static evidence, with no unresolved
   applicable Phase 0 evidence gap. Later-phase-owned properties are excluded,
   not treated as evidence gaps;
5. no Phase 0G change adds runtime functionality, a dependency, a later-phase
   security subsystem, an external write, or hidden environment mutation; and
6. the report records exact commands, commit/status identity, reused 0E/0F
   evidence, fresh 0G checks, failures/limitations, and a recommended next
   action.

This gate does not pass Phase 0. After 0G evidence is complete, work stops for
the separate 0H design/process gate and explicit human decision.

## 9. Proposed implementation files

The preferred implementation is documentation/evidence plus narrowly scoped
tests only where the matrix proves an actual gap:

- `tests/test_phase0g_security.py` only if existing tests cannot express a
  missing deterministic invariant;
- an ignored or governed Phase 0G evidence report under the repository's SDD
  evidence location;
- this specification may be updated only if the approved design reveals a
  contradiction before implementation.

No production module, runtime dependency, public CLI route, database schema,
credential store, or later-phase file is proposed.

## Status

This document is a design/specification for human review. It does not authorize
implementation, tests, dependency changes, Phase 0H, or the
`phase-00-passed` tag.
