# AAOS Telegram notifications — runbook

Phase 10. AAOS checks Moodle (read-only), detects changes, and messages you on Telegram.

- **Spec:** `docs/superpowers/specs/2026-10-02-phase-10-moodle-change-notifications.md`.
- **Where to run:** every command below runs in PowerShell from the **runtime data root**:

  ```powershell
  Set-Location C:\Users\aljaf\aaos-data
  $aaos = 'C:\Users\aljaf\aaos\.venv\Scripts\aaos.exe'
  ```

## 1. Create the bot (once, in Telegram)

1. In Telegram, open **@BotFather** and send `/newbot`.
2. Choose a name (for example "AAOS") and a username ending in `bot`.
3. BotFather replies with a **token**. Keep it private. Do not paste it into chats, issues or files in the repository.

## 2. Link AAOS to your chat (once)

```powershell
& $aaos notify link
```

1. The command asks for the token with a **hidden** prompt.
2. It tells you to send `/start` to your bot, and then press Enter.
3. AAOS accepts exactly one private `/start`, stores the token and chat ID in `data\credentials\telegram.json` (outside the repository), and sends "AAOS — Telegram linked".

Another path can be set with `AAOS_TELEGRAM_CREDENTIAL_PATH`, but it must be outside the repository.

## 3. Turn notifications on

```powershell
& $aaos notify enable
```

The current Moodle state becomes the baseline: only changes **after** this point are sent.

## 4. First run ("Sync Now")

```powershell
& $aaos monitor run-once --now
```

This opens the visible Edge window, runs the normal read-only sync, records changes, and sends any notifications. `--now` ignores active hours.

Output lines:

| Line | Meaning |
|---|---|
| `EVENTS_CREATED=` | changes recorded as new notifications |
| `NOTIFICATIONS_SENT=` | messages sent to Telegram |
| `MONITOR_AUTH_REQUIRED=ALERT_QUEUED` | your Moodle login expired; one alert is sent per expiry |
| `NOTIFY_NOT_LINKED` | Telegram is not linked; messages wait until you link |

**Signing in.** Moodle forgets its login every time the window closes. When a run lands on the Moodle login page, AAOS follows the page's own "Log in with Microsoft" link once. If your Microsoft sign-in is remembered, that is enough and the run continues on its own. AAOS never types, clicks or reads anything on a Microsoft page. If Microsoft asks for anything (password, code, account choice), the run waits:

- a scheduled run waits **90 seconds**, then sends one "Moodle login needed" message;
- `--now` and `aaos moodle sync` wait **10 minutes**, so you can finish the prompt in the window yourself.

Spec: `docs/superpowers/specs/2026-10-03-phase-10-sso-continuation.md`.

## 5. Run it every 15 minutes (Windows Task Scheduler)

**Create the task** (runs 07:00–24:00 Amman; the command itself also skips outside those hours):

```powershell
schtasks /Create /TN "AAOS Moodle monitor" /SC MINUTE /MO 15 /TR "cmd /c cd /d C:\Users\aljaf\aaos-data && C:\Users\aljaf\aaos\.venv\Scripts\aaos.exe monitor run-once" /F
```

**Remove it:**

```powershell
schtasks /Delete /TN "AAOS Moodle monitor" /F
```

Notes:
- Each run briefly opens Edge; this is expected (owner decision).
- Runs never overlap: the browser-profile lock makes a second run exit without contacting Moodle.
- The computer must be on and awake.

## Other commands

| Command | Does |
|---|---|
| `aaos notify status` | Telegram linked or not; notifications on or off; delivery counts |
| `aaos notify test` | sends a fixed test message |
| `aaos notify disable` | stops notifications; changes during this time are not sent later |
| `aaos notify unlink` | deletes the stored token and chat, and turns notifications off |

## What is sent

| Kind | When |
|---|---|
| New announcement | an announcement appears in a current course |
| New assignment | an assignment appears, with its due date |
| Deadline changed | a due date moves, showing before and now |
| New course | a course appears in your Moodle |
| Course archived | a course is no longer listed (one message, not one per file) |
| Moodle login needed | the session expired (at most once per expiry) |

- **Digest:** more than 5 changes from one run arrive as **one** summary message.
- **Never sent:** passwords, cookies, the token, file contents or announcement bodies (not stored yet).
- **Never contacted:** nothing is sent to Moodle, and the bot never reads your messages after linking.
