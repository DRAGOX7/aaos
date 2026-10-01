# Phase I3: Document and Course Summaries — Implementation Plan

**Spec:** `docs/superpowers/specs/2026-10-01-phase-i-bilingual-routing-summaries.md` (Summaries section)
**Owner decision (2026-10-01):** the reduce step is a deterministic selection of verified section points, not a model call (spec amendment recorded below).

## Reuse, not a parallel pipeline

Every summary point is produced and checked by existing Phase 4 components:

1. `RagRepository.list_chunks(identity, document_version_id)` gives the document's chunks in reading order under a pinned `HistoricalScope` snapshot.
2. Sections are consecutive chunk groups; `EvidenceSelector.select` materializes each section into authoritative, scope-checked `RetrievedEvidence` (its existing 6-chunk / 2,400-token bounds size a section).
3. `GroundedAnswerGenerator.generate` writes cited points for one section in the requested language.
4. Every point is checked with the existing `IndependentClaimEntailmentVerifier` (pinned multilingual NLI) against its cited evidence; unentailed points are dropped and counted. Production's atomic RAG path does not configure this per-claim verifier, so the summarizer owns one.
5. The final summary is one combined `GroundedModelOutput` validated by the existing `CitationValidator` against the union of cited evidence, then rendered by `render_rag_answer`. The result is a normal `RenderedRagResponse`, so the Ask UI shows its citations unchanged.

## Deterministic reduce

Up to 12 points, spread evenly across sections in reading order (each section's quota = ceil(12 / sections), filled in order, total capped at 12). No model call; every point keeps its original citation.

## Course summary

The course (with its Moodle section siblings) supplies at most 8 indexed document versions. Each needs a document summary (cached or computed within budget). Points are taken across documents in order (quota = ceil(12 / documents)), each still citing its own evidence, and validated as one combined answer.

## Cache (Alembic `0013_rag_document_summaries`)

Table `rag_document_summaries`: document version, RAG generation, content revision, summary policy version, model identifier, language, status (`COMPLETE` only is cached), section counts, dropped-point count, and the points as `(text, chunk keys)`. Unique on the identity tuple. On read, chunk keys are re-materialized under a fresh snapshot and the points re-validated by `CitationValidator`; any identity mismatch is a cache miss. No rendered text is trusted from storage.

## Budget and precompute

An Ask request computes a missing summary only while it stays inside the existing 300-second request deadline (checked before each section); otherwise it returns an honest "summary is not prepared yet" with the CLI command. `aaos rag summarize --document-version-id N [--language en|ar]` and `aaos rag summarize --all [--language en|ar]` precompute and cache.

**As built:** long chunks overflow the pinned NLI model's 512-token window, so points are verified by `WindowedClaimVerifier`, which splits an overflowing premise into overlapping halves (bounded depth) and still fails closed when a claim alone overflows. Measured on the real SE440 syllabus (2 sections), a cold summary took about 4m47s, so a section only starts with 150 s left. A routed Ask summary runs inside the `rag.answer` tool's existing 120 s timeout with a 100 s summary budget; in practice Ask serves cached summaries and reports `SUMMARY_NOT_PREPARED` otherwise, so summaries are precomputed with the CLI.

## Routing

`DOCUMENT_SUMMARY` and `COURSE_SUMMARY` route to a new read-only `rag.summarize` capability on its own router-selected registry surface (default surface unchanged). Document choice is deterministic: the single indexed document of the resolved course, or the best title match within scope; ties or no match return a typed refusal listing nothing guessed. Output is returned as trusted RAG output, like `rag.answer`.

**As built (smaller than planned):** instead of a new `rag.summarize` tool, the router selects the existing RAG-only `rag.answer` surface in a summary mode set only by the trusted router. This reuses the trusted Phase 4 output, citation, and provenance path unchanged, and adds no new tool name, runtime-policy entry, or sanitizer allowlist. Failures are typed: `SUMMARY_NOT_PREPARED`, `SUMMARY_UNVERIFIED`, `AMBIGUOUS_DOCUMENT`, `NO_INDEXED_DOCUMENTS`, `RESULT_TOO_LARGE` (more than 8 course documents), with fixed UI guidance. Without a configured summarizer, summary questions keep using the model loop.

## Steps (RED → GREEN each)

1. Migration `0013`, ORM model, repository get/put for summaries.
2. `DocumentSummarizer` (sections, map via existing components, NLI per point, deterministic reduce, budget, cache read/write with re-validation).
3. Course summaries over cached/computed document summaries.
4. CLI `aaos rag summarize`.
5. Summary mode on `rag.answer` (router-selected), document resolution, routing, wiring in the API composition.
6. Full suite, frozen A–H selectors, live verification on the real documents (SE440 syllabus within the Ask budget; the 17,500-token ML notes via CLI precompute).
