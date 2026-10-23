"use client";

import { ClipboardList } from "lucide-react";
import { useQuery } from "@tanstack/react-query";

import { AssignmentRow } from "@/components/aaos/assignment-row";
import { EmptyState } from "@/components/aaos/empty-state";
import { PageHeader } from "@/components/aaos/page-header";
import { SectionCard } from "@/components/aaos/section-card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api/errors";
import { useI18n } from "@/lib/i18n";
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
  const { t } = useI18n();
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
        title={t("Assignments")}
        description={t("Read-only assignment deadlines from your academic workspace.")}
      />

      <SectionCard title={t("Assignments")}>
        {isPending ? (
          <div aria-label={t("Loading assignments")} className="space-y-3">
            {Array.from({ length: 3 }, (_, index) => (
              <Skeleton key={index} className="h-28 w-full rounded-lg" />
            ))}
          </div>
        ) : notFound ? (
          <p className="text-sm text-muted-foreground">{t("Course not found.")}</p>
        ) : hasError ? (
          <p className="text-sm text-muted-foreground">
            {t("Academic data is temporarily unavailable.")}
          </p>
        ) : assignmentQuery.data?.assignments.length === 0 ? (
          <EmptyState
            icon={ClipboardList}
            title={t("No assignments found.")}
            description={t("Assignments and their deadlines appear here after your next Moodle sync.")}
          />
        ) : (
          <ul aria-label={t("Assignments")} className="space-y-3">
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
