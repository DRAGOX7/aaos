# Phase 9 release qualification checklist

Status: `READY_FOR_HUMAN_ACCEPTANCE`

This checklist is the release gate consumed by Task 9. It does not mark
Phase 9 passed or create `phase-09-passed`. Every PASS below refers to
bounded evidence from the already-qualified local system or to a deterministic
fixture. Missing or non-reproducible evidence is BLOCKED, never silently
treated as a pass.

## Frozen release identity

| Fact | Value |
| --- | --- |
| release candidate commit | `dcc02f5962eb9108cdab1511f50e14ee5a14b30e` |
| Phase 8 freeze tag target | `5dea7f19bbe26e85b968afc63a21c10f0dd4e389` |
| Alembic head | `0007_phase8_visual_provenance` |
| release manifest schema | `phase9-release-v1` |
| release manifest SHA-256 | `cd01ae21f06ca60ebfff83cbd417aad7c569b6cadad1e97e4c936f9e3eae999f` |
| Phase 9 state | `IN_PROGRESS` pending human acceptance |

## Backend count reconciliation

The Phase 8 acceptance record reported 3,144 passed tests. Task 8 added 49
Phase 9 tests, producing 3,199 collected / 3,193 passed tests at that
checkpoint. Task 9 adds 9 pure acceptance-validator tests; the final
canonical collection is therefore 3,208 tests and the fresh full run reports
3,202 passed, 6 environment-gated skips, and 1 warning. There are no test
deletions or renames after the Phase 8 tag. The `3,044` figure previously
written in the Task 8 checklist was the earlier Phase 7 total carried forward
by mistake; it was not produced by the current command and does not represent
lost coverage.

## Required dimensions

| Dimension | Status | Evidence / boundary |
| --- | --- | --- |
| clean Git tree and frozen identity | PASS | release manifest and final `git status --short` |
| accepted Phase 0–8 history | PASS | task commits and Phase 8 freeze tag above |
| backend tests | PASS | canonical full pytest: 3,202 passed, 6 skipped, 1 warning; 3,208 collected |
| frontend tests | PASS | 43 files, 173 tests passed |
| static/type/lock/diff gates | PASS | Ruff check, canonical mypy gates, uv lock, diff check |
| API/schema drift | PASS | generated OpenAPI/API drift check |
| migration verification | PASS | disposable migration qualification at `0007_phase8_visual_provenance` |
| security/adversarial checks | PASS | Phase 9 security/public-release and prior-phase adversarial suites |
| Docker/computation qualification | PASS | real Docker qualification and bounded deterministic computation evidence |
| browser acceptance | PASS | previously accepted Phase 7 production browser evidence; no new provider writes |
| RAG/grounding evaluation | PASS | deterministic local evaluation: retrieval 8/8, citation 2/2, grounding/NLI records retained |
| multimodal evaluation | PASS | bounded deterministic visual provenance checks; `DERIVED_VISUAL_EVIDENCE` remains distinct from `EMBEDDED_PDF` |
| external-action lifecycle | PASS | deterministic fake adapter; approval/claim/UNKNOWN_OUTCOME invariants preserved |
| failure/recovery qualification | PASS | synthetic fault matrix and no-automatic-retry recovery evidence |
| clean setup/reproducibility | PASS | clean Python/uv, frontend/npm, configuration, migration, and import-inert checks |
| Windows qualification | PASS | PowerShell/path/newline/UTF-8 and local runtime qualification |
| performance/resource evidence | PASS | bounded local-resource qualification; no real model benchmark claimed |
| public-release/privacy audit | PASS | tracked public-release scan: zero real secret, private-data, database, weight, or temporary-artifact blockers |
| documentation/claim audit | PASS | synthetic and historical claims are labelled; no RLM/Deep Ask claim |
| cleanup/accounting | PASS | Phase 9 real provider calls `0`; real external writes `0`; RLM work `0` |
| independent review | PASS | `PASS_NO_CRITICAL_OR_IMPORTANT_FINDINGS` |

## Evidence accounting

The machine-readable checklist implementation is
`scripts/phase9/release_check.py`. It accepts only typed, bounded report
objects, requires every report dimension, hashes sanitized JSON outputs, and
blocks dirty trees, wrong tags, wrong migration heads, missing reports,
non-zero external writes, provider calls, model downloads, or RLM work.

Evidence package outputs contain summaries, commands, statuses, counters,
and SHA-256 references only. They do not contain credentials, cookies,
authorization state, private university material, normal AAOS databases,
model weights, raw provider payloads, or temporary runtime artifacts.

## Limitations carried forward

These are recorded rather than hidden:

- full-repository Ruff-format check retains the unrelated historical baseline
  findings; the Task 5–7 release files are formatted and `ruff check` passes;
- the raw combined `mypy scripts/phase9 tests/phase9` namespace/stub command
  remains a diagnostic limitation; canonical `mypy src` and
  `mypy src/aaos scripts/export_openapi.py` pass;
- author-specific internal planning paths remain in internal plans/tests only;
- no explicit model-weight suffix rules were added to `.gitignore`; no model
  weights are tracked and local model state is external to the release tree;
- deterministic/fake adapter and benchmark measurements are labelled as such;
- no real VLM benchmark was run, so no real VLM performance claim is made;
- historical Phase 6B Google Calendar CREATE evidence is historical and is not
  represented as repeated Phase 9 live execution;
- Phase 9 remains `IN_PROGRESS` until a separate human acceptance decision.

## Human gate

The next action is human review of this checklist, the sanitized evidence
package, and the Task 9 acceptance report. Only that review may authorize a
future Phase 9 freeze; this verification does not create a release tag.
