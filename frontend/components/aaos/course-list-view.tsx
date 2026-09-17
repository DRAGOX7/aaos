"use client";

import { useQuery } from "@tanstack/react-query";

import { CourseCard } from "@/components/aaos/course-card";
import { SectionCard } from "@/components/aaos/section-card";
import { Skeleton } from "@/components/ui/skeleton";
import { courseListQueryOptions } from "@/lib/query/courses";

export function CourseListView() {
  const { data, isError, isPending } = useQuery(
    courseListQueryOptions(),
  );

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">
          Courses
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Active courses in your academic workspace.
        </p>
      </div>

      <SectionCard title="Active courses">
        {isPending ? (
          <div aria-label="Loading courses" className="space-y-3">
            {Array.from({ length: 3 }, (_, index) => (
              <Skeleton key={index} className="h-16 w-full rounded-lg" />
            ))}
          </div>
        ) : isError ? (
          <p className="text-sm text-muted-foreground">
            Academic data is temporarily unavailable.
          </p>
        ) : data?.courses.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No active courses found.
          </p>
        ) : (
          <ul aria-label="Active courses" className="divide-y divide-border">
            {data?.courses.map((course) => (
              <CourseCard key={course.id} course={course} />
            ))}
          </ul>
        )}
      </SectionCard>
    </div>
  );
}
