# README diagrams (Eraser)

The README embeds six PNG diagrams from this folder. They are drawn with
Eraser and exported as PNG (light theme, ~1600 px wide). Each section below is
the exact content to draw; keep labels short and use the same names as the
README. Diagrams show structure only: no credentials, hostnames, file paths
from a personal machine, or real course data.

## 1. `aaos-architecture.png` (hero: system context)

Cloud/architecture diagram, left to right.

- **Student** (person) uses **AAOS Dashboard** (Next.js) in the browser.
- **AAOS on the student's PC** (group/boundary), containing:
  - Dashboard → **AAOS API** (FastAPI)
  - API → **Orchestrator** → **Typed tools**
  - Orchestrator ↔ **LM Studio** (local LLM, qwen3.5-4b): label "proposes only"
  - Typed tools → **RAG** (BGE-M3 + Chroma + FTS, NLI check)
  - All → **SQLite** (canonical state) and **Document store** (SHA-256)
  - **Moodle connector** (Playwright + Edge profile)
  - **Monitor** (scheduled every 15 min)
- Outside the boundary:
  - **University Moodle**: arrow from Moodle connector, label "read-only".
  - **Microsoft sign-in**: dashed arrow, label "remembered session only".
  - **Telegram**: arrow from Monitor, label "send-only, one chat".
  - **Google Calendar**: arrow from Typed tools, label "after human approval".
- Footnote: "The model proposes; trusted code validates, authorizes and executes."

## 2. `aaos-components.png` (component architecture)

Layered diagram, top to bottom:

1. **Dashboard** (Next.js 16, React 19): Overview, Courses, Documents (PDF viewer), Ask, Tasks, Approvals, Activity, System health.
2. **API** `/api/v1` (FastAPI): read routes; the only writes are Ask, RAG query, Approve, Reject.
3. **Orchestrator**: Intent router (EN/AR) → Typed tools; otherwise Agent loop (model proposal → schema validation → permission policy → tool).
4. **Capabilities** (side by side): RAG (retrieve → generate → NLI verify) · Academic reads · Study tasks · Calendar proposals · Moodle changes.
5. **Domain services**: Documents (download, extract, provenance) · Moodle sync (complete snapshots) · Approval core (durable records) · Notifications (events, outbox, Telegram).
6. **Storage**: SQLite + Alembic · Chroma vectors · SQLite FTS · Content-addressed files.

Side box: **Local models**: LM Studio (chat), BGE-M3 (embeddings), mDeBERTa (NLI).

## 3. `aaos-ask-flow.png` (sequence diagram)

Participants: Student, Dashboard, API, Orchestrator, Router, Agent loop, LM Studio, Typed tools, RAG, NLI.

1. Student → Dashboard: question (EN or AR)
2. Dashboard → API: `POST /ask` (+ request ID)
3. API → Orchestrator: ask(question, conversation)
4. Orchestrator: resolve context (follow-up, course, past-course focus)
5. alt **known intent**: Orchestrator → Router → Typed tools → result
6. else: Orchestrator → Agent loop → LM Studio: JSON proposal → Agent loop: validate schema + permission → Typed tools
7. opt **document question**: Typed tools → RAG: retrieve passages → LM Studio: answer with evidence IDs → NLI: verify each claim → keep supported claims
8. Orchestrator → API → Dashboard: answer + sources, or "not enough evidence"

## 4. `aaos-sync-notifications.png` (flow chart)

1. **Windows Task Scheduler** (every 15 min, 07:00–24:00) → `aaos monitor run-once`
2. Open dedicated **Edge profile** → on Moodle login page? → follow Moodle's own "Log in with Microsoft" link once → wait for course list (90 s unattended)
   - timeout → one **"Moodle login needed"** Telegram alert per episode → stop
3. **Read-only sync** → snapshot complete?
   - no → record failure, send nothing → stop
4. **Diff** previous complete snapshot vs new one → events with stable keys
5. **Outbox**: de-duplicate, baseline at enable, digest if more than 5, retry with backoff
6. **Telegram** (send-only, linked chat) → student's phone

## 5. `aaos-approval-flow.png` (state diagram)

States: Proposed → Pending approval → Approved / Rejected; Approved → Claimed → Executing → Succeeded / Failed / **Unknown outcome** → Reconciled.

Notes:
- "Repeated or opposite decision → rejected (not actionable)."
- "Lost provider response → Unknown outcome, never shown as success."
- "Every transition is a durable record."

## 6. `aaos-phases.png` (timeline)

Horizontal timeline, one milestone per phase, with a status colour
(Frozen = solid, Implemented = outlined, Unresolved/Partial = hatched):

| Phase | Label | Status |
|---|---|---|
| 0 | Foundation | Acceptance unresolved |
| 1 | Domain model + mock portal | Frozen |
| 2 | Moodle, read-only | Frozen |
| 3 | Documents | Frozen |
| 4 | Local RAG | Frozen |
| 5 | Agent orchestrator | Partial, corrective review |
| 6 | Approvals + Google Calendar | Frozen (6B) |
| 7 | Dashboard | Frozen |
| 8 | Vision + restricted computation | Frozen |
| 9 | Release qualification | Frozen |
| 10 | Telegram notifications | Implemented |
