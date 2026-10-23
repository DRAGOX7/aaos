"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { CalendarDays } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { courseDisplayName } from "@/lib/courses/display";
import { assignmentsQueryOptions } from "@/lib/query/academic";
import { courseListQueryOptions } from "@/lib/query/courses";
import { useI18n } from "@/lib/i18n";
import { useClientMinute } from "@/lib/time/use-client-minute";

const MAX_ITEMS = 3;
const DUE_OPTIONS: Intl.DateTimeFormatOptions = {
  weekday: "short",
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
};

export function OverviewUpNext() {
  const { t, dateLocale } = useI18n();
  const dueFormat = new Intl.DateTimeFormat(dateLocale("en-GB"), DUE_OPTIONS);
  const assignments = useQuery(assignmentsQueryOptions());
  const courses = useQuery(courseListQueryOptions());
  // "Upcoming" depends on the viewer's clock, known only after hydration.
  const now = useClientMinute();

  const list = Array.isArray(assignments.data?.assignments) ? assignments.data.assignments : null;
  const codes = new Map<number, string>();
  for (const course of Array.isArray(courses.data?.courses) ? courses.data.courses : []) {
    const { code } = courseDisplayName(course);
    if (code) codes.set(course.id, code);
  }
  const upcoming = (list ?? [])
    .filter((item) => item.due_at && now !== null && Date.parse(item.due_at) >= now)
    .sort((left, right) => Date.parse(left.due_at ?? "") - Date.parse(right.due_at ?? ""))
    .slice(0, MAX_ITEMS);

  return (
    <section
      aria-labelledby="overview-up-next-title"
      className="flex min-w-0 flex-col gap-3 rounded-2xl border border-border bg-card px-6 py-5"
    >
      <div className="flex items-baseline justify-between gap-4">
        <h2 id="overview-up-next-title" className="text-base font-bold text-foreground">
          {t("Up next")}
        </h2>
        <Link
          href="/assignments"
          className="text-sm font-semibold text-primary-strong underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {t("View assignments")}
        </Link>
      </div>

      {assignments.isPending ? (
        <div aria-label={t("Loading assignments")} className="space-y-2">
          <Skeleton className="h-14 w-full rounded-xl" />
          <Skeleton className="h-14 w-3/4 rounded-xl" />
        </div>
      ) : assignments.isError || !list ? (
        <p className="text-sm text-muted-foreground">{t("Academic data is temporarily unavailable.")}</p>
      ) : upcoming.length === 0 ? (
        <div className="flex items-center gap-4 rounded-xl bg-muted p-4">
          <span className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-primary-soft text-primary">
            <CalendarDays aria-hidden="true" className="size-5" strokeWidth={2} />
          </span>
          <div className="flex flex-col gap-0.5">
            <p className="text-[15px] font-semibold text-foreground">{t("Nothing due right now")}</p>
            <p className="text-sm text-muted-foreground">
              {list.length === 0
                ? t("Assignments appear here after your next Moodle sync.")
                : t("No upcoming deadlines among your assignments.")}
            </p>
          </div>
        </div>
      ) : (
        <ul aria-label={t("Upcoming assignments")} className="flex flex-col divide-y divide-border">
          {upcoming.map((item) => (
            <li key={item.id}>
              <Link
                href={`/assignments?course_id=${item.course_id}`}
                className="flex items-center gap-4 py-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <span className="w-16 shrink-0 text-[13px] font-bold text-primary-strong">
                  {codes.get(item.course_id) ?? t("Course")}
                </span>
                <span className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground">{item.title}</span>
                <span className="shrink-0 text-[13px] text-muted-foreground">
                  {dueFormat.format(new Date(item.due_at ?? ""))}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
