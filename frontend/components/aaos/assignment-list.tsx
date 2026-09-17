"use client";

import { useQuery } from "@tanstack/react-query";

import { AssignmentRow } from "@/components/aaos/assignment-row";
import { PageHeader } from "@/components/aaos/page-header";
import { SectionCard } from "@/components/aaos/section-card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api/errors";
import { courseListQueryOptions } from "@/lib/query/courses";
import { assignmentsQueryOptions } from "@/lib/query/academic";

type AssignmentListProps = Readonly<{
  courseId?: number;
  now: Date;
}>;

export function AssignmentList({
  courseId,
  now,
}: AssignmentListProps) {
  const assignmentQuery = useQuery(assignmentsQueryOptions(courseId));
  const courseQuery = useQuery(courseListQueryOptions());

  const notFound =
    assignmentQuery.error instanceof ApiError &&
    assignmentQuery.error.code === "COURSE_NOT_FOUND";

  const hasError = assignmentQuery.isError || courseQuery.isError;
  const isPending = assignmentQuery.isPending || courseQuery.isPending;
  const coursesById = new Map(
    (courseQuery.data?.courses ?? []).map((course) => [course.id, course]),
  );

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6">
      <PageHeader
        title="Assignments"
        description="Read-only assignment deadlines from your academic workspace."
      />

      <SectionCard title="Assignments">
        {isPending ? (
          <div aria-label="Loading assignments" className="space-y-3">
            {Array.from({ length: 3 }, (_, index) => (
              <Skeleton key={index} className="h-28 w-full rounded-lg" />
            ))}
          </div>
        ) : notFound ? (
          <p className="text-sm text-muted-foreground">Course not found.</p>
        ) : hasError ? (
          <p className="text-sm text-muted-foreground">
            Academic data is temporarily unavailable.
          </p>
        ) : assignmentQuery.data?.assignments.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No assignments found.
          </p>
        ) : (
          <ul aria-label="Assignments" className="space-y-3">
            {assignmentQuery.data?.assignments.map((assignment) => {
              const course = coursesById.get(assignment.course_id);

              return (
                <AssignmentRow
                  key={assignment.id}
                  assignment={assignment}
                  courseContext={
                    course
                      ? {
                          kind: "AVAILABLE",
                          title: course.title,
                          code: course.code,
                        }
                      : { kind: "UNAVAILABLE" }
                  }
                  now={now}
                />
              );
            })}
          </ul>
        )}
      </SectionCard>
    </div>
  );
}
