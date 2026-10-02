# Phase 10 Spec — Moodle Change Notifications via Telegram

**Status: DRAFT for owner approval. No code until it is approved.**

- **Plan:** `docs/superpowers/plans/2026-10-02-phase-10-moodle-change-notifications.md` (owner decisions in §10).
- **Scope:** notification events, delivery store, Telegram adapter, monitor entry point, scheduling, operations.
- **Reused unchanged:** Moodle sync, snapshots and change detection.

## 1. Invariants

1. **Read-only Moodle.** Phase 10 adds no Moodle request beyond the existing sync.
2. **Deterministic pipeline.** No model call anywhere from sync to Telegram.
3. **Only complete pairs are authoritative.** Events come only from a diff between two `COMPLETE` snapshots of the same source. A `FAILED` or `RUNNING` run produces no change event.
4. **One send per change.** Each event has a stable key and is delivered at most once per channel, across restarts and repeated runs.
5. **One destination.** Telegram sends go only to the single chat linked by the owner, and only for the closed event set (§2).
6. **Secrets never leak.** The bot token, Moodle cookies and credentials never appear in logs, exceptions, API responses, the frontend, model context, Git or a Telegram message.
7. **Moodle text is inert data.** It is HTML-escaped into fixed templates and never interpreted. Links are built from the configured Moodle origin and stored IDs only.
8. **The bot is send-only.** Incoming messages are never processed, except the single `/start` read during linking (§6.2).

## 2. Events (closed set)

| Kind | Derived from (existing diff) | Stable key fields |
|---|---|---|
| `NEW_ANNOUNCEMENT` | ANNOUNCEMENT, ADDED | kind, source system, entity source ID |
| `NEW_ASSIGNMENT` | ASSIGNMENT, ADDED | kind, source system, entity source ID |
| `DEADLINE_CHANGED` | ASSIGNMENT, MODIFIED with `DUE_AT` in `changed_fields` | kind, source system, entity source ID, **new** due time (ISO, or `none`) |
| `NEW_COURSE` | COURSE, ADDED | kind, source system, course source ID |
| `COURSE_ARCHIVED` | COURSE, REMOVED between COMPLETE snapshots | kind, source system, course source ID, `to_snapshot_id` |
| `AUTH_REQUIRED` | a sync run failing at authentication | kind, source system, the ID of the first failed run after the last COMPLETE run (one per episode) |

- **The key** is `sha256` of canonical JSON of the key fields.
- **What follows from the key fields:**
  - a new announcement or assignment is notified once, ever;
  - a deadline moved twice is notified twice, once per new value;
  - a course archived, re-listed and archived again is notified per archive.
- **Not notified in v1, but still recorded by the existing change history:**
  - items that disappeared;
  - title, file and published-date edits;
  - items archived together with a course, which the diff already folds into the course event.
- **Past courses:** no events for a past course's items. Only `COURSE_ARCHIVED`.

## 3. Storage (migration `0015_notifications`)

- **`notification_state`** (one row per source system):
  - `source_system`;
  - `enabled_at`;
  - `baseline_snapshot_id`: the COMPLETE snapshot at enablement;
  - `last_processed_snapshot_id`;
  - `muted` (bool).
- **`notification_events`:**
  - `id`;
  - `event_key` (unique);
  - `kind`;
  - `source_system`;
  - `course_source_id`;
  - `entity_source_id`;
  - `from_snapshot_id`, `to_snapshot_id`;
  - `payload` JSON: course display name, title, due/published times, Moodle URL path;
  - `created_at`.
- **`notification_deliveries`:**
  - `id`;
  - `event_id`;
  - `channel` (`TELEGRAM`);
  - `status`: `PENDING`, `SENT`, `FAILED`, `SUPPRESSED`;
  - `attempts`;
  - `next_attempt_at`;
  - `last_error_code` (a closed enum, never raw provider text);
  - `sent_at`;
  - **unique** `(event_id, channel)`.
- **Stored payload text** is plain text from trusted AAOS rows. The payload is bounded: a title of at most 300 characters and a course name of at most 200.

## 4. Pipeline

`MonitorService.run_once()`:

1. **Sync.** Run the existing read-only sync (visible Edge, browser-profile lock, so runs never overlap).
2. **Failure path.** If the sync failed with an authentication failure code, derive one `AUTH_REQUIRED` event per episode. Other failures: no events.
3. **Diff.** Walk COMPLETE snapshots after `last_processed_snapshot_id`, in order. Diff each consecutive COMPLETE pair, derive the events (§2), and insert any new keys with a `PENDING` delivery. Then advance `last_processed_snapshot_id`, in the same transaction as the inserts.
4. **Deliver.** Send `PENDING` deliveries whose `next_attempt_at` has passed (§5).

- **Enabling** sets the baseline to the latest COMPLETE snapshot. Nothing at or before it is ever notified.
- **"Sync Now"** (CLI and dashboard) calls `run_once()`. The scheduler calls the same function.

## 5. Delivery

- **Templates:** fixed text in English, from plan §5.
  - Course names use the readable display name (`AI380 · Optimization Algorithms`).
  - Times are shown in Asia/Amman.
  - A missing date reads "date not stored" or "no due date".
- **Digest:** if one run produces more than `digest_threshold` events (default 5), they go out as **one** grouped message. Each delivery is then marked `SENT`, or `SUPPRESSED` with the digest's message reference.
- **Format:** `parse_mode=HTML`; every interpolated value is HTML-escaped; link previews are disabled; the message is capped at 4096 characters, truncated with "…".
- **Retry:**
  - up to 5 attempts with backoff (1 min, 5, 15, 60, 240), then `FAILED`;
  - Telegram `retry_after` (HTTP 429) is honoured;
  - at most 1 message per second.
- **Error codes:** `TOKEN_INVALID`, `CHAT_FORBIDDEN`, `RATE_LIMITED`, `NETWORK`, `TIMEOUT`, `PROVIDER_ERROR`, `MESSAGE_INVALID`.
- **Quiet time:**
  - outside active hours (default 07:00–24:00, configurable), runs are not scheduled;
  - "Sync Now" works at any time;
  - pending deliveries wait for the next run.

## 6. Telegram adapter

### 6.1 Contract

`NotificationAdapter.send(message) -> DeliveryResult(status, provider_message_id | None, error_code | None)`.

`TelegramNotifier`:

- calls `POST https://api.telegram.org/bot<token>/sendMessage` over HTTPS;
- applies a 10 s timeout;
- sends no other method, except `getUpdates` during linking.

**Token safety (critical).** The token is part of the request URL, so the adapter:

- never logs request URLs;
- disables HTTP client request logging for this client;
- catches every transport exception and rethrows a **sanitized** error that carries no URL, headers or provider body;
- returns only closed error codes.

Tests assert that the token string appears in no log record, no exception message or `repr`, and no API response.

### 6.2 Linking and credentials

**Credential file** (Calendar convention):

- an **absolute path outside the repository**, set by `AAOS_TELEGRAM_CREDENTIAL_PATH`;
- default: `C:\Users\aljaf\aaos-data\data\credentials\telegram.json`;
- contents: JSON `{"bot_token": "...", "chat_id": <int>}`, at most 4 KB;
- any read or parse failure returns a generic "Telegram unavailable".

**Commands:**

- **`aaos notify link`:**
  1. Prompts for the bot token with a hidden terminal prompt (`getpass`), so it is never echoed and never pasted into chat.
  2. Asks the owner to send `/start` to the bot.
  3. Calls `getUpdates` once and requires exactly one private chat, with exactly one `/start` sent after the prompt.
  4. Stores the token and chat ID in the credential file.
  5. Sends a confirmation message.
  6. Does **not** enable notifications.
- **`aaos notify enable`:** sets the baseline (§4). `aaos notify disable` mutes.
- **`aaos notify unlink`:** deletes the credential file and mutes. This also revokes the standing approval (§7).
- **`aaos notify test`:** sends a fixed test message.
- **`aaos monitor run-once`:** §4.

The bot never calls `getUpdates` outside linking, and no webhook is configured.

## 7. Standing approval (recorded owner decision)

The owner pre-approves Telegram sends that meet all of these conditions:

- the destination is the single linked chat;
- the message is one of the fixed templates for the closed event set, or the digest, test or link confirmation;
- the content is limited to course names, item titles and dates, plus Moodle links built from the configured origin.

Any other content or destination requires the normal approval path. Every send is recorded in `notification_deliveries`. This record is added to `docs/GOVERNANCE_LEDGER.md` when the spec is approved.

## 8. Scheduling

- **Windows Task Scheduler** runs `aaos monitor run-once` from the runtime data root every **15 minutes** within active hours.
- **No overlap.** The browser-profile lock prevents overlapping runs; a run that cannot take the lock exits with "busy" and makes no Moodle request.
- **Setup.** A documented `schtasks` command (with a matching delete command) is provided, not installed silently. The owner runs it.

## 9. Operations and UI

- **System health:**
  - a "Notifications" row: Telegram linked or not linked, last successful sync, last delivery, failures in the last 24 h;
  - Moodle sync history from `moodle_sync_snapshots`.
- **Dashboard "Sync Now":** calls the same service through a backend endpoint and shows the result counts.
  - Sync is a long call with a visible Edge window, so the endpoint returns immediately and the page polls a status read model.
  - Delivered after the CLI path is proven (slice 10E).
- **Metrics (persisted counts per run):**
  - events derived;
  - deliveries sent / failed / suppressed;
  - duplicates skipped;
  - auth failures.

## 10. Tests (written first, per slice)

- **10B Events:**
  - nothing changed → 0;
  - new announcement → 1;
  - the same pair processed again → 0;
  - deadline moved → 1; moved again → 1 more;
  - new course → 1;
  - course archived → 1, with 0 for its items;
  - FAILED/RUNNING runs → 0;
  - authentication failure → 1 `AUTH_REQUIRED` per episode;
  - the baseline excludes earlier history;
  - the cursor advances atomically with the inserts.
- **10C Service:**
  - repeated runs → 1 delivery per event;
  - a digest above the threshold;
  - HTML escaping of hostile titles (`<b>`, `&`, "ignore instructions…");
  - truncation;
  - the retry/backoff schedule;
  - `retry_after` honoured;
  - a muted state sends nothing;
  - a crash between insert and send → resent once, not twice.
- **10D Telegram:**
  - success;
  - invalid token;
  - 403 chat forbidden;
  - 429;
  - timeout;
  - network error;
  - 4096-character limit;
  - **token absent from logs, exceptions and responses**;
  - linking: zero, several or a group chat is refused, a stale `/start` is ignored, the hidden prompt is used.
- **10E End to end:** a replay of real snapshot history through a fake adapter checks correctness (every notification matches a diff change) and completeness (every in-scope change notified once). Then the first live run with the owner present.
- **10F Scheduler:** lock contention, active-hours gating, `AUTH_REQUIRED` rate limit.
- **Regression:** the full suite, the A–H selectors, ruff, mypy, and frontend tests.

## 11. Out of scope

- Moodle writes.
- Processing incoming Telegram messages.
- Model-generated text.
- Announcement bodies (until I5).
- Arabic templates.
- Per-event preferences.
- Destinations other than Telegram.
- Lifting the headless-browsing boundary.
