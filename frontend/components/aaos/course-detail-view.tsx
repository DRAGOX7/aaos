"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { SectionCard } from "@/components/aaos/section-card";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api/errors";
import { courseQueryOptions } from "@/lib/query/academic";

type CourseDetailViewProps = Readonly<{
  courseId: number | null;
}>;

export function parseCourseRouteId(value: string): number | null {
  if (!/^[0-9]+$/.test(value)) {
    return null;
  }

  const courseId = Number(value);

  return Number.isSafeInteger(courseId) && courseId > 0
    ? courseId
    : null;
}

function CourseDetailQuery({ courseId }: { courseId: number }) {
  const {
    data,
    error,
    isError,
    isPending,
  } = useQuery(courseQueryOptions(courseId));

  if (isPending) {
    return (
      <SectionCard title="Course">
        <div aria-label="Loading course">
          <Skeleton className="h-8 w-2/3" />
          <Skeleton className="mt-3 h-4 w-1/3" />
          <Skeleton className="mt-6 h-20 w-full" />
        </div>
      </SectionCard>
    );
  }

  if (isError || !data) {
    const notFound =
      error instanceof ApiError && error.code === "COURSE_NOT_FOUND";

    return (
      <SectionCard title="Course">
        <p className="text-sm text-muted-foreground">
          {notFound
            ? "Course not found."
            : "Academic data is temporarily unavailable."}
        </p>
      </SectionCard>
    );
  }

  return (
    <SectionCard title="Course">
      <article aria-labelledby="course-detail-title">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <h1
              id="course-detail-title"
              className="text-2xl font-semibold tracking-tight text-foreground"
            >
              {data.title}
            </h1>

            {data.code ? (
              <p className="mt-1 text-sm font-medium text-muted-foreground">
                {data.code}
              </p>
            ) : null}
          </div>
        </div>

        {data.description ? (
          <p className="mt-5 max-w-2xl text-sm leading-6 text-muted-foreground">
            {data.description}
          </p>
        ) : null}

        <nav
          aria-label="Course academic views"
          className="mt-6 flex flex-wrap gap-3"
        >
          <Link
            href={`/assignments?course_id=${data.id}`}
            className="rounded-md bg-secondary px-3 py-2 text-sm font-medium text-secondary-foreground transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            View assignments
          </Link>
          <Link
            href={`/documents?course_id=${data.id}`}
            className="rounded-md bg-secondary px-3 py-2 text-sm font-medium text-secondary-foreground transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            View documents
          </Link>
        </nav>
      </article>
    </SectionCard>
  );
}

export function CourseDetailView({
  courseId,
}: CourseDetailViewProps) {
  if (courseId === null) {
    return (
      <SectionCard title="Course">
        <p className="text-sm text-muted-foreground">
          The course link is invalid.
        </p>
      </SectionCard>
    );
  }

  return <CourseDetailQuery courseId={courseId} />;
}
