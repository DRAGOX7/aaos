"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { SectionCard } from "@/components/aaos/section-card";
import { Skeleton } from "@/components/ui/skeleton";
import { isPastCourse } from "@/lib/api/courses";
import { courseListQueryOptions } from "@/lib/query/courses";

export function CourseProofCard() {
  const {
    data,
    isError,
    isPending,
  } = useQuery(courseListQueryOptions());
  const currentCourses = data?.courses.filter((course) => !isPastCourse(course)) ?? [];

  return (
    <SectionCard
      title="Courses"
      action={
        <Link
          href="/courses"
          className="text-sm font-medium text-primary underline-offset-4 transition-colors hover:underline"
        >
          View courses
        </Link>
      }
    >
      {isPending ? (
        <div
          aria-label="Loading courses"
          className="space-y-3"
        >
          {Array.from({ length: 3 }, (_, index) => (
            <Skeleton
              key={index}
              className="h-14 w-full rounded-lg"
            />
          ))}
        </div>
      ) : isError ? (
        <p className="text-sm text-muted-foreground">
          Academic data is temporarily unavailable.
        </p>
      ) : currentCourses.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No active courses found.
        </p>
      ) : (
        <ul className="divide-y divide-border">
          {currentCourses.slice(0, 3).map((course) => (
            <li
              key={course.id}
              className="flex min-h-14 items-center justify-between gap-4 py-3 first:pt-0 last:pb-0"
            >
              <p className="min-w-0 truncate text-sm font-medium text-foreground">
                {course.title}
              </p>

              {course.code ? (
                <span className="shrink-0 text-xs font-medium text-muted-foreground">
                  {course.code}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}
