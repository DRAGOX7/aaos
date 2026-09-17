import Link from "next/link";

import type { CourseSummary } from "@/lib/api/courses";

type CourseCardProps = Readonly<{
  course: CourseSummary;
}>;

export function CourseCard({ course }: CourseCardProps) {
  return (
    <li className="py-4 first:pt-0 last:pb-0">
      <Link
        href={`/courses/${course.id}`}
        className="block rounded-lg px-2 py-2 transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-foreground">
              {course.title}
            </p>

            {course.description ? (
              <p className="mt-1 text-sm text-muted-foreground">
                {course.description}
              </p>
            ) : null}
          </div>

          {course.code ? (
            <span className="shrink-0 text-xs font-medium text-muted-foreground">
              {course.code}
            </span>
          ) : null}
        </div>
      </Link>
    </li>
  );
}
