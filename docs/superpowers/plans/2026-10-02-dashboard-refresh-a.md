# Plan — Dashboard Visual Refresh, Direction A (2026-10-02)

**Status: APPROVED by the owner in chat** ("A, write the plan and build it").

- **Design reference:** the "AAOS Dashboard Refresh" canvas, artboard "A · Refined — the Ask bar is the hero".
- **Scope:** presentation only. No backend, API contract or data-source change. Phase 7 data authority, truthful-data rules and accessibility requirements (`docs/superpowers/specs/2026-09-16-phase-7-dashboard-operational-visibility-design.md` §13–14) still hold.
- **Phase 7 status:** frozen. This is a separate, owner-approved visual change on top of it.

## Problems this fixes (audit, 2026-10-02)

1. The Overview's largest area is a marketing banner with no information, and it repeats the header.
2. Three of four headline metrics are 0 in large type, so empty data reads as loud.
3. Four panels each say "nothing" (schedule, assignments, documents, activity), so the page looks broken.
4. The Ask panel has no input; it only links to another page.
5. Raw Moodle course names: all-caps, section suffixes, and every course listed twice (`(1)` and `(All Sections )`).
6. Uniform cards with no hierarchy.
7. **Bug:** the shell header shows "Overview" on every page except Ask.
8. **Bug:** pages render a second header with a second search box and workspace badge.

## Design decisions

| Decision | Rule |
|---|---|
| Typeface | Plus Jakarta Sans (via `next/font`) replaces Inter |
| Identity | Keep the approved AAOS blue and navy tokens; add one dark hero surface (`--aaos-hero`) |
| Shell header | **One** global top bar: search, an overall health pill, profile. No page title. |
| Page titles | Each page owns its title through `PageHeader`, a plain title block with no search or badges. Overview's title is the hero heading. |
| Overview hero | Greeting from the local time of day (no name: AAOS has no profile data), today's date, a **working** Ask box, three suggested questions. Submitting opens Ask with the question and sends it. |
| Courses | Deduplicated and readable: code badge plus a title-cased name. Moodle section duplicates collapse to one course, preferring the "All Sections" entry. |
| Empty data | One calm "Nothing due right now" card. Zero counts sit in a small Workspace list, not as big metric tiles. Panels with no data source (schedule, learning activity) are removed, not shown empty. |
| Status | Health is shown with text plus a dot, never color alone. |

### Not shown (no authoritative data)

- the user's name;
- the semester name;
- a course-scope picker (`/ask` takes no scope parameter);
- the last Moodle sync time (not in the health API).

Each can return when the backend provides it.

## Invariants

- **Truthful data only:** every number and name comes from an existing query. No invented values.
- **Independent panels:** each panel keeps its own loading, empty and error state. A failed query never blanks another panel.
- **No leaked errors:** backend error text is never shown.
- **The handoff to Ask** uses `sessionStorage` (same origin, read once, then removed), never the URL. A question is not placed in browser history.
- **Accessibility:** real buttons, links and labelled inputs; visible focus; 4.5:1 text contrast; status not by color alone; usable at 390 px wide.

## Slices

1. **This change.**
   - tokens and font;
   - one top bar and plain page titles (fixes bugs 7 and 8);
   - sidebar polish;
   - Overview direction A;
   - shared course display names, used on Overview and the Courses page;
   - the Ask handoff.
2. **Next (separate approval):** the Ask page, as the conversation surface in the same style.
3. **Later:** Courses, Assignments, Documents, Tasks, Approvals, Activity and System health layouts.

## Tests (RED first where behavior is new)

- **Course names** (`lib/courses/display.ts`):
  - code and name parsing;
  - title case;
  - section suffix stripping, including `GRADUATION PROJECT (1) (1)` → `Graduation Project (1)`;
  - deduplication preferring "All Sections";
  - non-Moodle titles left unchanged.
- **Shell:**
  - one search box and one banner on every page;
  - no "Overview" heading on other pages.
- **Overview:**
  - the hero has a labelled Ask input and a submit button;
  - submitting stores the handoff and navigates to `/ask`;
  - suggestion buttons do the same;
  - courses render deduplicated, with links;
  - zero assignments show "Nothing due right now";
  - each panel's error state is local;
  - no fake analytics text (the existing guard is kept).
- **Ask:** a stored handoff question is sent once on arrival and removed from storage.
- **Updated tests:** tests that assert the old copy ("Academic snapshot", "Open Ask AAOS", "No schedule data available.") change to the new structure. Their intent (truthful, independent panels) is kept.

## Verification

- `npm run test`, `npm run typecheck`, `npm run lint`.
- Screenshots of Overview, Courses and Ask at 1440×900 and 390×844, before and after.
- A manual accessibility pass: keyboard through the Overview, focus visible, labels present.

## Slice 2 — Ask page (built 2026-10-02)

The owner approved this in chat: "merge it and start the Ask page redesign".

### Audit of the old page

- Every citation appeared twice: as `[n] …` lines in the answer and again in Sources.
- The past-course notice was plain text.
- The composer floated under the conversation.
- The empty state was a paragraph with no starting points.
- Arabic answers were laid out left-to-right.

### Decisions

| Decision | Rule |
|---|---|
| Layout | One centered 48rem column. The composer is docked at the bottom with a visible focus ring. |
| Empty state | Four suggestion cards that map to real capabilities (course files, deadlines, Moodle changes, study planning). A card sends its question. |
| Messages | The user's messages are on the navy hero surface. AAOS answers carry the AAOS mark. Every message body uses `dir="auto"`, so Arabic reads right-to-left. |
| Answer text | `splitAnswerText` lifts the past-course notice into an amber label and drops `[n]` citation lines **only when** the Sources list shows those citations. Inline `[n]` markers stay. A body that would be empty keeps the original text. |
| Sources | Numbered to match the `[n]` markers. The citation text is unchanged. |
| Refusals | A calm note with one tip ("try naming the course"). The wording of failure guidance is unchanged. |

### Invariants kept

- The labels "Your message", "Send", "Stop waiting", "AAOS is thinking…" and "New conversation" are unchanged.
- The conversation is still not persisted.
- Backend text is only re-laid out, never reworded.
