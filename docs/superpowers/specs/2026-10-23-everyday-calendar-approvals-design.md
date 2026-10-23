# Everyday Calendar proposals and approvals — design note

**Status:** PROPOSAL / NOT AUTHORIZED. Design only; no code. Nothing here may be
implemented until the owner authorizes it as a phase (see §6).

**Date:** 2026-10-23

## 1. Summary

The Phase 6 approval core and the Phase 6B Google Calendar adapter exist and are frozen, and
the dashboard already has Approve and Reject controls (Phase 7E). The app the owner runs every
day (`uvicorn aaos.main:app`) still cannot use them, for two reasons:

1. **No proposals are made.** `create_app()` builds the Calendar proposal service and its executor
   only when a caller passes them in (`calendar_task_service`, `calendar_task_executor` in
   `src/aaos/main.py`). The served app passes nothing, so Ask AAOS never proposes an event.
2. **An approval would be refused.** Approving needs a trusted approver context from
   `LocalPrincipalProvider` (`src/aaos/web/principal.py`), which reads
   `AppSettings.local_approver_identity` and `local_approver_context_id`. Nothing sets them:
   `settings_from_environment()` reads only the timezone and the model provider. So an approval
   fails closed with "local approver context is unavailable".
   - The Phase 7E amendment
     (`docs/superpowers/specs/2026-09-18-phase-7e-prerequisite-architecture-amendment.md`, §6)
     already names the intended variables, `AAOS_LOCAL_APPROVER_IDENTITY` and
     `AAOS_LOCAL_APPROVER_CONTEXT_ID`. They were never wired.

The one real Calendar write so far, a single `CREATE_EVENT`, ran through a separate acceptance
harness (`tests/phase6b_evaluation/real_acceptance_cli.py`). That harness took its config from
`AAOS_PHASE6B_*` operator variables and needed a one-time authorization ID and a manifest
fingerprint.

This note proposes a small composition change. When the owner turns it on, the served app builds
the existing frozen parts, the same way the harness did, so that each Calendar event is proposed in
Ask AAOS and created only after the owner clicks Approve. It is **off by default**, and nothing
changes for anyone who does not turn it on.

## 2. Goals and non-goals

**Goals**
- Ask AAOS can propose a Calendar event, such as a study session or a deadline reminder, as it
  already can in tests.
- The owner approves or rejects each proposal in the dashboard, and only an approved proposal is
  written to Google Calendar.
- The existing approval, claim, execution and reconciliation path is reused unchanged, including
  the honest `UNKNOWN_OUTCOME` when Google's reply is lost.

**Non-goals**
- No standing approval or auto-approve. Unlike Phase 10's Telegram sends, every Calendar write
  needs its own click.
- No update, delete, invite or attendee actions: `CREATE_EVENT` only, as frozen in Phase 6B.
- No writes to the owner's primary calendar.
- No OAuth user sign-in: the existing service-account model stays.
- No remote or multi-user use. The approver guarantee stays "the same-machine trusted local user"
  (Phase 7E §6).
- No change to any frozen Phase 6 or Phase 7 contract.

## 3. Configuration

All of it is non-secret, read once at startup from the backend's environment, and **off unless
every value is present and valid**. A partial configuration fails closed at startup with a safe
message. It never half-works.

| Setting | Purpose | Rule |
|---|---|---|
| `AAOS_CALENDAR_PROPOSALS` | The master switch | Must be exactly `on`. Anything else, including unset, keeps today's behaviour. |
| `AAOS_LOCAL_APPROVER_IDENTITY` | Who approves, recorded in every approval | A safe identifier (`_LOCAL_ID` in `config.py`), for example `owner`. The browser never sees or sends it. |
| `AAOS_LOCAL_APPROVER_CONTEXT_ID` | A stable context for this machine and setup | A safe identifier, for example `laptop-2026`. Persisted only inside the bound approval. |
| `AAOS_GOOGLE_CALENDAR_ID` | The dedicated AAOS calendar | Required. A dedicated calendar shared with the service account, as in 6B, never `primary`. |
| `AAOS_GOOGLE_SERVICE_IDENTITY` | The service account's email | Must match the credential file (the factory already checks this). |
| `AAOS_GOOGLE_CREDENTIAL_PATH` | The service-account key file | Absolute and outside the repository. The existing file is `%LOCALAPPDATA%\AAOS\credentials\google-calendar-service-account.json`. |
| `AAOS_CALENDAR_TIMEZONE` | The timezone for proposals | Already read today; required when proposals are on, for example `Asia/Amman`. |

**Fixed in code, never configurable:**
- the OAuth scope, `https://www.googleapis.com/auth/calendar.events` (enforced by
  `GoogleCalendarConfig`);
- the adapter version, `calendar-adapter-v1`.

The two approver variables also become the normal way to set the approver identity, closing the gap
in §1.2. They are useful on their own: they make the Approve endpoint work for any future approval
type.

## 4. Credentials

- **What credential:** the existing Google service-account key. AAOS acts as that service
  account, which can write only to calendars shared with it. Sharing one dedicated "AAOS" calendar
  keeps the blast radius to that calendar.
  - The owner sees events by adding that calendar to their own Google Calendar view.
- **Where the key lives:** only in `GoogleCalendarClientFactory` and `RealGoogleCalendarClient`
  (`src/aaos/integrations/google_calendar/client.py`), which already confine it.
  - It never appears in Git, logs, telemetry, API responses, the browser, model or RAG context, or
    approval, action and event records. The 6B security tests cover each of these.
- **No silent fallback:** a missing, malformed or mismatched credential makes the feature
  unavailable. The factory already refuses to fall back to the fake client.
- **Rotation and revocation:** the owner deletes or rotates the key in Google Cloud and replaces
  the file. Nothing in AAOS stores a copy.

## 5. Wiring and the owner's view

**Wiring.** When the switch is on, `create_app()`'s default composition builds the same parts the
6B harness built:
- `GoogleCalendarConfig`, then `GoogleCalendarClientFactory`;
- the Calendar adapter;
- `TaskExecutionCoordinator` as the `calendar_task_executor`;
- `TaskApplicationService` (`src/aaos/tasks/service.py`) as the `calendar_task_service`;
- a `LocalPrincipalProvider` from the approver settings.

When the switch is off, it builds none of them, exactly as today.

**What the owner sees:**
1. In Ask AAOS: "put a study session for SE440 on Sunday at 6 pm". The answer shows a proposal
   card with the title, time and timezone, and **Approve** and **Reject** buttons. The same item
   appears on the Approvals page.
2. Nothing is written yet. The proposal is a durable `PENDING` approval.
3. **Approve** records the decision with the trusted approver context. The existing executor then
   claims the action and makes one `CREATE_EVENT` call. Repeated or opposite clicks are refused,
   because the approval core enforces it.
4. The card and the Tasks page show the outcome: `CONFIRMED_SUCCESS`, a typed failure, or
   `UNKNOWN_OUTCOME` when Google's reply is lost. That last case is reconciled later and never
   shown as success.
5. **Reject** ends it, with nothing written.

**What leaves the machine:** only the event fields the owner approved (title, description, time and
timezone, as the adapter's canonical event defines them), sent to Google Calendar. Course excerpts never go into an event.

## 6. Where it fits in PHASES.md

**Current state:**
- Phase 11 is PASSED / FROZEN at `phase-11-passed`.
- The Phase 0 and Phase 5 gates are still deferred.
- Phase 6B is FROZEN with the constraint "do not reopen without defect evidence". Phase 7 is
  FROZEN.

**Placement:**
- This is a new, ongoing external-write capability, so it **cannot ride on an existing phase**.
  - 6B accepted exactly one real `CREATE`, as evidence, not ongoing use (Phase 9 threat model).
  - Wiring the served app is new composition, not a 6B defect.
- **Proposed home:** a new **Phase 12, "Everyday Calendar proposals and approvals"**. It needs its
  own owner authorization in the ledger, which should again decide whether the Phase 0 and 5 gates
  stay deferred. It also needs a spec, a plan and the gate below.
- **It reuses the frozen pieces without changing them:** Phase 6A/6B, plus the Phase 7E approval
  routes and cards. Any change to a frozen contract would need its own recorded amendment, as
  Gemini's did.

## 7. Verification and proposed acceptance gate

**Automated, with the fake Google client only:**
- The switch is off by default, and the served app then builds nothing.
- A partial configuration fails closed at startup.
- The approver variables are validated and never appear in a browser payload or a log.
- A proposal, then approval, then execution creates exactly one `CREATE_EVENT`.
- Reject writes nothing, and repeated or opposite decisions are refused.
- A lost reply gives `UNKNOWN_OUTCOME`.
- The existing 6B security tests, extended to the served-app composition.

**With the owner present (real, by the owner's choice):**
- One proposal approved end to end into the dedicated calendar, then seen in Google Calendar.
- One proposal rejected.

**Gate:**
- No Calendar write without a recorded human approval.
- No write outside the configured dedicated calendar.
- No credential or approver value in Git, logs, the browser or model context.
- Off by default.
- All earlier gates still pass.

## 8. Open questions for the spec

1. **Keep the 6B dedicated calendar, or a new one?** The note assumes the existing dedicated
   calendar and service account, after the owner confirms they still exist and are shared.
2. **Approval expiry:** how long should a pending proposal stay valid if not decided? The approval
   core already binds an `action_expires_at` to every action.
3. **Telegram:** should a pending proposal also be announced there? Phase 10's standing approval
   covers only the closed notification set, so this would need its own decision.
4. **Removing an event:** nothing is planned. Should a later phase allow deleting an event AAOS
   created, under the same approval rule?
