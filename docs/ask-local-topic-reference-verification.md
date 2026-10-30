# Ask local-topic reference fix — 2026-10-30

## Diagnosis

Baseline HEAD was `4bbe0b8e5116fc2e4c378b5eea9d37e6d1ae802a`
(Evidence Inspector V2). The running editable installation points at this checkout;
the backend started after that commit, exposes the stream endpoint and inspection
schema, and runs without reload. Work is isolated on
`codex/fix-ask-local-topic-reference`.

The reported fresh-conversation KNIME prompt fails identically through JSON and
SSE: HTTP 200, REFUSED, `UNRESOLVED_REFERENCE`, step count zero. SSE reports only
RECEIVED. The failing reference is the generic pronoun in **Explain it**, not a
document, course, citation, or version identifier.

Request path: frontend Ask view → stream client → Next proxy → FastAPI shared Ask
execution → assistant conversation lease → context reference resolver → intent
routing and course/source scope → tool registry → retrieval and coverage → model
generation → claim/citation verification → response projection and optional
request-local inspection metadata → terminal stream result.

The context resolver runs before retrieval. Its generic-reference regex matches
the pronoun and chooses from an empty trusted-context snapshot, producing MISSING.
The assistant maps that status to the observed refusal. An unrelated active entity
could instead be inherited incorrectly. Therefore neither citation verification nor
source PDF access is responsible. The matching regex dates to `f6165e593` on
2026-09-27; this module was unchanged between `621288d8` and `4bbe0b8e`, so the
failure predates both recent Ask implementations.

## Change and invariant

Recognize the bounded same-message form “What is <named topic>? Explain it …”
(including the existing explanatory verbs) as query text. Do not mint a trusted
record alias. Explicit IDs, ordinal/typed references, document-version references,
and deictic topics retain their existing checks. Further pronouns and detected
English/Arabic write intents fall through to normal trusted-context resolution.
All retrieval scope, access, verification and inspector code remains unchanged.

The reusable distinction is whether a pronoun names a topic already present in
this question or requires authority from a previous trusted tool observation.

## Verification

- Final isolated backend command: `.venv\Scripts\python.exe -m pytest`
  with `tests/test_ask_local_topic_reference.py`,
  `tests/test_agent_conversation_context.py`, `tests/test_agent_archived_courses.py`,
  `tests/test_ask_stream.py`, `tests/test_rag_structural_web_transport.py`,
  `tests/test_agent_academic_orchestration.py`, `tests/test_agent_intent_routing.py`,
  `tests/test_intent_router.py`, `tests/test_agent_rag_tool.py`,
  `tests/test_agent_scope.py`, and `tests/test_agent_prompt_injection.py`:
  **393 passed**. These use disposable fixtures/fake model and RAG services;
  the API regressions exercise the real resolver, assistant, routes and validated
  citation projection. They cover both transports, fresh conversations, archived
  versioned documents, valid prior references, invalid IDs, and action suffixes.
- Ruff check and format check on the changed Python files passed. Mypy on
  `src/aaos/agent/conversation_context.py` passed.
- Frontend `npm test -- --maxWorkers=2`: **367 passed / 57 files**.
  `npm run typecheck` passed. `npm run lint` passed with two existing unused
  route-parser import warnings in course/document detail views.
- An initial test command named nonexistent `tests/test_web_ask_stream.py`;
  corrected to `tests/test_ask_stream.py` before the passing runs above.
- Independent focused review found no remaining actionable findings after the
  additional action/pronoun regressions were added.

## Live runtime evidence and limits

Only bounded status/count/identifier metadata was recorded; no credentials,
document excerpts, model answers, or protected evaluation data were exposed.
The configured provider reports `chatgpt_plan`, model `gpt-6-luna`, cloud enabled.
The original running V2 backend was used, without altering runtime configuration.

`Define KNIME.` does not trigger the refusal on either endpoint. In the current
runtime it returns course-selection guidance, not a grounded definition: there
are zero READY documents in the default current-course document listing. The
archived-course specification intentionally requires explicit historical scope
rather than silently widening an unscoped question.

An actual streamed request, **What is KNIME from my previous semester?**, completed
with the configured model in 22.94 seconds: ANSWERED, two citations to document
version 4, past-course provenance, and two inspection entries. Observed stages
were RECEIVED, SEARCHING, CHECKING_COVERAGE, WRITING and VERIFYING. This establishes
that the archived indexed lecture, retrieval, model, verifier and Inspector V2
work together. It is a live baseline test, not a live test of the patched resolver.
Public document listings intentionally exclude version IDs; their absence there
does not establish missing academic records or stale source mappings.

The adjective “archived” alone did not select historical scope in a separate
probe; previous-semester wording did. This existing routing vocabulary limitation
is outside this fix. No automatic archive widening was added.

No persisted backend log was found in the checked conventional log locations;
the existing process console was unavailable. Diagnosis uses source, safe API
metadata, reproducible refusal status and isolated regressions instead.

Restart the existing backend through its normal launcher to load this commit,
then rerun the original prompt. The original non-reloading process remains
untouched, so patched live-model acceptance remains unverified. With the current
index state the original unscoped prompt should return course guidance; explicitly
select the previous semester to request the archived KNIME lecture.

No push, merge, PR, protected evaluation, database migration, frontend change,
or unrelated working-tree cleanup is part of this fix.
