# Phase I: Bilingual Intent Routing, Summaries, and Announcements

**Status:** HUMAN_APPROVED specification (project owner, 2026-10-01); implementation not yet started
**Baseline:** `main` at `edaac982`
**Decisions recorded 2026-10-01 (project owner):** Python intent router with model fallback; document and course summaries; announcement titles plus announcement bodies and dates where the authenticated Moodle source actually provides them (amendment 2026-10-01, replacing "titles only"); read-only Moodle access for the live slice after a confirmation immediately before the first live run.

## Objective and authority

Make `/api/v1/ask` reliably reach the right existing or new read capability for English and Arabic questions about documents, summaries, announcements, Moodle changes, and course/assignment facts.

Phase I adds no write authority. It does not change Moodle, StudyTask, Calendar, approval, or credential boundaries. Every capability it routes to is read-only, except routes into the existing explicit StudyTask/Calendar paths, which keep their current preconditions. Retrieved text, announcement titles, and model output remain untrusted data.

## Evidence that motivates this phase (measured 2026-09-30 / 2026-10-01)

- The 4B decision model (`qwen/qwen3.5-4b`) sometimes answers a document question with the course record instead of calling `rag.answer` (observed in English and consistently in Arabic: list_courses → get_course → list_assignments → ANSWER course record).
- Every deterministic intent pattern in the agent (`_PLAN_INTENT`, `_CHANGE_*_INTENT`, `_EXPLICIT_WRITE_INTENT`, `_DOCUMENT_INTENT`) is English-only. Arabic questions always fall through to model tool choice, and the deterministic mixed plan+write preflight does not recognize Arabic write requests.
- Arabic retrieval relies on the dense half only: keyword search found 2 hits for an Arabic question vs 30 for its English equivalent, because the indexed documents are English.
- No summarization capability exists; RAG answers from at most 6 excerpts (about 2,400 tokens). The largest indexed document is about 17,500 tokens, over the model's 8,192-token context.
- 22 announcements exist (all HSS100 section 1) with Arabic titles only; no body text or dates were captured by sync. No agent tool or API exposes them.
- 0 Moodle sync snapshots and 0 assignments exist in the runtime database, so "what changed" cannot answer yet.
- A course with no indexed documents is reported as "The document search failed" (NO_ELIGIBLE_DOCUMENTS is flattened to RAG_QUERY_FAILED).

## Trusted intent router

A deterministic Python router runs after context resolution and the existing unsupported-Calendar and study-planning checks, and before the model tool loop. Study planning (Phase G) is the accepted precedent for Python-owned routing.

- **Closed intents:** `DOCUMENT_QUESTION`, `DOCUMENT_SUMMARY`, `COURSE_SUMMARY`, `ANNOUNCEMENTS`, `MOODLE_CHANGES`, `COURSE_FACTS` (courses), `ASSIGNMENT_FACTS`, and `UNROUTED`.
- **Bilingual patterns:** English and Arabic patterns per intent. Arabic text is normalized for matching only (remove diacritics and tatweel; unify alef forms, alef maqsura/yaa, taa marbuta/haa); the original question is passed on unchanged.
- **Routing rule:** exactly one high-confidence intent routes directly in Python to its capability. Zero intents, several conflicting intents, or any write intent goes to the existing model loop and preflights unchanged (`UNROUTED`). The router never selects a write capability.
- **Scope:** courses are resolved from the question with the existing trusted rules (exact title, course-code prefix, section-sibling fallback for document questions only); documents by exact or normalized title within the resolved scope. Ambiguity returns the existing typed ambiguity refusal, never a guess.
- **Bilingual safety preflight:** the explicit-write and mixed plan+write detectors gain Arabic patterns so Arabic write requests receive the same early refusal as English ones.
- **Honest empty scope:** NO_ELIGIBLE_DOCUMENTS becomes a distinct typed result ("This course has no indexed documents"), not a generic search failure.
- **Provenance:** routed answers carry the same sources/provenance as model-routed ones, plus the routed intent in trusted metadata for observability.

## Announcements

### Read capability

- New read capability `academic.list_announcements`: course-scoped (resolved as above), optional normalized keyword filter over titles and stored bodies, bounded to 50 records. Order is newest-first by `published_at` when stored, otherwise by stable id; the order basis is reported.
- Deterministic rendering only (no generation): course, title, date when stored, a bounded body excerpt when stored, and the source reference. Titles and bodies are untrusted text, rendered inert as plain text, never instructions, links, or HTML.
- When a body or date is not stored, the answer says so explicitly; it never infers either.

### Bodies and dates from the authenticated source (amendment 2026-10-01)

Today sync stores titles only (the connector reads the approved forum list document `/current/mod/forum/view.php?id=…`). Bodies and dates are added only where the authenticated Moodle source actually provides them:

1. **Verify before implementing.** In an owner-confirmed, read-only live run, record which forum data the authenticated session exposes: first API-first (the `mod_forum_*` web-service functions the existing site-info gate reports as available to this session), otherwise fields present in already-approved or minimally extended read-only forum documents. Record the result, with sanitized fixtures containing no credentials, cookies, tokens, or session identifiers, in the slice evidence.
2. **Implement only the verified path.** Extend the read-only connector, its request allowlist, and its parser minimally for that path; no Moodle write, no new authority, no arbitrary URL following.
3. **Persist into existing columns.** `announcements.body` (sanitized plain text, bounded length) and `announcements.published_at` (timezone-aware, from the source's own timestamp) already exist; no migration is needed for this part. Fields the source does not provide stay NULL.
4. **Snapshot and diff safety.** Phase E snapshot completeness and deterministic diff rules are unchanged; if announcement body/date become compared fields, they are typed, deterministic, and an incomplete sync never produces false removals or false changes.

This modifies the accepted Phase 2 read-only connector under explicit owner authorization (2026-10-01). Live Moodle behavior is verified, not assumed; no undocumented endpoint, capability, permission, or response schema is relied on without recorded evidence.

## Summaries

- **Document summary:** map-reduce over the document's indexed chunks in reading order. Map calls receive bounded chunk groups (target ≤ 1,800 tokens) and return structured, evidence-cited points. The reduce call merges points into a bounded summary (≤ 12 points). Every point must cite request-local evidence IDs and pass the existing citation validation and claim-entailment verification; unverified points are dropped and the drop is disclosed.
- **Course summary:** reduce over the per-document summaries of the course's indexed documents (bounded to 8 documents); requires those document summaries to exist.
- **Cache:** verified document summaries are persisted per (document version, RAG generation, content revision, summary policy version, model identifier) in a new table (Alembic `0013`), so repeated and course summaries do not recompute. Any identity change invalidates the cached summary.
- **Budget:** an Ask request computes a missing document summary only if it fits within the existing 300-second request deadline; otherwise it returns an honest "summary not prepared yet" result. A CLI command (`aaos rag summarize ...`) precomputes summaries explicitly.
- **Language:** the summary is written in the question's language (English or Arabic) while citing English source text; verification uses the existing multilingual entailment model.

## Arabic retrieval quality

Measured slice. Build a frozen bilingual question set over the real indexed documents first, measure answer/refusal/citation outcomes for English vs Arabic, then decide between (a) dense-only retrieval for Arabic queries with adjusted sufficiency handling, or (b) a bounded model-produced English search-term rewrite used only for the keyword half (untrusted, never shown, never instructions). The choice is recorded with before/after evidence.

## Moodle changes

- Route bilingual "what changed" questions to the existing `moodle.list_changes` capability.
- In I5, after confirming with the project owner immediately before the first live run, run read-only Moodle syncs into the runtime database (outside the repository), keeping the Moodle auth state where it is, and verify English and Arabic change questions end to end. Snapshot safety (incomplete sync is never authoritative) is unchanged.

## Slices and acceptance

1. **I1 Router:** intent router, Arabic normalization, bilingual write preflight, honest empty-scope result, routes to existing capabilities.
2. **I2 Announcements read capability:** list/search with deterministic rendering of title, and of body/date when stored; routing.
3. **I3 Summaries:** document and course summaries, cache (`0013`), CLI precompute.
4. **I4 Arabic retrieval:** measured decision and implementation.
5. **I5 Live Moodle (read-only, owner-confirmed):** verify the forum data the authenticated source provides; capture announcement bodies/dates on the verified path; run syncs into the runtime database; verify announcement and change questions in English and Arabic end to end.

Each slice: RED → GREEN tests; full backend suite green; frontend checks when the frontend changes; frozen A–H evaluation selectors still 100/100; ruff and mypy clean on changed files; live verification on the runtime data; one reviewable commit series per slice with human approval to merge.

**Routing evaluation:** before I1 is implemented, freeze a bilingual routing manifest (at least 8 English and 8 Arabic cases per routed intent, plus `UNROUTED` and write-intent cases) under `tests/evaluation/`. The deterministic router must classify every manifest case correctly; live model behavior for `UNROUTED` cases is reported, not gated. The protected Phase 5 V4 held-out material is not accessed.

## Non-goals

Moodle writes; announcement fields the authenticated source does not provide (never inferred); Calendar update/delete; new write capabilities; replacing the model loop for unrouted questions; changing frozen Phase 4 retrieval contracts beyond what I3/I4 explicitly specify; Phase 10/RLM work.
