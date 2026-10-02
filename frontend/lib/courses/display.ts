import type { CourseSummary } from "@/lib/api/courses";

export type CourseDisplayName = Readonly<{
  code: string | null;
  name: string;
}>;

// Moodle titles look like "AI380-OPTIMIZATION ALGORITHMS (All Sections )":
// a course code, a dash, an all-caps name and a section suffix.
const MOODLE_TITLE = /^([A-Z]{2,4}\d{3}[A-Z]?(?:\.[A-Z]+)?)\s*-\s*(.+)$/;
const ALL_SECTIONS = /\s*\(\s*All Sections\s*\)\s*$/i;
const SECTION_NUMBER = /\s*\(\d+\)\s*$/;
const SMALL_WORDS = new Set(["a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to"]);

function titleCase(text: string): string {
  return text
    .toLowerCase()
    .split(/(\s+)/)
    .map((word, index) => {
      if (/^\s+$/.test(word) || (index > 0 && SMALL_WORDS.has(word))) return word;
      const opening = word.startsWith("(") ? "(" : "";
      const rest = word.slice(opening.length);
      return opening + rest.charAt(0).toUpperCase() + rest.slice(1);
    })
    .join("");
}

function isAllCaps(text: string): boolean {
  return /[A-Z]/.test(text) && text === text.toUpperCase();
}

/** A readable course code and name from a Moodle course title. */
export function courseDisplayName(course: Pick<CourseSummary, "title" | "code">): CourseDisplayName {
  const match = MOODLE_TITLE.exec(course.title.trim());
  if (!match) {
    return { code: course.code, name: course.title.trim() };
  }
  let name = match[2];
  if (ALL_SECTIONS.test(name)) {
    name = name.replace(ALL_SECTIONS, "");
  } else {
    name = name.replace(SECTION_NUMBER, ""); // the section entry's own number
  }
  name = name.replace(/(\S)\(/g, "$1 (").trim(); // "SOCIETY(NOT" reads as "Society (Not"
  return { code: match[1], name: isAllCaps(name) ? titleCase(name) : name };
}

function isAllSectionsEntry(course: CourseSummary): boolean {
  return ALL_SECTIONS.test(course.title.trim());
}

/**
 * One entry per course: Moodle lists a course once per section plus an
 * "All Sections" entry. Entries sharing a code collapse to one, keeping the
 * "All Sections" entry. Order follows the first appearance of each course.
 */
export function distinctCourses<T extends CourseSummary>(courses: readonly T[]): T[] {
  const byKey = new Map<string, T>();
  for (const course of courses) {
    const { code } = courseDisplayName(course);
    const key = code ? `code:${code}` : `id:${course.id}`;
    const existing = byKey.get(key);
    if (!existing || (!isAllSectionsEntry(existing) && isAllSectionsEntry(course))) {
      byKey.set(key, course);
    }
  }
  return [...byKey.values()];
}
