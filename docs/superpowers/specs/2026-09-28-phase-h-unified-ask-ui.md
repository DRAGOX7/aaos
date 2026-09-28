# Phase H: Unified Ask UI

## Scope and authority

Phase H integrates the accepted A–G capabilities into the existing Next.js Ask page at /ask, backed by POST /api/v1/ask. It is a separate milestone in the agentic A–H roadmap. It does not change the repository's formal Phase 7–9 status in docs/PHASES.md or authorize Phase 5 V4, deployment, or the later frozen agent security evaluation.

The browser owns presentation and page-lifetime conversation state. The backend remains authoritative for entity resolution, session context, tool execution, StudyTask writes, Moodle changes, Calendar proposals and approvals, study planning, and provenance. No Phase A–G capability is broadened.

## Assistant surface

Keep one canonical Ask experience at /ask, reusing the current AAOS app shell, navigation, colors, typography, controls, and same-origin API proxy. The conversation is the primary surface: user turns, assistant responses, typed result components, citations, a bounded composer, and clear loading/error/refusal states. Do not add another dashboard launcher or assistant panel.

The accepted desktop visual references are [the conversation concept](assets/phase-h-ask-desktop.png) and [the Calendar proposal concept](assets/phase-h-calendar-proposal.png). The existing AAOS tokens remain the styling source of truth: background #f6f9fd, white surfaces, navy text #10214b, muted slate-blue, primary blue #1677f2, teal #0b9ea8, Inter, thin blue-gray borders, and restrained rounded panels/shadows. Dynamic response content always comes from the backend; concept examples are not seeded application data.

## API and session handling

Use generated types from frontend/openapi/aaos-v1.json and frontend/types/api.generated.ts. Submit only the question, the current conversation_id when returned, and a stable UUID request_id. Keep that ID for an uncertain/timed-out retry of the same question in the same conversation; use a new UUID for a different turn. Prevent duplicate in-flight submissions in the UI without treating that as a security boundary.

Keep transcript and conversation_id in component memory only. Do not persist message text or secrets in localStorage, sessionStorage, cookies, or another browser memory store. A page refresh starts a new UI conversation because the page does not restore its session ID or transcript. The browser does not resolve references or recover trusted entities from old messages.

The Ask request may be stopped by aborting its request. This closes the browser request so the existing route can observe disconnection and signal cancellation. The UI must report an uncertain outcome accurately: a local task mutation may have committed before the response was lost, so retrying the same question reuses its request ID.

## Response rendering

Use a response-kind renderer with focused components for ordinary answers, refusals, RAG citations, Moodle change sources, StudyTask results, Calendar proposals, and study plans. Render answer text as inert text with preserved paragraphs. Do not use raw HTML or add a Markdown dependency. Link only to fixed AAOS routes built from trusted typed identifiers; never use a backend-supplied URL or source_ref as an href.

The Ask RAG provenance projection is typed at the backend boundary. Show the backend document title plus page or structural location supplied by its citation. Do not invent source names, pages, links, or identities. INSUFFICIENT_EVIDENCE remains an intentional refusal with no citations, not a red application error.

Moodle change sources may expose change kind, changed fields, snapshot IDs, and trusted before/after deadlines. Display only fields actually present. The UI never diffs text. StudyTask source cards use the persisted status, priority, due date, and linkage from the validated tool result; render them only after a successful Ask response. Other academic source references remain concise provenance.

A CalendarProposalView always begins as a proposal, not a created event. Show summary, start/end, timezone, linked source kind/ID, and current backend status. Review/reject/approve uses the existing approval endpoints and exactly the response's approval_id. After an approval command, use the returned task_id for a read-only task refresh. Show an event as created only when the trusted operation state is CONFIRMED_SUCCESS; approval by itself is not execution success. A failed or unknown execution remains visibly failed or unknown. No update/delete controls exist.

Render StudyPlanView as date-grouped recommendations, not JSON. Keep current obligations and task facts distinct from recommended items. The contract provides dates and durations but no scheduling times, so the UI must not invent clock slots. Mark estimated durations, show assumptions/warnings and the fact that calendar conflicts were not checked, and keep recommendation focus visibly separate from trusted facts. Empty plans and empty change/task results are valid results.

## Loading, errors, and refusals

Use one generic high-level loading label and an accessible live status; do not invent execution stages or expose chain-of-thought. Distinguish local input validation, backend refusal, insufficient evidence, server unavailable, network failure, client/server timeout, malformed response, cancellation/uncertain outcome, and provider execution failure. Use bounded safe messages; never expose exception text, stack traces, credentials, authorization headers, or provider payloads.

## Security and accessibility

All Moodle-controlled titles, task text, citations, model output, and error messages are display data. React text rendering keeps them inert. Keep credentials and auth state out of UI and browser persistence. Approval controls are presentation only; the backend validates all approval commands.

Use semantic headings, labeled composer, keyboard submission, visible focus, semantic buttons, sensible tab order, and polite status announcements. Color is not the sole state signal. Honor reduced-motion preferences. At desktop, narrower laptop, and tablet widths, keep cards, provenance, and plan rows within the viewport without horizontal scrolling.

## Phase boundaries and acceptance

This milestone changes frontend integration and only the narrow typed Ask response projections needed to display already-trusted RAG citations, Moodle changes, and StudyTask results. It does not add persistent memory, voice, notifications, Calendar update/delete, Moodle writes, plan execution, background autonomy, a new agent framework, V4 evaluation, or deployment.

Acceptance requires one surface to exercise normal and follow-up answers, RAG citations/refusals, StudyTask reads/actions, Moodle changes and follow-ups, pending Calendar proposal decisions, and structured plans with honest warnings. Verify frontend tests/typecheck/lint/build, canonical OpenAPI generation, the focused Ask and A–G backend regression bundle, lock/Ruff/mypy status, responsive/accessibility behavior, exact-SHA security review, and a clean committed worktree. Never perform live Calendar or Moodle writes.
