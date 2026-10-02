"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { Skeleton } from "@/components/ui/skeleton";
import { isPastCourse } from "@/lib/api/courses";
import { courseDisplayName, distinctCourses } from "@/lib/courses/display";
import { documentsQueryOptions } from "@/lib/query/academic";
import { courseListQueryOptions } from "@/lib/query/courses";

// Each course gets a stable tint by position: soft fill and readable ink.
const TINTS = [
  "bg-[#e8f1fe] text-[#0b4aae]",
  "bg-[#e7f8f7] text-[#0a6e75]",
  "bg-[#f0ecff] text-[#5238b0]",
  "bg-[#fff2df] text-[#8a5300]",
  "bg-[#fdecee] text-[#a62a33]",
  "bg-[#e9f8f0] text-[#066b47]",
] as const;

function fileLabel(count: number | null): string {
  if (count === null) return "";
  if (count === 0) return "No files posted yet";
  return `${count} file${count === 1 ? "" : "s"}`;
}

export function OverviewCourses() {
  const courses = useQuery(courseListQueryOptions());
  const documents = useQuery(documentsQueryOptions());

  const all = Array.isArray(courses.data?.courses) ? courses.data.courses : null;
  const current = all ? distinctCourses(all.filter((course) => !isPastCourse(course))) : [];
  const pastCount = all ? distinctCourses(all.filter(isPastCourse)).length : 0;
  const fileCounts = new Map<number, number>();
  const documentList = Array.isArray(documents.data?.documents) ? documents.data.documents : null;
  for (const document of documentList ?? []) {
    fileCounts.set(document.course_id, (fileCounts.get(document.course_id) ?? 0) + 1);
  }

  return (
    <section aria-labelledby="overview-courses-title" className="flex flex-col gap-3.5">
      <div className="flex items-baseline justify-between gap-4">
        <h2 id="overview-courses-title" className="text-lg font-bold tracking-tight text-foreground">
          This semester
        </h2>
        <Link
          href="/courses"
          className="text-sm font-semibold text-primary-strong underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {pastCount > 0 ? `All courses · ${pastCount} past` : "All courses"}
        </Link>
      </div>

      {courses.isPending ? (
        <div aria-label="Loading courses" className="grid grid-cols-2 gap-3 sm:gap-3.5 lg:grid-cols-3 xl:grid-cols-5">
          {Array.from({ length: 5 }, (_, index) => (
            <Skeleton key={index} className="h-[8.5rem] rounded-2xl" />
          ))}
        </div>
      ) : courses.isError || !all ? (
        <p className="rounded-2xl border border-border bg-white px-5 py-4 text-sm text-muted-foreground">
          Academic data is temporarily unavailable.
        </p>
      ) : current.length === 0 ? (
        <p className="rounded-2xl border border-border bg-white px-5 py-4 text-sm text-muted-foreground">
          No current courses yet. They appear after your next Moodle sync.
        </p>
      ) : (
        <ul aria-label="Current courses" className="grid grid-cols-2 gap-3 sm:gap-3.5 lg:grid-cols-3 xl:grid-cols-5">
          {current.map((course, index) => {
            const { code, name } = courseDisplayName(course);
            const files = documentList ? (fileCounts.get(course.id) ?? 0) : null;
            return (
              <li key={course.id}>
                <Link
                  href={`/courses/${course.id}`}
                  className="flex h-full min-h-[7.5rem] min-w-0 flex-col gap-3 rounded-2xl border border-border bg-white p-4 sm:min-h-[8.5rem] sm:gap-3.5 sm:p-[1.125rem] transition-[border-color,box-shadow] duration-200 hover:border-[#bcd3f4] hover:shadow-[0_10px_28px_rgb(16_33_75_/_0.08)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {code ? (
                    <span className={`self-start rounded-lg px-2.5 py-1 text-[13px] font-bold ${TINTS[index % TINTS.length]}`}>
                      {code}
                    </span>
                  ) : null}
                  <span className="break-words text-sm font-semibold leading-snug text-foreground sm:text-[15px]">{name}</span>
                  <span className="mt-auto text-xs text-muted-foreground">{fileLabel(files)}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
