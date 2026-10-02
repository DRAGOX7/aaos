# Overview course cards — colour blocks with real data

**Status:** approved by the owner on 2026-10-03 ("yes do both": a colour mockup, then this plan and the build).
**Scope:** frontend only. Overview page, "This semester" section. No backend or API change.

## 1. Goal

Make each current course instantly recognisable, the way the reference design does (colour block, big title, one action). Every value on a card must come from data AAOS already has.

## 2. What a card shows

| Part | Content | Source |
|---|---|---|
| Colour band | course code chip + course name, large | `courseDisplayName` |
| Deadline line | the next upcoming assignment: "Lab 4 · due Sun 12:00", or "No upcoming deadlines" | assignments query (`due_at` ≥ now) |
| Footer | "N files" or "No files posted yet", plus an "Open" pill | documents query |

**Left out on purpose:**
- **Progress bars and lesson counts.** AAOS has no lesson-progress data, so a bar would be invented.
- **Classmate avatars.** No such data, and it should not be collected.
- **"New files".** `updated_at` reflects the sync time, not when the teacher posted the file.

The existing test "invents no analytics, schedule, or profile data" keeps guarding this.

## 3. Decisions

- **Style:** chosen from the mockup: **C, white card with coloured top** (owner, 2026-10-03).
  - **A:** deep solid fill;
  - **B:** soft tint;
  - **C:** white card with a coloured top (recommended).
  - The palette is one constant, so switching styles is a small edit.
- **Contrast:** every text/background pair meets WCAG AA (4.5:1 for small text).
- **One link per card:** the card stays a single link to `/courses/{id}`. "Open" is a visual pill inside it, not a nested button. A nested button would be invalid HTML and a second tab stop.
- **Duplicate Moodle entries:** a course's section entries and "All Sections" entry already collapse into one card (`distinctCourses`). The card's deadline and file count now gather data from every entry sharing that course code, not just the kept entry, so an assignment posted in a section entry still shows.
- **Clock:** the deadline line uses `useClientMinute`, as "Up next" does, so the server render and hydration agree. Before the clock is known, the line is omitted.
- **Failure isolation:**
  - if assignments fail, cards still render without the deadline line;
  - if documents fail, cards render without the file count (as today).
- **Layout:** the card height is fixed by the content (no stretching text). Grid:
  - phones: 1 column;
  - `sm`: 2 columns;
  - `lg`: 3 columns;
  - `xl`: 3 columns, wrapping (was 5).
  - Bigger cards need the room.

## 4. Steps

1. **Pure helper.** Add `lib/courses/card-data.ts` with `courseCardFacts(courses, assignments, documents, now)`. It returns, per displayed course, the next deadline and the file count, grouped by display code. Unit tests cover:
   - grouping across section entries;
   - past deadlines ignored;
   - the earliest upcoming deadline chosen;
   - null `due_at` ignored;
   - assignments or documents unavailable.
2. **Component.** Rewrite the card markup in `components/aaos/overview-courses.tsx`. Keep the section header, the loading, error and empty states, and the list label "Current courses".
3. **Tests.** Extend `tests/overview-academic-summary.test.tsx`:
   - a deadline shows on the right card;
   - "No upcoming deadlines" appears;
   - cards survive an assignments failure;
   - the card is still one link.
4. **Verify:** `npx vitest run`, `npx tsc --noEmit`, `npx eslint .`, and a visual check in the browser at desktop and phone widths.

## 5. Out of scope

- The Courses page (`/courses`) cards. The same component could be reused there later.
- Per-course colours chosen by the user.
