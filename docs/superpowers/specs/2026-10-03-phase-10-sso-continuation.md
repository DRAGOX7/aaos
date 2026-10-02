# Phase 10 Addendum — Continuing a Remembered Microsoft Sign-in

**Status: DRAFT for owner approval. No code until approved.**

**Changes a Phase 2 boundary:** "the user logs in themselves". It is narrowed, not removed: AAOS never sees, types or stores a credential.

## 1. Problem (observed 2026-10-03)

- Moodle's session cookie lasts only until the browser closes, and every sync closes Edge.
- Each sync therefore starts on `/current/login/index.php`, which shows one button: "Log in with Microsoft".
- The owner's Microsoft sign-in is remembered, so pressing that button logs in with no password or code. The owner confirmed "straight in".
- **Without a change:**
  - every 15-minute check waits on the login page for up to 10 minutes;
  - it then fails as `AUTH_TIMEOUT`.

## 2. Change

When a sync lands on Moodle's login page, AAOS **follows the page's own "Log in with Microsoft" link once**, then waits for the authenticated course list, exactly as today.

**Allowed actions (the whole list):**

1. **Read the login page.** Find exactly one anchor on the configured Moodle origin whose path is `/current/auth/oauth2/login.php`.
2. **Navigate to that href once.** Equivalent to one click. No other element is clicked.
3. **After Microsoft redirects back to the Moodle origin,** navigate to the fixed course list `/current/my/courses.php`, as the sync already does.

**Never:**

- typing into any field;
- reading or storing passwords, codes, cookies or tokens;
- clicking on any Microsoft page (account picker, "Stay signed in?", consent, password, MFA);
- following any link except the one SSO anchor;
- retrying the SSO link within the same run.

**Stops:**

| Situation | Result |
|---|---|
| Not exactly one SSO anchor on the configured origin | No click; normal waiting (§3) |
| Microsoft shows any page that needs input (password, code, account picker, consent, "Stay signed in?") | AAOS does nothing on it; the run waits only its time budget (§3), then ends as `AUTH_REQUIRED` with one Telegram alert per episode |
| Redirect lands on a different origin than Moodle or Microsoft sign-in | The run stops as `AUTH_REQUIRED` |

## 3. Waiting budget

| Run | SSO follow | Wait for the course list | On timeout |
|---|---|---|---|
| Scheduled `aaos monitor run-once` (unattended) | yes | **90 s** | `AUTH_REQUIRED`: window closed, one alert per episode |
| `aaos monitor run-once --now`, `aaos moodle sync` (owner present) | yes | **10 min** (unchanged) | as today; the owner can finish any Microsoft prompt by hand in the window |

## 4. Invariants

- **Credentials:** none is entered, read, logged or stored by AAOS. Only the browser profile's existing Microsoft session is used, as it is today.
- **Scope:** the only automated navigation is the single same-origin SSO href read from Moodle's own page.
- **Read-only:** Moodle stays read-only; the login flow changes no Moodle data.
- **Boundaries unchanged:** the visible window and the headless-browsing boundary stay as they are.

## 5. Tests (written first)

- **Anchor discovery:** one same-origin `/current/auth/oauth2/login.php` anchor is followed once.
  - zero anchors → no follow;
  - two anchors → no follow;
  - an anchor on another origin → no follow;
  - a lookalike path → no follow.
- **No input:** no `fill`, `type`, `press` or `check` call is ever made on any page. A fake page fails the test if one happens.
- **Microsoft prompt:** a fake that stays on a password page ends as `AUTH_REQUIRED` within the 90 s unattended budget; the interactive mode keeps 10 minutes.
- **Success path:** the redirect back to Moodle reaches the authenticated course list and the sync continues.
- **Scheduled runs:**
  - `monitor run-once` uses the unattended budget;
  - `--now` and `aaos moodle sync` use the interactive budget.
- **Regression:** existing sync, browser-policy and Phase 2 tests stay green.

## 6. Owner verification

1. **Logged-in case:** run `aaos monitor run-once` with Moodle logged out but Microsoft remembered. Expected: completes with no click.
2. **Expired case** (optional): after a Microsoft sign-out. Expected: one "Moodle login needed" message within 90 s, and no field touched.
