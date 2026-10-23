# Phase 12 Spec — Everyday Calendar Proposals and Approvals

**Status: APPROVED by the owner on 2026-10-23 ("I approve the Phase 12 spec with the recommended defaults for D1–D6"). Coding has not started; the owner asked not to start yet.**

- **Authorization:** `docs/GOVERNANCE_LEDGER.md`, "Phase 12 authorization (2026-10-23)".
- **Design note:** `docs/superpowers/specs/2026-10-23-everyday-calendar-approvals-design.md`.
- **Owner decisions:** §2.
- **Pre-check findings:** §11.
- **Scope:** configuration, a production composition of the frozen Calendar parts, the served
  app's wiring, and the owner-present real check.
- **Reused unchanged:**
  - the Phase 6A approval core (approval records, claims, operations, events, reconciliation);
  - the Phase 6B Calendar contracts, client factory, client and adapter;
  - the Phase 7E approval routes, mutation fence and proposal and approval cards;
  - the agent's existing Calendar proposal tool.

## 1. Invariants

1. **Off by default.** With `AAOS_CALENDAR_PROPOSALS` unset, the served app builds nothing new and
   behaves exactly as today.
2. **One click per write.** Every Calendar write needs its own recorded human approval. There is no
   standing approval, auto-approve or retry that writes again after an unknown outcome.
3. **`CREATE_EVENT` only,** and only to the configured dedicated calendar. Never `primary`, and no
   update, delete, invite or attendees.
4. **A trusted approver only.** The approver identity and context come from the backend's
   environment through `LocalPrincipalProvider`. The browser can name an approval and a decision,
   nothing else (Phase 7E §6).
5. **Credentials stay confined.** The service-account key is read only by
   `GoogleCalendarClientFactory`. It and the approver values never appear in:
   - Git, logs or telemetry;
   - API responses or the browser;
   - model or RAG context;
   - approval, action or event records (where the target is stored only as the existing hashed
     `TargetIdentity`).
6. **No silent fallback.** A missing, malformed or mismatched credential or setting leaves proposals
   off and says so. It never falls back to the fake client.
7. **Honest outcomes.** A lost provider reply stays `UNKNOWN_OUTCOME` until reconciliation decides
   it. It is never shown as success.
8. **Frozen contracts are untouched.** Phase 12 adds composition and configuration only. Any change
   to a Phase 6 or Phase 7 contract needs its own recorded amendment.

## 2. Owner decisions (approved 2026-10-23: all recommended defaults)

| # | Decision | Approved default |
|---|---|---|
| D1 | How long a proposal can wait for approval | **24 hours** (`approval_lifetime`), with the action valid for **25 hours** (`action_lifetime`). The frozen constructor defaults are 5 and 10 minutes, chosen for the 6B acceptance run. They are only parameters, so no contract changes. |
| D2 | Which calendar | **The existing 6B dedicated calendar**, after the owner confirms it in §11. A new dedicated calendar is the alternative. |
| D3 | Approver identity and context values | `owner` and `aaos-laptop-1`. Both must be safe identifiers (`_LOCAL_ID`). |
| D4 | A partial or invalid configuration | **The backend still starts, with proposals off.** It logs one clear warning, and System health shows `CALENDAR_PROPOSALS_MISCONFIGURED`. The alternative is to refuse to start at all. |
| D5 | Telegram announcement of pending proposals | **No.** Phase 10's standing approval covers only its closed event set. |
| D6 | Where proposals come from | **Ask AAOS only:** the existing agent proposal tool. No proposals from scheduled jobs or notifications. |

## 3. Configuration (12A)

**Settings,** read once at startup:
- `AAOS_CALENDAR_PROPOSALS=on`;
- `AAOS_LOCAL_APPROVER_IDENTITY` and `AAOS_LOCAL_APPROVER_CONTEXT_ID`;
- `AAOS_GOOGLE_CALENDAR_ID`, `AAOS_GOOGLE_SERVICE_IDENTITY` and `AAOS_GOOGLE_CREDENTIAL_PATH`;
- `AAOS_CALENDAR_TIMEZONE` (already read today).

**Approver settings everywhere.** `settings_from_environment()` reads the two approver variables
into the existing `AppSettings.local_approver_identity` and `local_approver_context_id` fields.
`AppSettings` already validates them, so the Approve endpoint has a trusted approver whenever
they are set, even with proposals off. This closes the gap in design note §1.2.

**Calendar settings** become a `GoogleCalendarConfig` with these fixed in code:
- the scope `https://www.googleapis.com/auth/calendar.events`;
- `provider_adapter_version` `calendar-adapter-v1`;
- `enabled=True` only when the switch is on.

The credential path must be absolute and outside the repository, the rule the factory already
enforces.

**What D4 means for the states:**

| State | Meaning |
|---|---|
| `ON` | Every value is present and valid, and the factory built a client |
| `OFF` | Switch unset |
| `MISCONFIGURED` | Switch on, but something is missing, invalid, or refused by the factory |

`MISCONFIGURED` names only which setting is wrong, never its value.

## 4. Production composition (12B)

A new module (for example `src/aaos/integrations/google_calendar/composition.py`) builds what
`tests/phase6b_evaluation/real_acceptance_cli.py` built, without the acceptance-only parts (the
one-time authorization ID, manifest fingerprint and acceptance root).

| Part | Production value |
|---|---|
| `Phase6ActionDefinition` | `CREATE_EVENT`, `calendar_create_event`, `phase6b-calendar-v1`, `LOCAL_STATE_WRITE` (the Phase 7E rule: preparing a proposal is a local write, and the external write is gated by Phase 6 approval), timeout 10 s, the canonical-event argument validator. **`tool_definition_fingerprint` and `policy_fingerprint` are computed from the real tool definition and policy**, not the harness placeholders. |
| Adapter | `GoogleCalendarAdapter` with `derivation_version="calendar-provider-id-v1"` (as in 6B) and the repository-backed expected-action loader |
| Client | `GoogleCalendarClientFactory(config)`. A failure gives `MISCONFIGURED`, never the fake client. |
| Proposal service | `TaskApplicationService(...)` with D1's lifetimes, the actor from the approver context, and the config |
| Executor | `TaskExecutionCoordinator(...)` with the same definition and policy |
| Principal | `LocalPrincipalProvider(settings)` |

**Wiring.** `create_app()`'s default path passes the proposal service and the executor in only
when the state is `ON`. When it isn't, the app is composed exactly as today. The approve route
already calls the executor when one is present (`routes.py`, `approve_approval`), and the agent
already offers its proposal tool only when a proposal service exists.

## 5. What the owner sees (12C)

The UI is already built (Phase 7E). Phase 12 changes only wording.

1. **Ask AAOS** ("a study session for SE440 on Sunday at 6 pm") shows a proposal card with the
   title, time, timezone and the approval deadline (D1).
2. **Approve** records the decision, then one `CREATE_EVENT` runs. The card moves through
   "Approved · executing" to "Created" or a typed failure, or `UNKNOWN_OUTCOME`.
3. **Reject** ends it, with nothing written.
4. A proposal not decided in time expires, and the card says so.
5. **System health** shows Calendar proposals as `ON`, `OFF` or `MISCONFIGURED`.

## 6. Data leaving the machine

Only the approved event's canonical fields (summary, description, start, end, timezone,
location), sent to Google Calendar after approval. No course excerpt is copied into an event
unless the owner's own request put it in the title or description they approved.

## 7. Tests (written first, per slice)

All automated tests use the fake Google client. No real Google call runs in the suite.

- **12A Configuration:**
  - the switch is off by default and nothing is built;
  - each missing or invalid value gives `MISCONFIGURED` with the setting's name and no value;
  - the approver variables are read and validated;
  - an in-repository credential path is refused;
  - `primary` is refused as the calendar ID.
- **12B Composition:**
  - the definition matches the frozen 6B shape;
  - its fingerprints are computed, not placeholders;
  - a factory failure never selects the fake client;
  - the lifetimes from D1 are passed through.
- **12C Flow:** proposal → approve → exactly one `CREATE_EVENT` on the fake client, then:
  - reject writes nothing;
  - repeated and opposite decisions are refused;
  - an expired proposal can't be approved;
  - a lost reply gives `UNKNOWN_OUTCOME` with no second create;
  - the browser can't supply identity, target or arguments.
- **Security:** the 6B security tests run against the served-app composition: no key, calendar
  ID, service identity or approver value in logs, API responses, records or model context.
- **Regression:** the full backend suite, the A–H selectors, ruff, mypy and the frontend tests.

## 8. Owner-present real check (12D)

Only after the spec is approved and 12A–12C pass:

1. The owner starts the backend with the configuration.
2. In Ask AAOS, one proposal is approved. The event appears in the dedicated calendar, and the
   card shows the confirmed outcome.
3. A second proposal is rejected. Nothing appears in the calendar.
4. The ledger records both outcomes, with only hashed targets and no IDs.

## 9. Out of scope

- Update, delete, invite, attendees and recurring events.
- The primary calendar, OAuth user sign-in, or other Google APIs.
- Standing or automatic approval, and Telegram announcement (D5).
- Proposals from anything other than Ask AAOS (D6).
- Remote or multi-user use.

## 10. Acceptance gate

As listed under Phase 12 in `docs/PHASES.md`, plus:
- the 12A–12C tests pass;
- the 12D real check is recorded.

## 11. Pre-check findings (2026-10-23, read-only)

Run before drafting, at the owner's request. It used only the `calendar.readonly` scope and GET
requests, and it printed no key, account email or calendar ID: matches were checked by hash
against the 6B record.

| Check | Result |
|---|---|
| The service-account key file exists | **Yes:** `%LOCALAPPDATA%\AAOS\credentials\google-calendar-service-account.json`, dated 2026-09-15 |
| The key is the 6B service account | **Yes:** its email hashes to the 6B record's `svc-9775433c…` |
| Google still accepts the key | **Yes:** a read-only token was issued |
| The 6B dedicated calendar is reachable | **Not yet confirmed.** The service account's own calendar list is empty, which is normal: calendars shared with a service account usually don't appear there. AAOS stores only the calendar's hash (`cal-e22174a2…`), so the ID must come from the owner. |
| The `AAOS_PHASE6B_*` settings | Not set in any environment now. They were the acceptance run's settings. |

**To finish the check (owner):** set `AAOS_GOOGLE_CALENDAR_ID` in a terminal to the dedicated
calendar's ID, found in Google Calendar under the calendar's settings, "Integrate calendar". Then
run the read-only check script.
- It confirms by hash that it is the 6B calendar, and that the service account can read it.
- The script is kept outside the repository, since no code is written before spec approval.
  12A can turn it into an `aaos doctor` check.
- Write permission is confirmed only in 12D.

### 11.1 Calendar identification (2026-10-23)

- **What was compared:** the owner gave the calendar ID of the calendar Phase 6B wrote to. Its hash
  was compared locally with the 6B record. No network call was made.
- **Result:** **it matches `cal-e22174a2…`, and it is the owner's primary calendar** (its ID is the
  owner's Google account address).
- **So the Phase 6B real write went to the owner's primary calendar, not a dedicated one.** The 6B
  plan's "dedicated calendar" wording did not hold in the run.
- **Effect on D2:** "the existing 6B dedicated calendar" can't be used. Invariant 3 and the
  Phase 12 not-authorized list forbid the primary calendar. D2 therefore falls back, as the ledger
  provides, to **a new dedicated calendar**:
  1. the owner creates it in Google Calendar;
  2. the owner shares it with the same service account, with "Make changes to events";
  3. its ID is confirmed with the read-only check before 12D.
- **Recommended:** once the new calendar is shared, the owner removes the service account's access
  to the primary calendar, so the key can no longer write there at all.

### 11.2 The new dedicated calendar (2026-10-23, read-only)

The owner created a dedicated calendar named **AAOS** and shared it with the same service account,
with "Make changes to events". Its ID is a group calendar ID ending in
`@group.calendar.google.com`; it is set in the owner's environment, not stored here. The check used
the `calendar.readonly` scope and GET requests only.

| Check | Result |
|---|---|
| A dedicated, non-primary calendar | **Yes** (a group calendar ID) |
| Readable by the service account | **Yes:** summary `AAOS`, time zone `Asia/Amman` |
| Write permission | Not checkable read-only. Confirmed in 12D. |
| The service account can still read the **primary** calendar | **Yes.** Its access has not been removed, or the primary calendar is readable publicly. Recommended to the owner: remove the service account from the primary calendar's sharing, and check "Make available to public" is off. |

**D2 is now settled:** Phase 12 uses this AAOS calendar. The primary calendar is refused by
configuration (`primary` and the owner's own address are rejected as `AAOS_GOOGLE_CALENDAR_ID`).

