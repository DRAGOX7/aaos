import { courseDisplayName } from "@/lib/courses/display";

type CourseRef = Readonly<{ id: number; title: string; code: string | null }>;
type AssignmentRef = Readonly<{ course_id: number; title: string; due_at: string | null }>;
type DocumentRef = Readonly<{ course_id: number }>;

export type NextDeadline = Readonly<{ title: string; dueAt: number }>;

export type CourseCardFacts = Readonly<{
  /** Null when assignments or the clock are unknown; undefined deadline means none upcoming. */
  nextDeadline: NextDeadline | null | undefined;
  /** Null when documents are unknown. */
  fileCount: number | null;
}>;

function groupKey(course: CourseRef): string {
  const { code } = courseDisplayName(course);
  return code ? `code:${code}` : `id:${course.id}`;
}

/**
 * Facts for each displayed course card, keyed by the displayed course's id.
 * Moodle lists a course once per section plus an "All Sections" entry; a card
 * stands for all of them, so its facts gather every entry sharing its code.
 */
export function courseCardFacts(
  displayed: readonly CourseRef[],
  allCourses: readonly CourseRef[],
  assignments: readonly AssignmentRef[] | null,
  documents: readonly DocumentRef[] | null,
  now: number | null,
): Map<number, CourseCardFacts> {
  const keyById = new Map<number, string>();
  for (const course of allCourses) keyById.set(course.id, groupKey(course));

  const fileCounts = new Map<string, number>();
  for (const document of documents ?? []) {
    const key = keyById.get(document.course_id);
    if (key) fileCounts.set(key, (fileCounts.get(key) ?? 0) + 1);
  }

  const deadlines = new Map<string, NextDeadline>();
  if (assignments && now !== null) {
    for (const assignment of assignments) {
      const key = keyById.get(assignment.course_id);
      const dueAt = assignment.due_at ? Date.parse(assignment.due_at) : Number.NaN;
      if (!key || Number.isNaN(dueAt) || dueAt < now) continue;
      const current = deadlines.get(key);
      if (!current || dueAt < current.dueAt) deadlines.set(key, { title: assignment.title, dueAt });
    }
  }

  const facts = new Map<number, CourseCardFacts>();
  for (const course of displayed) {
    const key = groupKey(course);
    facts.set(course.id, {
      nextDeadline: assignments && now !== null ? deadlines.get(key) : null,
      fileCount: documents ? (fileCounts.get(key) ?? 0) : null,
    });
  }
  return facts;
}
