# Phase 0H Phase 0 Final Acceptance Design

## Status and purpose

This specification defines Phase 0H only: the final integration, verification,
and human-acceptance gate for Phase 0. It introduces no product functionality,
runtime architecture, dependency, security subsystem, or later-phase work.

Phase 0H answers:

> Does the current repository, implementation, and evidence satisfy the
> authoritative Phase 0 acceptance requirements?

Only after the final evidence is reviewed and the human explicitly accepts
Phase 0 may the `phase-00-passed` tag be created. Until then, Phase 0 remains
incomplete and Phase 1 must not begin.

## Scope boundary

Phase 0H covers only:

- authoritative Phase 0 acceptance requirements;
- accepted status and evidence for subphases 0A through 0G;
- fresh current-HEAD deterministic gates;
- required local/live smoke checks already defined by Phase 0;
- repository cleanliness, credential hygiene, and reproducibility;
- one final whole-Phase-0 review;
- a complete final evidence report and explicit human acceptance.

It must not add or redesign Moodle, RAG, MCP, agent, approval, dashboard,
REPL, ingestion, browser-authentication, or other Phase 1+ functionality. It
must not repeat historical benchmarks or architecture experiments merely to
recreate accepted evidence.

## Authoritative Phase 0 requirements

The requirements are recovered from `docs/PHASES.md`,
`docs/PROJECT_SPEC.md`, `docs/ARCHITECTURE.md`, `AGENTS.md`, and
`docs/DEVELOPMENT_RULES.md`:

1. The environment is reproducible from documented, locked project steps.
2. The FastAPI `/health` endpoint works with its stable process-health
   contract.
3. The SQLite database migration upgrades from a clean state, downgrades, and
   upgrades again without unexpected schema state.
4. LM Studio can be reached through the approved local configuration.
5. The configured local model can return schema-valid plain assistant output
   through the accepted Phase 0E gateway contract.
6. Git contains no credentials or authentication state, and Phase 0 output and
   errors do not expose secrets, prompts, tokens, cookies, or private data.
7. Accepted Phase 0 security, import-safety, validation, and deterministic
   execution boundaries remain intact.
8. Any Docker configuration introduced during Phase 0 validates; no Docker
   requirement exists when Docker is absent.

The final gate evaluates these requirements at the current verified revision;
historical acceptance evidence is provenance, not a substitute for a required
fresh check.

## Accepted subphase status and evidence

| Subphase | Authoritative status | Evidence reused for provenance | 0H treatment |
|---|---|---|---|
| 0A Preflight | Complete | Governance status and prior preflight record | Confirm status and include provenance; do not repeat historical machine discovery without a current requirement. |
| 0B Python + uv Tooling | Complete | Existing environment/configuration tests and accepted tooling evidence | Fresh locked sync and deterministic tooling gates verify the current repository. |
| 0C FastAPI Foundation | Complete | `tests/test_health.py`, current FastAPI application, prior acceptance record | Fresh `/health` tests and import-safety checks are required. |
| 0D SQLite + Alembic | Complete | `tests/test_database.py`, Alembic baseline, prior migration acceptance record | Fresh clean temporary-database migration round trip is required. |
| 0E Local Model Gateway | Accepted | Approved Phase 0E design, implementation tests, accepted live-smoke evidence, and human acceptance record | Fresh deterministic gateway checks **and one fresh current-environment Phase 0E generation smoke** are required. The smoke uses the already prepared profile and cannot manage LM Studio. |
| 0F Doctor / Hardware Diagnostics | Accepted | Approved Phase 0F design, implementation tests, final evidence, and human acceptance record | Fresh deterministic doctor/CLI/import checks and the approved local doctor smoke are required. |
| 0G Security Baseline Verification | Accepted | Approved 0G design/plan, current evidence report, fresh gates, independent review, and human acceptance record | Fresh current-HEAD final security/repository checks are required; no new security feature is added. |

If an older report says a subphase was pending but a later explicit human
acceptance record says it was accepted, the explicit acceptance decision and
current governance status control. The older report remains historical
provenance and must not be silently presented as current acceptance.

## Compact final acceptance matrix

| Requirement | Current verification | PASS condition | Failure handling |
|---|---|---|---|
| Reproducible environment | `uv lock --check`; `uv sync --locked --all-groups`; verify no tracked mutation | Locked resolution/sync succeeds and tracked project files remain unchanged | Stop; do not rewrite dependency files to obtain a pass. |
| Python/tooling quality | Ruff format check, Ruff lint, strict mypy, full pytest, `git diff --check` | All current gates pass without weakening configuration or tests | Stop and report the first real failure. |
| FastAPI foundation | Fresh `/health` tests and import-side-effect checks | Stable HTTP 200 JSON contract and no import-time forbidden I/O | Stop; no workaround or unrelated refactor. |
| SQLite/Alembic foundation | Fresh clean temporary database upgrade/downgrade/re-upgrade tests | Expected revision and schema state are reached | Stop; do not alter the database or migration history during 0H. |
| Local model gateway | Fresh deterministic gateway suite plus one fresh current-environment Phase 0E generation smoke using the already approved user-prepared profile | Deterministic gateway tests PASS and the live smoke returns a schema-valid plain `ChatResponse` from the configured model | If an external prerequisite is unavailable, record the precise blocker and stop for human decision; do not classify this applicable requirement as optional or pass it from historical evidence. Never start/load/reconfigure LM Studio automatically. |
| Doctor and hardware diagnostics | Fresh doctor deterministic suite and `aaos doctor` local smoke | Required invariants and warning-only optional diagnostics behave as accepted | Record failure; do not invent thresholds or remediation. |
| Security baseline | Fresh 0G deterministic/security checks and bounded Git hygiene check | Every applicable 0G property remains PASS; later-owned properties remain excluded | Any applicable failure blocks Phase 0; no limitation silently waives it. |
| Docker applicability | Inspect whether Phase 0 introduced Docker configuration; if absent, record NOT APPLICABLE; if present, run the appropriate existing configuration validation | No Docker configuration exists, or every introduced Docker configuration validates | Do not add Docker files, install Docker dependencies, or expand into deployment work. |
| Repository/security cleanliness | Current status, protected-path check, no phase tag before acceptance | Clean worktree, no tracked credential/auth paths, no secrets in evidence, no premature tag | Stop and remediate only through an explicitly approved scope. |
| Whole-Phase-0 scope | Final diff/history/scope review | No Phase 1+ functionality, hidden external writes, or architecture drift | Stop for human judgment if a contradiction is found. |

## Final current-HEAD verification

The execution plan must record a final revision before claiming readiness. It
must run the deterministic checks required by the applicable gates, including:

```text
uv lock --check
uv sync --locked --all-groups
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest -v
git diff --check
git status --short --branch
```

It must also run the fresh `/health`, database migration, import-safety,
gateway, doctor, bounded credential/auth-path, Docker-applicability, and
required live Phase 0E generation checks in the matrix. The report must
identify the exact final HEAD that was verified. If a genuine verification-only
test is added, it must be committed as a focused change and all affected plus
complete deterministic gates must be rerun before the new HEAD is recorded.

## Local and live smoke policy

Accepted 0E/0F live evidence may be reused for provenance, but it does not
replace the fresh Phase 0E generation smoke required by the final matrix. Every
required live/local check must be explicit and read-only:

- `/health` must be exercised locally;
- a clean temporary SQLite migration round trip must be exercised locally;
- `aaos doctor` may inspect the already prepared local environment;
- LM Studio smoke must connect to the already running service, use the already
  loaded configured model, send the approved minimal plain-generation request,
  and validate the Phase 0E `ChatResponse` contract. It must not start LM
  Studio, load/unload/download a model, change context length, alter JIT or GPU
  offload, reconfigure LM Studio, introduce agent/tool behavior, or persist
  conversation state.

An unavailable external prerequisite is recorded precisely. It does not grant
permission to mutate the environment, and it blocks the corresponding
applicable acceptance requirement unless the human explicitly decides how to
handle that specific limitation.

## Final evidence report and review

The final report must contain:

- exact final commit/HEAD and branch/status;
- the matrix above with PASS/blocked/owned classifications and evidence paths;
- accepted 0A–0G provenance references and fresh current-HEAD results;
- exact command outcomes, test counts, smoke results, and limitations;
- repository, credential/auth-path, whitespace, and tag checks;
- confirmation that no product files or later-phase functionality were added;
- one final whole-Phase-0 review result;
- recommended next action and the explicit human acceptance decision field.

The whole-Phase-0 review is a final consistency and scope review, not a new
architecture exercise. It must confirm that no applicable requirement is being
waived, no stale evidence is presented as current, and no Phase 1+ work leaked
into Phase 0.

## Acceptance and tag rule

Phase 0H is ready for human acceptance only when every applicable matrix row
passes at the exact reported final HEAD, including the fresh Phase 0E
generation smoke, all required current checks pass, and the final review is
clean. The agent must then stop and request explicit human Phase 0 acceptance.

Only after that explicit human acceptance may an authorized Git operation create
the `phase-00-passed` tag. The final deterministic verification HEAD, final
whole-Phase-0 review HEAD, human-accepted Phase 0 HEAD, and tag target must be
the same commit. No commit created after the verified/reviewed revision may
silently become the tag target. Tag creation must explicitly target the
human-accepted commit and then verify that `phase-00-passed` resolves to that
exact commit, following any established repository tag convention. The tag
must not be created automatically during design, planning, execution, or
review. Phase 1 remains blocked until the tag and acceptance decision are both
appropriately recorded.

## Non-goals

Phase 0H does not add features, dependencies, security subsystems, external
integrations, credentials, academic-data access, agent behavior, approval
flows, dashboard routes, REPL functionality, or Phase 1 scaffolding.

This is a design/specification only. It does not authorize implementation,
final gates, tag creation, Phase 1, or any change beyond the later approved
Phase 0H execution plan.
