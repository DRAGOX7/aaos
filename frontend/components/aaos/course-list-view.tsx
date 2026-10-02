"use client";

import { useQuery } from "@tanstack/react-query";

import { CourseCard } from "@/components/aaos/course-card";
import { PageHeader } from "@/components/aaos/page-header";
import { SectionCard } from "@/components/aaos/section-card";
import { Skeleton } from "@/components/ui/skeleton";
import { isPastCourse } from "@/lib/api/courses";
import { distinctCourses } from "@/lib/courses/display";
import { courseListQueryOptions } from "@/lib/query/courses";

export function CourseListView() {
  const { data, isError, isPending } = useQuery(
    courseListQueryOptions(),
  );
  // Moodle lists a course once per section; show each course once.
  const currentCourses = distinctCourses(data?.courses.filter((course) => !isPastCourse(course)) ?? []);
  const pastCourses = distinctCourses(data?.courses.filter(isPastCourse) ?? []);

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6">
      <PageHeader
        title="Courses"
        description="Active courses in your academic workspace."
      />

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
        ) : currentCourses.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No active courses found.
          </p>
        ) : (
          <ul aria-label="Active courses" className="divide-y divide-border">
            {currentCourses.map((course) => (
              <CourseCard key={course.id} course={course} />
            ))}
          </ul>
        )}
      </SectionCard>

      {pastCourses.length > 0 ? (
        <SectionCard title="Past courses">
          <ul aria-label="Past courses" className="divide-y divide-border">
            {pastCourses.map((course) => (
              <CourseCard key={course.id} course={course} />
            ))}
          </ul>
        </SectionCard>
      ) : null}
    </div>
  );
}
