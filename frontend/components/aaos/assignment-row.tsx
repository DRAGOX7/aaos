import type { components } from "@/types/api.generated";
import {
  bucketAssignment,
  type AssignmentDueBucket,
} from "@/lib/assignments/due-buckets";

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

function formatDueDate(dueAt: string | null): string {
  if (dueAt === null) {
    return "No due date";
  }

  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeZone: "UTC",
  }).format(new Date(dueAt));
}

export function AssignmentRow({
  assignment,
  courseContext,
  now,
}: AssignmentRowProps) {
  const dueBucket: AssignmentDueBucket = bucketAssignment(
    assignment.due_at,
    now,
  );

  return (
    <li className="rounded-lg border border-border bg-background p-4">
      <article>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-foreground">
              {assignment.title}
            </h2>

            {courseContext.kind === "AVAILABLE" ? (
              <p className="mt-1 text-xs text-muted-foreground">
                {courseContext.title}
                {courseContext.code ? ` · ${courseContext.code}` : ""}
              </p>
            ) : (
              <p className="mt-1 text-xs text-muted-foreground">
                Course unavailable
              </p>
            )}
          </div>

          <span className="rounded-full bg-muted px-2.5 py-1 text-xs font-medium text-muted-foreground">
            {dueBucket}
          </span>
        </div>

        {assignment.description ? (
          <p className="mt-3 text-sm text-muted-foreground">
            {assignment.description}
          </p>
        ) : null}

        <p className="mt-3 text-xs text-muted-foreground">
          Due: {formatDueDate(assignment.due_at)}
        </p>
      </article>
    </li>
  );
}
