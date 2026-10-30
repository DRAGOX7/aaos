# Evidence Inspector V2 — implementation and verification, 2026-10-30

## Starting point and scope

The starting branch was `claude/project-thread-ajbkxr` at `15660c8d`, the verified
streaming and first evidence-panel commit. Created `codex/evidence-inspector-v2`
from that implementation. Unrelated files, `.codex/hooks.json`, and the initial
`frontend/next-env.d.ts` dev-path edits were preserved and excluded from the commit.
No merge, push, PR, protected evaluation, or live user-database operation was performed.
The build-generated changes to next-env were restored to its initial dev imports.
This is an owner-requested refinement of existing Ask features, not phase acceptance.

The initial inspector used one modal Sheet on every viewport, retrieved bounded
page/unit context, lacked claim text/source offsets, and started with page headers.
Manual confirmation that original PDF links opened the right version/page was supplied
by the owner; this task did not repeat that check against private files.

## Architecture and trust boundaries

The existing RAG pipeline retains validated `RagClaim` objects, their evidence IDs,
retrieved chunks, original source offsets, and generation/revision identity. Its agent
adapter intentionally projects a closed, bounded citation shape without those details.
V2 preserves that agent/model observation schema and all verification decisions.

A request-local ContextVar collector records optional inspection bindings only after
claim verification, citation validation, and validated response rendering. Each binding
contains the surviving claim text and source contributions from a uniquely identified
cited retrieved chunk. Colliding chunks on the same claim/version/location do not acquire
an arbitrary binding. Text plus claims is bounded to 32,000 characters per record, with
up to four source contributions per citation. At most four request-local records are
retained; existing agent runtime policy permits only one useful RAG call per request.

Both JSON and streaming Ask share one execution wrapper. It enriches only the final web
projection when generation, revision, answer text, rendered text, status, language, and
individual trusted citation fields match. The collector is reset in `finally`; parallel
requests do not share state. No raw model output, inspection text, or new answer content
is sent in stage events. No additional model, embedding, or PDF-parsing calls were added.

The optional `inspection` citation field is additive. Old/historical citations, summary
paths without captured spans, and ambiguous bindings use the original context fallback.
Malformed optional client metadata is ignored rather than invalidating a verified answer.
No index, migration, database persistence, or conversation schema changes are required.

## What highlighting genuinely means

V2 locates original **retrieved passage contributions**, not a minimal verified
claim-supporting/NLI premise span. The verifier can evaluate selected or bounded premises;
it does not export a final minimal per-claim premise range through this response contract.
The UI explicitly labels the passage and explains that limitation. Claim text is shown
only from an accurate validated claim/citation association; the answer itself is not
sentence-matched or rewritten. The selected citation reference remains visibly active.

On selection, the UI rechecks document access through the existing version-specific
source endpoint. It first checks the recorded Unicode-code-point offsets against the
returned original text. Python offsets are converted to JS UTF-16 indices. If offsets
cannot be used, it may relocate only a unique normalized match within complete source
text. Matching handles NFC composition, whitespace/line breaks, presentation quotes,
and conservative PDF word hyphenation. It preserves math signs, numeric punctuation,
subscripts, Arabic marks, and single-letter variables. It never compares the generated
answer to the document or uses fuzzy nearest-sentence matching.

Normalization maintains original-offset maps and is used only for matching. Highlighted
text is sliced from the unchanged fetched source. Repeated matches without reliable
offsets, no matches, version mismatches, missing metadata/text, and truncated excerpts
with failed offsets produce an honest unhighlighted fallback. Alignment failure changes
neither the answer nor its verification status. Original document navigation remains.

A review found that a late cited occurrence beyond the 16,000-character source cap could
otherwise relocate to an identical earlier prefix occurrence. Fixed by disallowing
relocation within incomplete source. The regression and independent focused re-review
passed. A later conservative guard also avoids interpreting `a-\nb` as the variable `ab`.

## Layout and multilingual behavior

Wide screens (at least 1200px) show the existing answer and a sticky, independently
scrollable, non-modal evidence pane. There is no overlay/blur or modal focus trap on that
path; the answer remains interactive and is rendered once. Selecting another citation
updates the pane and active reference. New conversation resets the inspector selection.

Tablet/narrow screens use the existing full-width Sheet with modal keyboard focus,
Escape/Close, and return focus. Source and claim paragraphs use `dir="auto"`, logical
start alignment, and `unicode-bidi: plaintext`; filenames/title/metadata use bidi
isolation. Interface labels follow the UI locale independently of source language.
Arabic/English switching preserves the original source wording.

The inspector shows title/location/version/course, selected claim when available,
located retrieved passage first, then collapsible surrounding context and the original
file action. Headers before a reliable passage stay collapsed. Context complements omit
the already shown matched ranges where possible. Source text remains bounded; PDF page
anchors are retained, while structural documents never acquire invented page numbers.

## Requests and performance

No new API route was necessary. Selecting a new citation makes one existing source GET
per unique captured page/unit (at most four), so the original source can be checked and
access permissions revalidated. A historical citation makes its original one bounded
range request. Expanding multi-location surrounding context makes one additional bounded
range GET only then. Single-location expansion reuses the fetched source without another
request. Switching/retrying rechecks access; no private evidence is persistently cached.
Closed panels and stale selections abort their fetches. Each source response retains its
20-location/16,000-character cap. Matching is bounded to those excerpts, not entire PDFs.

## Verification and commands

Scoped backend integration/security gate: **165 passed**. From repository root:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_ask_stream.py tests/test_rag_service.py tests/test_document_files_and_study_tasks_api.py tests/test_phase_h_ask_projections.py tests/test_rag_structural_citations.py tests/test_rag_structural_web_transport.py tests/test_phase7_rag_api.py tests/test_phase7_rag_security.py tests/test_phase7_openapi_contract.py tests/test_main_lazy_services.py tests/test_phase7g_route_inventory.py tests/test_phase7g_security.py tests/test_agent_academic_orchestration.py
```

After the final metadata-budget adjustment, focused RAG suite: **33 passed**:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_rag_service.py
```

Full frontend suite: **57 files, 366 tests passed**. After the final additional math-safe
hyphenation guard: **33 targeted alignment/inspector tests passed**. From `frontend`:

```powershell
$env:TEMP='C:\Users\aljaf\aaos\.tmp'
$env:TMP=$env:TEMP
npm test -- --maxWorkers=2 --reporter=dot
npm test -- --maxWorkers=2 tests/evidence-alignment.test.ts tests/evidence-inspector-v2.test.tsx
npm run typecheck
npm run lint
npm run build
```

TypeScript and final production build passed. ESLint: no errors; two pre-existing
unused-import warnings in course-detail-view.tsx and document-detail-view.tsx.
Ruff and relevant strict mypy passed:

```powershell
.venv\Scripts\python.exe -m ruff check src/aaos/rag/inspection.py src/aaos/web/inspection.py src/aaos/rag/service.py src/aaos/web/contracts.py src/aaos/web/routes.py tests/test_rag_service.py
.venv\Scripts\python.exe -m mypy src/aaos/rag/inspection.py src/aaos/web/inspection.py src/aaos/rag/service.py src/aaos/web/contracts.py src/aaos/web/routes.py
.venv\Scripts\python.exe scripts/export_openapi.py
# From frontend:
npx openapi-typescript openapi/aaos-v1.json -o types/api.generated.ts
```

Backend tests ran outside the sandbox to avoid the previously isolated Windows asyncio
socketpair initialization hang. Builds ran outside it to avoid the SWC path canonicalization
access failure. Tests used disposable/fake services. No checks were skipped or weakened
to force a pass. The prior claim-ID label assertion was replaced with the explicit
source-context/fallback UI expectation, preserving source, version, and focus assertions.

Browser checks used installed headless Edge, a local production frontend on port 3107,
and intercepted synthetic API responses. English and Arabic each passed desktop
1440×1000, tablet 1024×900, and mobile 390×844: correct original highlights, LTR English
inside Arabic UI, RTL Arabic excerpts with English terms/math, active citation switching,
non-modal answer interaction, modal focus containment, Escape/Close/focus return, and no
horizontal overflow. Captures were taken after sheet animations settled and visually
inspected. No live model/source request or production deployment was tested.

Coverage includes normalized source matching, original offsets/emoji/combining marks,
line wrapping/hyphenation, ambiguous repeated passages, math-safe no-match, truncated
prefixes, old/malformed metadata, source version mismatch/unavailability, original access
controls, post-verification capture, rejected claims, request isolation, and JSON/SSE
projection equivalence. Full repository/protected evaluation suites were not run.

## Files changed

- `frontend/components/aaos/ask-result-cards.tsx`
- `frontend/components/aaos/ask-view.tsx`
- `frontend/components/aaos/citation-evidence.tsx`
- `frontend/lib/api/ask.ts`
- `frontend/lib/ask/evidence-alignment.ts`
- `frontend/lib/i18n/messages-ar.ts`
- `frontend/openapi/aaos-v1.json`
- `frontend/types/api.generated.ts`
- `frontend/tests/citation-evidence.test.tsx`
- `frontend/tests/evidence-alignment.test.ts`
- `frontend/tests/evidence-inspector-v2.test.tsx`
- `src/aaos/rag/inspection.py`
- `src/aaos/rag/service.py`
- `src/aaos/web/contracts.py`
- `src/aaos/web/inspection.py`
- `src/aaos/web/routes.py`
- `tests/test_rag_service.py`

This report is also part of the feature commit.

## Manual verification

1. From `C:\Users\aljaf\aaos`, run `.\scripts\start-aaos.ps1` with your existing
   data-directory configuration and default local model available. Open
   `http://localhost:3000/ask` in a wide desktop window (at least 1200px).
2. Submit a fresh read-only question about a specific indexed lecture. Confirm streaming
   stages and the final answer still work. Older responses lack the new optional metadata.
3. Select a source: the answer stays readable and interactive beside its source. Check
   title/version/page or unit/course; inspect the selected claim when supplied, the
   retrieved-passage label, and original-text highlighting. Switch citations and confirm
   the active reference and original document/version follow the selection.
4. Expand Surrounding source context to see headers and extra text; collapse it again.
   Open original document and verify its PDF page anchor (or structural file action).
5. Switch the UI to Arabic. English source text should remain LTR and left/start aligned;
   Arabic source text should remain RTL. Try mixed technical terms, numerals, and math.
6. At tablet/mobile widths, confirm a full-width modal opens, Tab stays inside, Close and
   Escape return to the answer, and focus returns to the selected source. On desktop,
   confirm you can focus/use the answer and switch references while the pane is open.
7. Test an older citation or unavailable source. Expect context/fallback or unavailable
   messaging, without a fabricated highlight or changed answer verification status.
8. New conversation should clear the previous inspector. Stop waiting/retry still behaves
   as in the streaming feature. Use read-only questions for these manual checks.

## Limitations and Recommended Follow-Up

- No claim-minimal NLI support-span guarantee. Highlights locate actual retrieved source
  contributions, with explicit fallback; no synthetic sentence association is attempted.
- Ambiguous bindings, unsupported inspection paths, old conversations, conservative
  normalization, or passages outside a truncated source prefix may have no highlight.
- Source/excerpt caps intentionally trade completeness for bounded work. Open the original
  for full context; Word files have no reliable page anchor.
- Live-model quality/latency and real-data end-to-end behavior remain untested here.
- Recommended next action: run the read-only manual flow with a fresh lecture question.
  Separately, Impeccable reports legacy PRODUCT.md metadata; its optional `init` workflow
  can refresh that context. No product-context rewrite was included in this feature.
