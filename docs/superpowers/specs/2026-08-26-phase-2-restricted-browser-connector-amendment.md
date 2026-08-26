# Phase 2 Restricted Browser Connector Amendment

Status: human-approved scope amendment for the current Phase 2 implementation

This addendum preserves the historical Phase 2 design and implementation plans.
It supersedes only the course-only, no-response-body, and entity-expansion
restrictions that conflict with the later restricted browser connector scope
approved during Phase 2 continuation. All other Phase 2 requirements remain in
force.

## Approved Phase 2 namespace

After complete course discovery succeeds, the restricted browser connector may
collect the approved Phase 2 namespace:

- courses;
- assignments;
- announcements;
- Moodle Resource remote-file metadata.

The connector may produce a `CompleteSourceSnapshot` only when the entire
approved namespace is completely enumerated and every collection stage
succeeds. Partial, malformed, redirected, failed, or unknown-completeness
results remain fail-closed.

## Approved entity sources and title contracts

- Courses: the validated Moodle-owned
  `core_course_get_enrolled_courses_by_timeline_classification` response.
- Course titles: the validated `fullname` field already present in that
  response.
- Assignments: approved COURSE_DETAIL assignment activity links.
- Assignment titles: the first validated nonblank safe value from existing
  approved anchor metadata, in this order: `data-activityname`, `aria-label`,
  `title`, then anchor text.
- Announcements: the approved announcement/news-forum canonical discussion
  representation.
- Announcement titles: the first validated nonblank safe value from existing
  approved canonical discussion-link metadata, using the same anchor metadata
  order.
- Remote files: approved Moodle `/mod/resource/view.php?id=...` activities
  only.
- Remote-file titles: the first validated nonblank safe value from existing
  approved resource-link metadata, using the same anchor metadata order.

Remote-file scope does not include `/mod/url`, `/mod/page`, `pluginfile`,
downloads, or arbitrary resources.

## Narrow response observation approval

The earlier blanket prohibition on response-body inspection is superseded only
for the validated Moodle-owned course-discovery response for
`core_course_get_enrolled_courses_by_timeline_classification`. The connector
may observe and validate that exact expected response solely to establish
complete course discovery and retain approved stable course metadata such as
the ID and `fullname`.

This does not authorize generic network-body inspection, arbitrary AJAX
operations, credential/cookie/storage inspection, sesskey export, unrelated
response capture, or broader network permissions.

## Unchanged safety and persistence boundaries

- Browser mode is explicitly selected and uses a dedicated persistent Edge
  profile with human SSO/MFA; AAOS never automates passwords or MFA.
- Cookies, browser storage, and `storage_state` are not exported or inspected.
- There is no API/browser automatic fallback, Moodle write, download, or
  `pluginfile` access.
- Normal-database automatic promotion remains prohibited; normal AAOS database
  synchronization remains separately human-authorized.
- No Moodle-specific persistence tables are introduced.
- Persistence remains exactly:

  `CompleteSourceSnapshot -> normalize_collection() -> CanonicalSnapshot ->`
  `AcademicSyncService.sync()`

  using a caller-selected disposable migrated SQLite database for Phase 2
  acceptance. The amendment does not authorize `data/database/aaos.db`.
