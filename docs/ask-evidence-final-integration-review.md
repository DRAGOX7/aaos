# Ask and Evidence: final integration review — 2026-10-30

## Decision and Git integration

**READY WITH LIMITATIONS.** No merge-blocking security, citation-integrity,
reference or integration defect remains after the corrections below. Final broad
backend, frontend, production build, type checks, contract checks, independent
correction review and read-only live baseline probes passed. Limitations are
preexisting formatting debt, partial formal security-tool inventory coverage,
and the explicitly unmeasured deployment/cancellation/performance cases below.

Initial branch: `codex/fix-ask-local-topic-reference`, HEAD `00d22e4c`.
Local and recorded remote `main`: `621288d84a1bcefe3c4841a007ff4e0006f17383`.
No fetch, push, PR, reset, stash, forced update, or cherry-pick was used.
The three requested commits form one linear ancestry chain:

```text
621288d8 main
  15660c8d Verified Ask Streaming / Evidence Inspector V1
    4bbe0b8e Evidence Inspector V2
      00d22e4c same-message reference fix
```

`git branch --contains` confirmed their feature branches and descendants;
`git merge-base main HEAD` returned `621288d8`. `git cherry main HEAD` marked
all three patches new, so main has neither these ancestors nor equivalent patches
under another hash. `codex/ask-evidence-final-integration` was created at
`00d22e4c`; it inherits all three without duplication. Planned integration is a
normal local `git merge --no-ff` from main after verification, retaining a distinct
integration boundary. The integration commit/hash and post-merge results are
recorded below when available.

Unrelated work preserved: the preexisting `frontend/next-env.d.ts` dev-type imports;
`.codex/`, `.pytest-tmp-archive-ah/`, `AAOS_HANDOFF.md.txt`,
`Prompting-Opus-5-5-Motion-Design-PDF.pdf`, `claude.md.txt`, `notification.mp4`,
and `reflex`. Production builds restore the exact original next-env bytes in
`finally`. Existing feature branch pointers remain untouched. Evaluation branch
`eval/lfm3-1.7b` remains `bdcd702196a3b29dfac2dd1979abe41e286d456c`.

## Files reviewed and corrected

Reviewed the entire 34-file `git diff 621288d8..00d22e4c`, including:

- Backend: `ask_progress.py`, `main.py`, agent `conversation_context.py`, RAG
  `service.py` / `inspection.py`, web `ask_stream.py` / `routes.py` /
  `contracts.py` / `document_files.py` / `inspection.py`, and supporting
  orchestrator, intent routing, visibility and citation verification code.
- Frontend: stream proxy, `lib/api/ask.ts`, evidence alignment, Ask view and
  conversation/result cards, evidence workspace, Arabic messages, generated
  OpenAPI/types, and all changed tests.
- The three earlier verification reports and relevant repository instructions,
  governance/phase documents, archived-course specification and Next route docs.

Integration corrections are deliberately small:

1. `frontend/components/aaos/citation-evidence.tsx`: count source payload limits
   in Unicode code points, matching Python's 16,000-character cap. A valid source
   with supplementary Unicode previously failed the UTF-16 count. Regression
   first failed, then passed; 16,001 code points are still rejected.
2. `frontend/components/aaos/ask-result-cards.tsx`: isolate structural headings
   with `bdi dir=auto` beside translated location/version metadata.
3. `frontend/tests/evidence-inspector-v2.test.tsx`: test Unicode limits, RTL
   heading isolation, and switching between two answers that reuse the same
   claim ID and document version but bind different claims/passages.
   `frontend/tests/ask-view.test.tsx` checks the same complete visible citation
   label across the new bidi element rather than requiring one text node.
4. `src/aaos/agent/conversation_context.py`: recognize the explicitly requested
   “Compare this with the previous lecture” as a genuine context reference. It
   previously returned NONE and could reach unrelated retrieval without a trusted
   comparison target. This gap predates the three commits but prevents the
   requested conservative-reference acceptance. Added missing/trusted-context
   and both-endpoint refusal-before-RAG regressions in
   `tests/test_ask_local_topic_reference.py`.
5. `src/aaos/web/routes.py`: enrich inspection inside the shared response mapper,
   constructing one response instead of copying it with `update=...`. The latter
   tripped the existing read-only source audit despite doing no database write.
   The read-only audit remains unchanged.
6. `tests/test_phase7d_scope_audit.py`: extend its exact closed route inventory
   by precisely the three owner-authorized routes; no wildcard or removed check.

No credentials, protected datasets, database migrations, provider changes,
model/dependency upgrades, source reindexing or unrelated formatting were added.

## Security and trust boundaries

Path reviewed: browser → fixed same-origin Next proxy → shared Ask execution →
conversation resolver → intent/tool and source scope → retrieval/coverage → model
→ existing claim and citation verifier → final response projection → inspector.

The stream carries only enumerated stages and elapsed milliseconds before one
terminal result. Model strings and academic excerpts cannot enter stage events.
Unexpected execution errors use fixed browser-safe messages; no raw exception or
credential is serialized. JSON and SSE share the same assistant and response
projection. Inspection is optional and does not change verifier decisions or the
agent/model observation contract; historical responses without it still work.

Inspection records are request-local and reset in `finally`. Response identity,
generation/revision and full citation equality gate enrichment. Ambiguous chunk
associations omit the highlight. Browser matching uses only pipeline-provided
passages against freshly fetched original source; displayed marks slice that
original string. They never use an LLM-generated span. The UI explicitly says
“Highlight locates a cited retrieved passage, not a minimal verified claim span.”
Large passages/headings may legitimately remain highlighted when they belong to
the retrieved chunk; this does not claim every highlighted word was NLI-verified.

Evidence/version-file routes independently recheck `item_visible` with existing
archived-course support. A numeric citation/version identifier is not an access
capability: guessed hidden or missing records are refused. Accessible records
remain readable by the local owner, consistent with existing document APIs.
Original-file reads retain MIME restrictions, resolved content-root containment,
recorded size and SHA256 validation. Version-specific navigation stays on the
cited version after a later ingestion. No new tenant/authentication boundary is
claimed: the existing default deployment is a single-user loopback service.
Internet-exposed/multi-user deployment security is outside this review.

Independent security and final correction review found no confirmed security
regression. Formal scan `a03a42c4-badd-45fb-8309-25f4fcef68f9` was sealed with
**partial coverage** and a retained manual 34-file source-review receipt. Its
automated inventory/candidate-registration helper failed because installed
Git `2.39.1.windows.1` does not support `git cat-file --batch -Z` (exit 129).
Direct committed-file inspection succeeded. The limitation is retained rather
than claiming the inventory phase passed. No Git/plugin/config changes were made.
The scan report is in the Codex Security workbench under that scan ID.

## Standards

Independent standards review found no hard violation in the initial diff.
One operational judgment remains: `read_evidence` materializes up to 20 complete
page/unit text values before applying the 16,000-character response cap. Very
large individual extracts can therefore use more memory than the response cap
suggests. No resource-exhaustion reproduction or memory benchmark was performed;
bounded SQL projection is a recommended follow-up, not a claimed fixed defect.

## Spec

Independent spec review found no frontend merge blocker. Its two minor gaps—
structural-heading bidi isolation and multiple-answer selection coverage—were
closed. Independent final correction review found no remaining actionable issue.
Standards: 0 hard violations, 1 operational judgment. Spec: 2 minor gaps closed.

## Streaming, cancellation and requests

Existing tests cover verified success, fixed errors, invalid input, blocked final
text while verification is pending, cancellation, interrupted/malformed streams,
UTF-8 and CRLF fragmentation, multiple events per chunk, bounded buffering and
reader cleanup. Full Ask UI coverage includes timeout/stop/retry state cleanup,
repeated submissions and stable request identifiers. The frontend guards one
submission at a time and aborts on unmount; new-conversation switching is blocked
while pending. The backend retains existing conversation lease/idempotent action
handling rather than adding another execution path.

Closing the browser stream sets a cooperative cancellation event; it does not
forcibly kill an already running model call or undo an issued external action.
The stream observes eventual task exceptions. Retry keeps the existing request
identity/approval architecture; a transport error is never shown as action success.
The frontend waits at most 60 seconds, so slower verified requests can outlive the
browser wait until backend checkpoints observe cancellation. No external action
was invoked during live testing.

Remaining coverage limits: source review confirms proxy signal propagation, but
a real browser-to-proxy-to-ASGI disconnect timing test was not run. Synthetic
disconnect/reader cancellation and existing approval/idempotency tests are not
presented as proof of hard model interruption. No load or concurrency stress
benchmark was run.

## Evidence UI and browser checks

Final component suite covers desktop non-modal comparison, mobile modal close and
focus restoration, citation switching, old responses, exact/normalized alignment,
ambiguous/missing metadata, source errors/retry, context expansion and Arabic
direction. Sources are fetched on selection, at most four passage locations, with
an additional bounded range read when expanded. No source request is needed to
display the verified answer. No-store reads favor current access checks over a
cross-answer source cache.

Headless installed Chrome, with **all API requests intercepted by synthetic
fixtures**, passed at 1440, 900 and 390 pixel widths in both English and Arabic:
correct passage, original link, context expansion, desktop vs modal behavior,
modal Tab containment, Escape, trigger focus restoration, mixed-language text,
reduced-motion emulation and no horizontal page overflow. No private academic
page or screenshot was exposed. The first browser script expected an incorrect
label; correcting it to the actual explanatory copy made the assertion pass.
Playwright's bundled Chromium was unavailable; installed Chrome worked.

Rendered source-highlight contrast measured **14.45:1 in light mode** at all
three widths and **12.43:1 in dark mode** at desktop, above 4.5:1. Accessible
names/headings and Radix modal behavior were checked; a dedicated screen-reader
session and exhaustive contrast audit were not performed. These
checks do not replace the owner's prior real-document manual evidence.

## Automated commands and results

All backend tests use disposable fixtures, not configured databases. Protected
evaluation families and Docker engine qualification are excluded deliberately:

```powershell
.venv\Scripts\python.exe -m pytest tests --ignore-glob=tests/test_agent_evaluation*.py --ignore=tests/phase6a_evaluation -k "not test_real_docker_supervisor_path_uses_qualified_runtime" --tb=short
```

First broad run: **4475 passed, 2 failed, 6 skipped, 1 deselected**, 565.30 seconds.
The two failures were the source read-only audit and exact route inventory
described above; both were corrected, not ignored. Final broad rerun:
**4480 passed, 6 skipped, 1 deselected, 2 warnings**, 397.48 seconds. Warnings
were Google API's future Python 3.11 support notice and Chroma's legacy embedding
configuration deprecation. No protected acceptance gate was executed.

Affected backend rerun:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_phase7_documents_api.py tests/test_phase7d_scope_audit.py tests/test_rag_service.py tests/test_document_files_and_study_tasks_api.py tests/test_ask_stream.py tests/test_ask_local_topic_reference.py tests/test_phase7g_route_inventory.py
```

**99 passed**. Additional comparison resolver tests reproduced NONE before the
fix; both transports now refuse missing context before model/retrieval. Sandbox
TestClient event-loop initialization hung in `socketpair`; authorized escalated
runs above succeeded. No test assertions were removed or weakened.

```powershell
.venv\Scripts\python.exe -m ruff check src tests scripts
.venv\Scripts\python.exe -m ruff format --check src tests scripts
.venv\Scripts\python.exe -m mypy src
```

Ruff lint passed. Mypy passed **226 source files** using the established strict
configuration. Format check reports **589 formatted, 2 preexisting failures**:
`src/aaos/agent/academic_orchestrator.py` and
`tests/test_phase_h_ask_projections.py`. Both are byte-for-byte unchanged by the
combined diff; no unrelated formatting cleanup was performed.

From `frontend`:

```powershell
npm test -- --maxWorkers=2
npm run lint
npm run typecheck
npm run build
```

Final frontend: **371 passed / 57 files**, 55.40 seconds. The first 367-test
baseline passed; the bidi correction initially exposed a text-node-specific
assertion, then the composed visible-label assertion passed. Four affected UI
files also passed **74 tests**. ESLint has only two preexisting unused route-parser
import warnings in course/document detail views. TypeScript and production build
passed. Build emitted the existing parent `package-lock.json` ignored warning
and experimental proxyTimeout notice; all 14 static pages and the dynamic Ask
stream route built successfully.

Contract regeneration check:

```powershell
.venv\Scripts\python.exe scripts/export_openapi.py
# from frontend:
npx --no-install openapi-typescript openapi/aaos-v1.json -o types/api.generated.ts
git diff --exit-code -- openapi/aaos-v1.json types/api.generated.ts
```

Passed without semantic generated-file changes. `git diff --check` passed.
No additional relevant CI workflow exists under `.github/workflows`.

## New live-model validation and measurements

Provider metadata verified before calls: `chatgpt_plan`, `gpt-6-luna`.
Only the existing subscription provider was used; no paid API provider or
external state-changing tool was selected. Fresh IDs/conversations were used.
Only status/count/version/timing metadata was recorded; no answer, source text,
credential or protected evaluation content was printed or retained.

| Prompt | Transport | Result | Citations / inspections | First stage | Total |
| --- | --- | --- | --- | --- | --- |
| Original currently-available-documents KNIME prompt | JSON | ANSWERED, no reference refusal; current-course guidance | 0 / 0 | n/a | 0.17 s |
| What is KNIME from my previous semester? Explain its main features and cite the relevant lecture sources. | SSE | ANSWERED, archived provenance | 2 / 2 | 0.031 s | 40.80 s |
| What is KNIME? Explain it using my previous semester's lecture. | SSE | ANSWERED, archived provenance | 1 / 1 | 0.015 s | 19.26 s |
| Exact global market share in 2025 according to the previous-semester KNIME lecture | SSE | INSUFFICIENT_EVIDENCE | 0 / 0 | 0.000 s rounded | 9.30 s |

Both grounded requests cite version 4. Version-specific evidence and original-file
GETs succeeded; file size was 8,748,309 bytes. No citation IDs were fabricated.
Stage payloads were checked to contain only stage/elapsed_ms. Successful streams
progressed through retrieval, coverage, writing and verification; unsupported
coverage did not fabricate an answer. These are genuine configured-model/RAG
checks, distinct from mocked API/UI tests. They exercise the running resolver
fix and V2 baseline; the final additional comparison/Unicode/projection corrections
are verified by tests/build/source review, not by claiming a runtime reload.

Timings are single observations, not a latency distribution or CPU/VRAM benchmark.
Totals include final read-only source checks for cited versions. No paired JSON
versus SSE latency comparison, peak process memory, PDF rendering, or concurrent
load measurement was performed. Streaming introduces one task, a small stage
queue and heartbeat polling; it does not duplicate retrieval/model generation.
Source alignment is bounded to fetched source strings; API file reads still load
the full original bytes, consistent with the existing file service.

## Remaining recommendations and merge record

- Restart the backend through its normal launcher after integration to load all
  corrections; refresh the frontend. Existing processes/configuration were not
  replaced. No deployment or external publication was performed.
- Follow up on bounded DB text projection, real proxy/ASGI disconnect testing,
  a dedicated screen-reader/contrast pass and concurrent-request profiling.
- Upgrade Git separately if a fully automatic Codex Security inventory receipt
  is required; the retained source review is not an invented successful helper run.
- Resolve the two preexisting formatting files and lint/dependency notices in
  separate maintenance work if desired.

Integration correction commit: `b179aa53`. Reviewed integration branch tip:
`00b3b596cdd012af1e5f515400990559b6a9a589` on
`codex/ask-evidence-final-integration`.

Local merge completed using:

```powershell
git switch main
git merge --no-ff codex/ask-evidence-final-integration -m "Merge verified Ask streaming and evidence integration"
```

Merge commit: **`20bdcff348b8a2a72812763d64c797e3454d004f`**. Its parents are
the original main `621288d8` and verified integration tip `00b3b596`.
`git merge-base --is-ancestor` succeeded for all three requested commits against
main. `git diff --exit-code HEAD^2 HEAD -- src frontend tests` confirmed the
merge's code and tests equal the tested integration branch. No conflicts occurred.

Post-merge smoke:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ask_stream.py tests/test_ask_local_topic_reference.py tests/test_document_files_and_study_tasks_api.py tests/test_phase7d_scope_audit.py tests/test_rag_service.py tests/test_phase7_documents_api.py tests/test_phase7g_route_inventory.py
# from frontend:
npx --no-install vitest run tests/ask-stream.test.ts tests/ask-view.test.tsx tests/evidence-inspector-v2.test.tsx tests/citation-evidence.test.tsx tests/evidence-alignment.test.ts --maxWorkers=2
```

**99 backend passed** (9.11 seconds); **87 frontend passed / 5 files**
(7.59 seconds). Final branch is local `main`; this report receives a separate
documentation-only commit after the merge to record the known merge hash and
smoke results. Feature/evaluation branches remain preserved. No push or PR.

Read-only live/browser probes ran as disposable `.integration-live.py`,
`.integration-browser.py`, `.integration-browser-ar.py` and
`.integration-browser-dark.py` using `.venv\Scripts\python.exe`; the probe files
and metadata-only logs were removed after their results were recorded here.

Tool-reported security-scan rollup: 7,235,096 total tokens (7,210,315 input,
including 7,012,096 cached input; 24,781 output). This is the plugin's rollout
aggregate including earlier chat context, not a task-isolated measurement or
an API billing calculation. The bounded security-review goal took 14m24s.
