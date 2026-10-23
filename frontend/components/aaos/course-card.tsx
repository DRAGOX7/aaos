"use client";

import Link from "next/link";

import {
  formatArchivedDate,
  isPastCourse,
  type CourseSummary,
} from "@/lib/api/courses";
import { courseDisplayName } from "@/lib/courses/display";
import { useI18n } from "@/lib/i18n";

type CourseCardProps = Readonly<{
  course: CourseSummary;
}>;

export function CourseCard({ course }: CourseCardProps) {
  const { t, dateLocale } = useI18n();
  const archived = isPastCourse(course)
    ? formatArchivedDate(course.archived_at, dateLocale("en-GB"))
    : null;
  const { code, name } = courseDisplayName(course);

  return (
    <li className="py-4 first:pt-0 last:pb-0">
      <Link
        href={`/courses/${course.id}`}
        className="block rounded-lg px-2 py-2 transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <p className="truncate text-[15px] font-semibold text-foreground">
              {name}
            </p>

            {course.description ? (
              <p className="mt-1 text-sm text-muted-foreground">
                {course.description}
              </p>
            ) : null}

            {archived ? (
              <p className="mt-1 text-xs text-muted-foreground">
                {t("Archived {date}", { date: archived })}
              </p>
            ) : null}
          </div>

          {code ? (
            <span className="shrink-0 rounded-lg bg-primary-soft px-2.5 py-1 text-xs font-bold text-primary-strong">
              {code}
            </span>
          ) : null}
        </div>
      </Link>
    </li>
  );
}
