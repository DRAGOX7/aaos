"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";

import { systemHealthQueryOptions } from "@/lib/query/health";
import { approvalListQueryOptions, taskListQueryOptions } from "@/lib/query/tasks";

const TERMINAL_TASK_STATES = new Set([
  "CONFIRMED_SUCCESS",
  "CONFIRMED_FAILURE",
  "NOT_DISPATCHED",
  "REJECTED",
  "INVALIDATED",
  "EXPIRED",
  "REVOKED",
]);

const HEALTH_LABEL: Record<string, string> = {
  HEALTHY: "Healthy",
  DEGRADED: "Degraded",
  NOT_CONFIGURED: "Not configured",
};

type QueryLike = Readonly<{ isPending: boolean; isError: boolean }>;

function valueOf(query: QueryLike, value: () => ReactNode): ReactNode {
  if (query.isPending) return <span className="text-muted-foreground">…</span>;
  if (query.isError) return <span className="font-medium text-muted-foreground">Unavailable</span>;
  return value();
}

function Row({ label, href, children }: Readonly<{ label: string; href: string; children: ReactNode }>) {
  return (
    <li>
      <Link
        href={href}
        className="flex items-center justify-between gap-4 border-b border-border py-2.5 text-sm last:border-b-0 hover:text-primary-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="text-muted-foreground">{label}</span>
        <span className="font-bold text-foreground">{children}</span>
      </Link>
    </li>
  );
}

export function OverviewWorkspace() {
  const approvals = useQuery(approvalListQueryOptions());
  const tasks = useQuery(taskListQueryOptions());
  const health = useQuery(systemHealthQueryOptions());

  return (
    <section
      aria-labelledby="overview-workspace-title"
      className="flex min-w-0 flex-col gap-1 rounded-2xl border border-border bg-card px-6 py-5"
    >
      <h2 id="overview-workspace-title" className="mb-1 text-base font-bold text-foreground">
        Workspace
      </h2>
      <ul className="flex flex-col">
        <Row label="Pending approvals" href="/approvals">
          {valueOf(approvals, () =>
            (approvals.data?.approvals ?? []).filter((item) => item.status === "PENDING").length)}
        </Row>
        <Row label="Active tasks" href="/tasks">
          {valueOf(tasks, () =>
            (tasks.data?.tasks ?? []).filter((item) => !TERMINAL_TASK_STATES.has(item.status)).length)}
        </Row>
        <Row label="System health" href="/system-health">
          {valueOf(health, () => {
            const overall = health.data?.overall;
            return overall ? (HEALTH_LABEL[overall] ?? "Needs attention") : "Unavailable";
          })}
        </Row>
      </ul>
    </section>
  );
}
