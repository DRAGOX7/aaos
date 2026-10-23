"use client";

import type { components } from "@/types/api.generated";
import {
  bucketAssignment,
  type AssignmentDueBucket,
} from "@/lib/assignments/due-buckets";
import { courseDisplayName } from "@/lib/courses/display";
import { useI18n, type Translator, type MessageKey } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const DUE_LOOK: Record<AssignmentDueBucket, { label: MessageKey; pill: string }> = {
  OVERDUE: { label: "Overdue", pill: "bg-danger-soft text-danger-strong" },
  DUE_SOON: { label: "Due soon", pill: "bg-warning-soft text-warning-strong" },
  THIS_WEEK: { label: "This week", pill: "bg-primary-soft text-primary-strong" },
  LATER: { label: "Later", pill: "bg-muted text-muted-foreground" },
  NO_DUE_DATE: { label: "No due date", pill: "bg-muted text-muted-foreground" },
};

type AssignmentSummary =
  components["schemas"]["AssignmentSummary"];

export type AssignmentCourseContext =
  | Readonly<{
      kind: "AVAILABLE";
      title: string;
      code: string | null;
    }>
  | Readonly<{
      kind: "UNAVAILABLE";
    }>;

type AssignmentRowProps = Readonly<{
  assignment: AssignmentSummary;
  courseContext: AssignmentCourseContext;
  now: Date;
}>;

function formatDueDate(dueAt: string | null, { t, dateLocale }: Translator): string {
  if (dueAt === null) {
    return t("No due date");
  }

  return new Intl.DateTimeFormat(dateLocale("en-US"), {
    dateStyle: "medium",
    timeZone: "UTC",
  }).format(new Date(dueAt));
}

export function AssignmentRow({
  assignment,
  courseContext,
  now,
}: AssignmentRowProps) {
  const i18n = useI18n();
  const { t } = i18n;
  const dueBucket: AssignmentDueBucket = bucketAssignment(
    assignment.due_at,
    now,
  );

  return (
    <li className="rounded-xl border border-border bg-card p-4">
      <article>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-[15px] font-semibold text-foreground">
              {assignment.title}
            </h2>

            {courseContext.kind === "AVAILABLE" ? (
              <p className="mt-1 text-[13px] text-muted-foreground">
                {(() => {
                  const { code, name } = courseDisplayName(courseContext);
                  return code ? `${code} · ${name}` : name;
                })()}
              </p>
            ) : (
              <p className="mt-1 text-xs text-muted-foreground">
                {t("Course unavailable")}
              </p>
            )}
          </div>

          <span
            className={cn("rounded-full px-2.5 py-1 text-xs font-semibold", DUE_LOOK[dueBucket].pill)}
          >
            {t(DUE_LOOK[dueBucket].label)}
          </span>
        </div>

        {assignment.description ? (
          <p className="mt-3 text-sm text-muted-foreground">
            {assignment.description}
          </p>
        ) : null}

        <p className="mt-3 text-xs text-muted-foreground">
          {t("Due: {date}", { date: formatDueDate(assignment.due_at, i18n) })}
        </p>
      </article>
    </li>
  );
}
