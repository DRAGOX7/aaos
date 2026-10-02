# Phase 10 — Moodle Change Notifications (Telegram) — Plan

**Status: PROPOSED. Not authorized. No code written.**

- **Source:** the owner's proposed design (2026-10-02), reviewed against the repository and revised here.
- **Before any implementation:**
  1. The owner answers the open decisions (§9).
  2. A Phase 10 spec is written and approved.
- **Phase discipline:** Phase 0 acceptance is unresolved and Phase 5 needs a corrective review (`docs/PHASES.md`). Starting Phase 10 ahead of those gates is an explicit owner decision.

## 1. Goal

AAOS periodically runs its existing read-only Moodle sync. It detects what changed since the previous **complete** sync and sends a short Telegram message for each meaningful change: new announcement, new assignment, deadline changed, new course, course archived.

The pipeline is **deterministic**. No language model is in the path.

## 2. What the proposed design gets right (kept)

- **Telegram is one adapter** behind a `NotificationService`. Moodle code never calls Telegram. Other destinations can be added later without touching sync.
- **Manual "Sync Now" comes first.** The scheduler only invokes the same service; there is never a second sync path.
- **Notifications are short.** Moodle content stays in AAOS and Moodle.
- **Secrets stay out:** Moodle credentials and the bot token never reach the model, logs, frontend, Git or Telegram.
- **Moodle text is data.** An announcement saying "ignore your instructions" is just text in a message.
- **Archiving a course is one event,** not one per file or announcement.
- **Failures are controlled.** An incomplete or failed sync is never treated as "everything was removed". An expired login becomes a single, rate-limited "login needed" alert.
- **It is auditable,** with sync and notification history and failure-path tests.

## 3. Revisions to the proposed design

| Proposed | Repository reality | Revision |
|---|---|---|
| Phase 1 "Harden Moodle sync", Phase 2 "Change detection" | Both exist and are accepted: the read-only sync (Phase 2), `moodle_sync_snapshots` with RUNNING/COMPLETE/FAILED and failure codes, `diff_moodle_snapshots`, `MoodleChangeReadService`, and archived courses (`0014`) with archived items collapsed into a count | **Reuse them; do not rebuild.** Phase 10 starts at the notification layer. |
| New `sync_runs` table | `moodle_sync_snapshots` already records each run's status, timing and failure code | **Extend it** with an `AUTH_REQUIRED` failure code if missing; no second history table. |
| Phases numbered 0–7 | AAOS phases 0–9 already exist | This is **Phase 10**, with slices 10A–10F. |
| De-duplicate on the change ID | `change_id` is `"{from}-{to}-{index}"`: specific to one pair of syncs, not stable | **Use a stable event key** built from the change itself: `entity_type`, `entity_source_id`, the event kind, and the new value where relevant (for example the new due date). The same change is notified once, however many syncs see it. A deadline moved twice gives two events. |
| Event names NEW / UPDATED / REMOVED / DEADLINE CHANGED / COURSE CHANGED | The detector emits ADDED/REMOVED/MODIFIED with typed `changed_fields` | **Map to a closed set:** `NEW_ANNOUNCEMENT`, `NEW_ASSIGNMENT`, `DEADLINE_CHANGED`, `NEW_COURSE`, `COURSE_ARCHIVED`, `AUTH_REQUIRED`. File changes, title edits and removals are recorded but not notified in v1. "COURSE CHANGED" is dropped as too vague. |
| "Real time" | Moodle cannot push events to AAOS; there is no web-service token | **Near real time:** a notification arrives within the check interval (for example 15 minutes) while this computer is on and awake. |
| Scheduler runs sync | The sync must use a **visible** Edge window; `browser_runtime.py` refuses headless browsing (a Phase 2 safety boundary) | **Owner decision:** accept a brief Edge window each run, or a reviewed change to that boundary. Not decided silently. |
| Bot "links/authorizes" a chat | — | **One-time linking:** `aaos notify link` reads the owner's `/start` once and stores **one allow-listed chat ID**. The bot is **send-only**: it never processes incoming messages, so it cannot become an input or prompt-injection channel. |
| Messages include body text and a "Published" time | Announcement bodies are not stored yet (I5 is paused); some dates are not stored | v1 sends **titles and stored dates only**, and says "date not stored" otherwise. Bodies arrive with I5. |
| Telegram sends are not covered by the approval rule | AGENTS.md: external writes use the approval architecture | **A recorded standing approval:** fixed templates, sent only to the owner's linked chat, for the closed event set. It can be revoked by unlinking. Every send is still logged. |
| — | Not covered by the proposed design | **No flood:** enabling notifications sets a baseline, so only changes after enablement are sent. More than N events from one sync (for example a new semester) go out as **one digest message**. |
| — | Not covered | **Telegram safety:** text sent as HTML with every Moodle-derived string escaped. "Open in Moodle" links are built from the configured Moodle origin and stored IDs, never from content. Link previews are off. |
| — | Not covered | **Delivery:** an outbox. A notification row is written before sending, then marked `SENT`/`FAILED`, with bounded retry and backoff. A unique constraint on `(event_key, channel)` prevents duplicates. Telegram rate limits are respected. |
| Scheduler location unspecified | The backend is not always running | A CLI entry `aaos monitor run-once`, scheduled by **Windows Task Scheduler**, holding the existing browser-profile lock so runs never overlap. "Sync Now" in the UI calls the same service. |

## 4. Architecture

```
Windows Task Scheduler ─┐
"Sync Now" (UI / CLI) ──┴─► MonitorService.run_once()
                              │ existing read-only sync (visible Edge, profile lock)
                              ▼
                       moodle_sync_snapshots (COMPLETE / FAILED / AUTH_REQUIRED)
                              │ only COMPLETE → COMPLETE pairs are authoritative
                              ▼
                       diff_moodle_snapshots (existing) → NotificationEvent (closed set, stable key)
                              ▼
                       notifications outbox (unique event_key + channel)
                              ▼
                       NotificationAdapter ──► TelegramNotifier (send-only, one allow-listed chat)
```

## 5. Message templates (plain, escaped, no model)

- **New announcement:**
  ```
  AAOS — New Announcement
  {course}
  {title}
  Published: {date | "date not stored"}
  [Open in Moodle]
  ```
- **New assignment:**
  ```
  AAOS — New Assignment
  {course}
  {title}
  Due: {date | "no due date"}
  [Open in Moodle]
  ```
- **Deadline changed:**
  ```
  AAOS — Deadline Changed
  {course}
  {title}
  Before: {old}
  Now: {new}
  [Open in Moodle]
  ```
- **New course:**
  ```
  AAOS — New Course
  {course}
  A new course is now available in your Moodle account.
  ```
- **Course archived:**
  ```
  AAOS — Course Archived
  {course}
  This course is no longer listed in Moodle. Its indexed content stays searchable as a past course.
  ```
- **Login needed:** "AAOS — Moodle login needed. AAOS could not sync because the session expired. Run the login once to resume." At most one per expiry episode.
- **Digest:** "AAOS — {n} changes in your Moodle", with grouped lines (when more than the threshold arrive at once).

Course names use the readable display names (`AI380 · Optimization Algorithms`). Times use Asia/Amman.

## 6. Slices (each: spec → tests first → owner acceptance)

- **10A — Contracts.**
  - The event set and stable event key.
  - The outbox schema (migration).
  - The adapter interface.
  - The standing-approval record and the threat model (credentials, token, Moodle text, chat allow-list).
- **10B — Event derivation.** From an existing COMPLETE→COMPLETE diff.
  - Deterministic tests: nothing changed → 0 events; a new announcement → 1; the same diff seen again → 0; a deadline moved → 1; a course archived → 1 with no per-item events.
  - Incomplete or failed syncs produce 0 events.
- **10C — Notification service.**
  - Outbox, de-duplication, the baseline at enablement, the digest threshold, formatting, escaping, and the retry policy.
  - No Telegram code yet; a fake adapter.
- **10D — Telegram adapter.**
  - Send-only, token from `data/credentials` (or an environment variable).
  - `aaos notify link` / `unlink` / `test`.
  - Tests: success, invalid token, API error, timeout, retry, rate limit, oversized text, **no token in logs, exceptions or responses**.
- **10E — Manual end-to-end.**
  - "Sync Now" (CLI, then a dashboard button) → sync → events → Telegram.
  - **First live run** with the owner present.
- **10F — Scheduler and operations.**
  - Task Scheduler entry, the active-hours window, the `AUTH_REQUIRED` handling.
  - Sync and notification history on the System Health page, the "last successful sync" display, and metrics: `notifications_sent/failed`, `duplicates_suppressed`, `auth_failures`, `partial_syncs`.

**Later (not v1):** per-event preferences, mute, Arabic message variants, announcement bodies (after I5), other destinations.

## 7. Acceptance gate (Phase 10)

- **Writes:** no Moodle writes. No Telegram send outside the linked chat and the closed event set.
- **Secrets:** Moodle credentials, cookies and the bot token appear in no log, frontend response, model context, Git commit or Telegram message. Verified by tests and a log scan.
- **Correctness:** over a replay of real snapshot history, every notification corresponds to a real change.
- **Completeness:** every in-scope change in that replay is notified exactly once.
- **Repetition:** repeated runs on unchanged Moodle data send nothing. A failed or incomplete run sends nothing except at most one `AUTH_REQUIRED` alert.
- **Regression:** all earlier phase gates still pass (full suite, A–H selectors).

## 8. Out of scope

- Anything sent back to Moodle.
- Processing incoming Telegram messages or commands, beyond the one-time `/start` read during linking.
- Model-written notification text.
- Notifications for past-course items other than `COURSE_ARCHIVED`.

## 9. Open decisions (owner)

1. **Check interval and active hours.** Proposed: every 15 minutes, 07:00–24:00.
2. **Visible Edge window per run,** or a reviewed change to the headless-browsing boundary.
3. **Consent to send** course, assignment and announcement titles and dates through Telegram's servers.
4. **Digest threshold.** Proposed: more than 5 events from one sync become one message.
5. **Authorization to start Phase 10** ahead of the open Phase 0 and Phase 5 gates.
