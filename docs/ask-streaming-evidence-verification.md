# Verified Ask streaming and citation evidence — 2026-10-30

## Scope and initial state

Resumed `claude/project-thread-ajbkxr` at `621288d8`. The working tree already contained
stream stages, the SSE backend route, shared response projection, frontend proxy/parser,
Ask progress labels, generated API changes, backend tests, and partially updated UI tests.
The evidence inspection UI was missing. No new branch, merge, push, or PR was created.
This is owner-requested local work on the existing Ask features, not a new phase acceptance.
Protected evaluations and live user databases were not accessed.

## Decisions and completed work

- One shared Ask service and response mapper serve JSON and SSE. Progress reports only
  real pipeline stages. The final response is emitted after the shared pipeline returns,
  including its existing evidence verification; raw model tokens never enter the stream.
- Hardened fragmented UTF-8/CRLF parsing, incomplete/invalid responses, bounded buffering,
  and response-reader cancellation. The proxy propagates stream cancellation upstream.
- Backend disconnect sets the existing cooperative cancellation event; it does not
  forcibly cancel potentially executing actions. Background exceptions are observed,
  with fixed browser errors and metadata-only server logging.
- Added a design-system Sheet for each Ask source: original extracted text, course/title,
  cited page or structural location, claim association, and version-specific original file.
  Existing separate RAG document navigation remains unchanged.
- Evidence is original context from the cited location, not an invented quote or a new
  per-claim verification result. It is capped at 20 locations / 16,000 characters, with
  explicit truncation. Missing text/access and retry have dedicated states.
- Document visibility is rechecked on evidence and file reads. Original files retain
  content-root containment, recorded size/hash verification, and safe content types.
  Browser URLs contain version identifiers, never local storage paths.
- Sheet supports RTL, mobile width, focus trapping, Escape, focus return, and reduced motion.
- Updated exact route inventories and regenerated API schema/types; added behavioral tests.

## Verification

Final scoped backend gate: **159 passed**. Command from repository root:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ask_stream.py tests/test_rag_service.py tests/test_document_files_and_study_tasks_api.py tests/test_phase_h_ask_projections.py tests/test_rag_structural_citations.py tests/test_rag_structural_web_transport.py tests/test_phase7_rag_api.py tests/test_phase7_rag_security.py tests/test_phase7_openapi_contract.py tests/test_main_lazy_services.py tests/test_phase7g_route_inventory.py tests/test_phase7g_security.py tests/test_agent_academic_orchestration.py
```

Final full frontend gate: **55 test files / 334 tests passed**. From `frontend`:

```powershell
$env:TEMP='C:\Users\aljaf\aaos\.tmp'
$env:TMP=$env:TEMP
npm test -- --maxWorkers=2 --reporter=dot
npm run typecheck
npm run lint
npm run build
```

Type checking and production build passed. ESLint had zero errors and two pre-existing
unused-import warnings in `course-detail-view.tsx` and `document-detail-view.tsx`.
Backend Ruff passed on all changed Python files and mypy passed on the six changed
implementation modules plus their imported dependencies:

```powershell
.venv\Scripts\python.exe -m ruff check src/aaos/ask_progress.py src/aaos/web/ask_stream.py src/aaos/rag/service.py src/aaos/main.py src/aaos/web/contracts.py src/aaos/web/document_files.py src/aaos/web/routes.py tests/test_ask_stream.py tests/test_rag_service.py tests/test_document_files_and_study_tasks_api.py tests/test_phase7g_route_inventory.py
.venv\Scripts\python.exe -m mypy src/aaos/ask_progress.py src/aaos/web/ask_stream.py src/aaos/web/document_files.py src/aaos/web/routes.py src/aaos/main.py src/aaos/rag/service.py
.venv\Scripts\python.exe scripts/export_openapi.py
# From frontend:
npx openapi-typescript openapi/aaos-v1.json -o types/api.generated.ts
```

Earlier failures were diagnosed and addressed: sandbox Windows asyncio socketpair startup
hung before backend execution; the isolated tests passed outside the sandbox. SWC could
not canonicalize the workspace inside the sandbox; production build passed outside it.
The initial Vitest worker cache failed with ENOENT; a stable temp directory and two workers
resolved it. Exact API inventory assertions required registering the three new routes.

Independent security/correctness review found a valid citation-range/reader-limit mismatch.
Fixed by limiting the excerpt, not rejecting the citation. Focused re-review closed the
finding; the requested short-text wide-range regression also passes.

Headless installed Edge against the production frontend used synthetic intercepted API
responses only. Desktop (1440×1000) and mobile (390×844) passed: parsed final answer to
citation to original text, keyboard opening, focus trap, Escape, focus restoration, and
no horizontal overflow. Both rendered screenshots were inspected. Real-model quality,
real-data latency, live network disconnect timing, and production deployment were not tested.
Full repository/protected evaluation suites were intentionally not run.

## Manual UI test

1. From `C:\Users\aljaf\aaos`, run `.\scripts\start-aaos.ps1` using your existing
   data-directory configuration. Start LM Studio if using the default local provider.
   Open `http://localhost:3000/ask`.
2. Ask a read-only question about a specific indexed lecture. While waiting, confirm the
   current processing stage changes as the pipeline reaches it, with no answer shown
   until the final result. Short requests may finish too quickly to show every stage.
3. Confirm the answer and Sources render. Click a source title: inspect its original
   text, course, cited page/units, and the explicit source-context wording. Click another
   citation to confirm it resolves to the corresponding document/version.
4. Choose Open original document. PDF citations open the cited version at `#page=N`;
   structural citations open/download the version without a fabricated page number.
5. Open with keyboard Enter, Tab through the panel, close with Escape and the Close
   button, and confirm focus returns. Repeat in a narrow mobile viewport and Arabic mode.
6. Ask again, select Stop waiting, and confirm the loading state clears. Resubmit the same
   question. For an interrupted stream, use browser DevTools Offline during the request,
   restore connectivity, and retry; loading must clear and no partial answer may appear.
7. An inaccessible source should show an unavailable state and Retry. A source without
   extracted text should say so. Long source contexts show the truncation notice.

## Files changed

- `frontend/app/api/v1/ask/stream/route.ts`
- `frontend/components/aaos/ask-conversation.tsx`
- `frontend/components/aaos/ask-result-cards.tsx`
- `frontend/components/aaos/ask-view.tsx`
- `frontend/components/aaos/citation-evidence.tsx`
- `frontend/lib/api/ask.ts`
- `frontend/lib/i18n/messages-ar.ts`
- `frontend/openapi/aaos-v1.json`
- `frontend/types/api.generated.ts`
- `frontend/tests/ask-stream.test.ts`
- `frontend/tests/ask-view.test.tsx`
- `frontend/tests/citation-evidence.test.tsx`
- `frontend/tests/phase7g-ui-integration.test.tsx`
- `src/aaos/ask_progress.py`
- `src/aaos/main.py`
- `src/aaos/rag/service.py`
- `src/aaos/web/ask_stream.py`
- `src/aaos/web/contracts.py`
- `src/aaos/web/document_files.py`
- `src/aaos/web/routes.py`
- `tests/test_ask_stream.py`
- `tests/test_document_files_and_study_tasks_api.py`
- `tests/test_phase7g_route_inventory.py`
- `tests/test_rag_service.py`

This report is also part of the feature commit. Pre-existing `frontend/next-env.d.ts`
changes and unrelated untracked files are excluded. The build rewrote next-env to production
paths; its initial dev-path edits were restored before staging. The evaluation branch
`eval/lfm3-1.7b` was not modified.

## Remaining limitations and next action

Evidence shows bounded original page/unit context rather than exact claim-span highlighting,
because the existing Ask citation payload has no excerpt or character offsets. A PDF viewer
can honor page anchors; Word does not have reliable page anchors. No live model request or
external action was performed. Recommended next action: run the manual read-only UI check
with an indexed lecture; retain the existing branch for owner review.
