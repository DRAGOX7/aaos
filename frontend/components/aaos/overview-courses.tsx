"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { CalendarCheck, CalendarClock } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { isPastCourse } from "@/lib/api/courses";
import { courseCardFacts, type CourseCardFacts } from "@/lib/courses/card-data";
import { courseDisplayName, distinctCourses } from "@/lib/courses/display";
import { assignmentsQueryOptions, documentsQueryOptions } from "@/lib/query/academic";
import { courseListQueryOptions } from "@/lib/query/courses";
import { useClientMinute } from "@/lib/time/use-client-minute";

// Each course gets a stable colour by position. White text meets WCAG AA on
// every band (each is at least 6:1 against white).
const BANDS = ["#1d3a8a", "#0f6b66", "#5232a8", "#8a4b08", "#9b1c3a", "#25507a"] as const;

const dueFormat = new Intl.DateTimeFormat("en-GB", {
  weekday: "short",
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});

function fileLabel(count: number | null): string {
  if (count === null) return "";
  if (count === 0) return "No files posted yet";
  return `${count} file${count === 1 ? "" : "s"}`;
}

function DeadlineLine({ deadline }: { deadline: CourseCardFacts["nextDeadline"] }) {
  // Unknown (assignments failed, or the clock is not known yet): say nothing.
  if (deadline === null) return null;
  if (deadline === undefined) {
    return (
      <p className="flex items-center gap-2 text-[13px] text-muted-foreground">
        <CalendarCheck aria-hidden="true" className="size-4 shrink-0" strokeWidth={2} />
        No upcoming deadlines
      </p>
    );
  }
  return (
    <p className="flex items-start gap-2 text-[13px] leading-snug text-foreground">
      <CalendarClock
        aria-hidden="true"
        className="mt-px size-4 shrink-0 text-primary-strong"
        strokeWidth={2}
      />
      <span className="min-w-0 break-words">
        <span className="font-semibold">{deadline.title}</span>
        <span className="text-muted-foreground"> · due {dueFormat.format(deadline.dueAt)}</span>
      </span>
    </p>
  );
}

export function OverviewCourses() {
  const courses = useQuery(courseListQueryOptions());
  const documents = useQuery(documentsQueryOptions());
  const assignments = useQuery(assignmentsQueryOptions());
  // "Upcoming" depends on the viewer's clock, known only after hydration.
  const now = useClientMinute();

  const all = Array.isArray(courses.data?.courses) ? courses.data.courses : null;
  const current = all ? distinctCourses(all.filter((course) => !isPastCourse(course))) : [];
  const pastCount = all ? distinctCourses(all.filter(isPastCourse)).length : 0;
  const documentList = Array.isArray(documents.data?.documents) ? documents.data.documents : null;
  const assignmentList = Array.isArray(assignments.data?.assignments)
    ? assignments.data.assignments
    : null;
  const facts = courseCardFacts(current, all ?? [], assignmentList, documentList, now);

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
        <div aria-label="Loading courses" className="grid gap-3.5 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }, (_, index) => (
            <Skeleton key={index} className="h-[11rem] rounded-2xl" />
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
        <ul aria-label="Current courses" className="grid gap-3.5 sm:grid-cols-2 lg:grid-cols-3">
          {current.map((course, index) => {
            const { code, name } = courseDisplayName(course);
            const card = facts.get(course.id);
            const band = BANDS[index % BANDS.length];
            return (
              <li key={course.id}>
                <Link
                  href={`/courses/${course.id}`}
                  className="group flex h-full min-w-0 flex-col overflow-hidden rounded-2xl border border-border bg-white transition-[box-shadow,transform] duration-200 hover:-translate-y-0.5 hover:shadow-[0_14px_32px_rgb(16_33_75_/_0.12)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 motion-reduce:transition-none motion-reduce:hover:translate-y-0"
                >
                  <div
                    className="flex flex-col gap-2.5 px-[1.125rem] pb-4 pt-[1.125rem] text-white"
                    style={{ backgroundColor: band }}
                  >
                    {code ? (
                      <span className="self-start rounded-lg bg-white/15 px-2.5 py-1 text-[13px] font-bold">
                        {code}
                      </span>
                    ) : null}
                    <span className="break-words text-[17px] font-bold leading-snug tracking-tight">
                      {name}
                    </span>
                  </div>
                  <div className="flex flex-1 flex-col gap-3.5 px-[1.125rem] py-4">
                    <DeadlineLine deadline={card ? card.nextDeadline : null} />
                    <div className="mt-auto flex items-center justify-between gap-3">
                      <span className="text-xs text-muted-foreground">
                        {fileLabel(card ? card.fileCount : null)}
                      </span>
                      {/* Visual only: the whole card is the one link. */}
                      <span
                        aria-hidden="true"
                        className="rounded-lg px-3 py-1.5 text-xs font-semibold text-white"
                        style={{ backgroundColor: band }}
                      >
                        Open
                      </span>
                    </div>
                  </div>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
