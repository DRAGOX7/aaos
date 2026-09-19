"use client";

import Link from "next/link";
import {
  ArrowUpRight,
  BookOpen,
  CheckCircle2,
  ClipboardList,
  ListTodo,
  type LucideIcon,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";

import { SectionCard } from "@/components/aaos/section-card";
import { Skeleton } from "@/components/ui/skeleton";
import { courseListQueryOptions } from "@/lib/query/courses";
import { assignmentsQueryOptions } from "@/lib/query/academic";
import {
  approvalListQueryOptions,
  taskListQueryOptions,
} from "@/lib/query/tasks";

type MetricTone = "blue" | "teal" | "purple" | "orange";

type MetricCardProps = Readonly<{
  label: string;
  value: string;
  detail: string;
  href: string;
  icon: LucideIcon;
  tone: MetricTone;
  loading?: boolean;
}>;

const toneClasses: Record<MetricTone, string> = {
  blue: "bg-primary-soft text-primary",
  teal: "bg-teal-soft text-teal",
  purple: "bg-purple-soft text-purple",
  orange: "bg-warning-soft text-warning",
};

function MetricCard({
  label,
  value,
  detail,
  href,
  icon: Icon,
  tone,
  loading = false,
}: MetricCardProps) {
  return (
    <SectionCard
      title={label}
      className="group overflow-hidden"
      action={
        <Link
          href={href}
          aria-label={`View ${label.toLowerCase()}`}
          className="rounded-full p-1.5 text-text-subtle transition-colors hover:bg-primary-soft hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <ArrowUpRight aria-hidden="true" className="size-4" strokeWidth={1.8} />
        </Link>
      }
    >
      <div className="flex items-end justify-between gap-4">
        <div className="min-w-0">
          {loading ? (
            <Skeleton className="h-10 w-16 rounded-lg" />
          ) : (
            <p className="text-4xl font-semibold tracking-tight text-primary-deep">
              {value}
            </p>
          )}
          <p className="mt-2 text-sm text-muted-foreground">{detail}</p>
        </div>
        <span className={`flex size-11 shrink-0 items-center justify-center rounded-2xl ${toneClasses[tone]}`}>
          <Icon aria-hidden="true" className="size-5" strokeWidth={1.8} />
        </span>
      </div>
    </SectionCard>
  );
}

function metricState<T extends object>(query: {
  data?: T;
  isError: boolean;
  isPending: boolean;
}): { value: string; detail: string; loading: boolean } {
  if (query.isPending) {
    return {
      value: "",
      detail: "Loading current data",
      loading: true,
    };
  }

  if (query.isError || !query.data) {
    return {
      value: "Unavailable",
      detail: "Current count is unavailable",
      loading: false,
    };
  }

  return { value: "0", detail: "No records reported", loading: false };
}

const terminalTaskStates = new Set([
  "CONFIRMED_SUCCESS",
  "CONFIRMED_FAILURE",
  "NOT_DISPATCHED",
  "REJECTED",
  "INVALIDATED",
  "EXPIRED",
  "REVOKED",
]);

export function OverviewMetricGrid() {
  const courses = useQuery(courseListQueryOptions());
  const assignments = useQuery(assignmentsQueryOptions());
  const tasks = useQuery(taskListQueryOptions());
  const approvals = useQuery(approvalListQueryOptions());

  const courseState = metricState(courses);
  const assignmentState = metricState(assignments);
  const taskState = metricState(tasks);
  const approvalState = metricState(approvals);

  const courseCount = courses.data?.courses.length ?? 0;
  const assignmentCount = assignments.data?.assignments.length ?? 0;
  const activeTaskCount = tasks.data?.tasks.filter(
    (task) => !terminalTaskStates.has(task.status),
  ).length ?? 0;
  const pendingApprovalCount = approvals.data?.approvals.filter(
    (approval) => approval.status === "PENDING",
  ).length ?? 0;

  return (
    <section aria-labelledby="academic-snapshot" className="space-y-4">
      <div>
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">
          Workspace pulse
        </p>
        <h2 id="academic-snapshot" className="mt-1 text-lg font-semibold tracking-tight text-primary-deep">
          Academic snapshot
        </h2>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="Active courses"
          value={courseState.loading || courseState.value === "Unavailable" ? courseState.value : String(courseCount)}
          detail={courseState.loading ? courseState.detail : courseState.value === "Unavailable" ? courseState.detail : "Courses in your workspace"}
          href="/courses"
          icon={BookOpen}
          tone="blue"
          loading={courseState.loading}
        />
        <MetricCard
          label="Open assignments"
          value={assignmentState.loading || assignmentState.value === "Unavailable" ? assignmentState.value : String(assignmentCount)}
          detail={assignmentState.loading ? assignmentState.detail : assignmentState.value === "Unavailable" ? assignmentState.detail : "Assignments to keep in view"}
          href="/assignments"
          icon={ClipboardList}
          tone="teal"
          loading={assignmentState.loading}
        />
        <MetricCard
          label="Pending approvals"
          value={approvalState.loading || approvalState.value === "Unavailable" ? approvalState.value : String(pendingApprovalCount)}
          detail={approvalState.loading ? approvalState.detail : approvalState.value === "Unavailable" ? approvalState.detail : "Human decisions waiting"}
          href="/approvals"
          icon={CheckCircle2}
          tone="purple"
          loading={approvalState.loading}
        />
        <MetricCard
          label="Active tasks"
          value={taskState.loading || taskState.value === "Unavailable" ? taskState.value : String(activeTaskCount)}
          detail={taskState.loading ? taskState.detail : taskState.value === "Unavailable" ? taskState.detail : "Trusted work in motion"}
          href="/tasks"
          icon={ListTodo}
          tone="orange"
          loading={taskState.loading}
        />
      </div>
    </section>
  );
}
