# Archived (Past) Courses — Specification

Status: **approved by the owner on 2026-10-02**, including both choices recorded under "Owner decisions". Implementation follows this spec, test-first.

## Why

On 2026-10-02 the first Fall sync listed 10 new courses and no longer listed the 5 Summer 2026 courses. A complete snapshot deactivates whatever it doesn't see, so the Summer courses, their 22 announcements and 20 files became inactive. RAG search only uses active courses and files (`_version_filters`), so every indexed document and summary disappeared from Ask.

Owner decision (2026-10-02): keep past documents searchable, explicitly marked as past courses. Do not restore the pre-sync backup.

## Facts this builds on

- `courses`, `remote_files` and `announcements` have `is_active` but no record of when or why something became inactive.
- A complete sync deactivates the whole namespace, then re-activates every entity it saw (`academic/sync.py`). An incomplete sync never changes state.
- Document versions and their RAG indexes are immutable and unaffected by deactivation; only eligibility hides them.

## 1. Archive state

A course is in exactly one state:

| State | Meaning | Stored as |
|---|---|---|
| **Current** | listed by the latest complete sync | `is_active = 1`, `archived_at = NULL` |
| **Past** (archived) | was current, then a complete sync stopped listing it | `is_active = 0`, `archived_at = <that sync's finish time>` |

Rules:
- New nullable column `courses.archived_at`, timezone-aware (one Alembic migration). This is the explicit archive marker.
- A complete sync that stops listing a current course sets `archived_at` to the sync's finish time. If the course is listed again later (a new enrollment of the same Moodle course), it becomes current and `archived_at` is cleared.
- **Files and announcements archive with their course.** An item that stops being listed in the *same* sync that archives its course gets `archived_at` too (new nullable column on `remote_files` and `announcements`).
- **An item that disappears while its course stays current is removed, not archived.** It gets no `archived_at` and stays unsearchable, as today. This keeps an instructor's deletion meaning "gone".
- **Backfill for 2026-10-02:** the migration sets `archived_at` for the 5 Summer courses and the items deactivated in that same sync. The time comes from that sync's `moodle_sync_snapshots.finished_at`. Nothing else is inferred.
- Nothing is deleted. Archiving is reversible only through Moodle listing the course again.

## 2. Search behaviour

- **Eligible documents:** documents of current courses, plus documents of past courses whose file archived with its course. Removed files stay ineligible. Indexing (`rebuild`, `index_new`) uses the same rule, so past documents stay in the index.
- **No course named:** search **current courses only**. If current courses have no indexed documents, the answer says so and names the past courses that have documents, instead of a bare "no documents".
- **A past course named** (by code or name, e.g. "SE440"): search that past course. A question that names a past course is never widened to current courses.
- **Explicit past wording** ("last semester", "previous courses", "الفصل الماضي", "المواد السابقة") without a course: search past courses only.
- **The same course code current and past** (a retaken course): resolve to the current one. The past one is used only with past wording or an explicit choice; both together are reported as ambiguous.

## 3. Course isolation

- Course scoping, including Moodle section siblings, works identically for past courses. A question scoped to one course, current or past, never retrieves another course's documents.
- Course-name resolution covers current and past courses. The resolver returns the state with the match, so routing and answers know a course is past.
- Assignment and announcement listings default to current courses. A past course's assignments and announcements appear **only when that past course is explicitly named**, and are then labelled past. They never appear in unscoped or "recent" lists.

## 4. Summaries

- Document and course summaries work for past courses with the same verification and caching. The cache key is unchanged, so existing cached summaries stay valid for the same index generation.
- A course summary covers only one course's documents (current or past, never mixed).
- A summary of a past course is labelled as such in the answer header.

## 5. Presentation

- **API:** `CourseSummary` gains `status: "current" | "past"` and `archived_at` (nullable). This is an additive change to the API contract, so the OpenAPI document and frontend types are regenerated.
- **Courses page:** current courses first; past courses in a separate, collapsed "Past courses" group showing the archive date.
- **Answers and citations:** a citation from a past course reads e.g. `[1] SE440 Syllabus.pdf (page 2) · past course`. A grounded answer drawn only from past courses starts with a fixed, language-matched notice ("From a past course (archived 2 Oct 2026):" / Arabic equivalent).
- **"What changed":** archiving appears as "course archived" (not "removed"), listing the course. Items that archived with it are summarized as a count, not listed one by one.

## 6. Safety and verification

- No Moodle write, no new Moodle request: archiving is decided from complete snapshots AAOS already takes.
- An incomplete sync never archives or un-archives anything.
- **Tests:**
  - archive and un-archive transitions;
  - same-sync item archiving versus removal;
  - backfill on a copy of the real database;
  - eligibility and indexing;
  - global versus named past-course search;
  - retaken-course ambiguity;
  - summaries;
  - API status fields;
  - frontend grouping and citation labels.
- Frozen A–H selectors and the I4/refusal trace still pass.
- **Live check:** after the migration, Ask "who is the instructor of AI 495C?" answers from the past course with the past notice, and an unscoped question about current courses doesn't cite Summer documents.

## Out of scope

Term names (Moodle gives none; the archive date stands in), deleting past data, and a UI to manually archive or restore.

## Owner decisions (2026-10-02)

1. **Unscoped questions search current courses only.** Past courses are searched only when named, or when the question uses explicit past wording (section 2).
2. **Past-course announcements appear only when the past course is explicitly named** (section 3). They never appear in unscoped or "recent announcements" lists.
