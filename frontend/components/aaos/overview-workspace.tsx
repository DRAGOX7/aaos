"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";

import { useI18n, type MessageKey, type Translator } from "@/lib/i18n";
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

const HEALTH_LABEL: Record<string, MessageKey> = {
  HEALTHY: "Healthy",
  DEGRADED: "Degraded",
  NOT_CONFIGURED: "Not configured",
};

type QueryLike = Readonly<{ isPending: boolean; isError: boolean }>;

function valueOf(query: QueryLike, value: () => ReactNode, { t }: Translator): ReactNode {
  if (query.isPending) return <span className="text-muted-foreground">…</span>;
  if (query.isError) return <span className="font-medium text-muted-foreground">{t("Unavailable")}</span>;
  return value();
}

function Row({ label, href, children }: Readonly<{ label: string; href: string; children: ReactNode }>) {
  return (
    <li>
      <Link
        href={href}
        className="flex min-h-14 items-center justify-between gap-4 py-3 text-sm hover:text-primary-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="text-muted-foreground">{label}</span>
        <span className="shrink-0 rounded-md bg-muted px-2.5 py-1 text-sm font-semibold text-foreground">{children}</span>
      </Link>
    </li>
  );
}

export function OverviewWorkspace() {
  const i18n = useI18n();
  const { t } = i18n;
  const approvals = useQuery(approvalListQueryOptions());
  const tasks = useQuery(taskListQueryOptions());
  const health = useQuery(systemHealthQueryOptions());

  return (
    <section
      aria-labelledby="overview-workspace-title"
      className="flex min-w-0 flex-col gap-1 self-start rounded-2xl border border-border bg-card px-6 py-5"
    >
      <h2 id="overview-workspace-title" className="mb-1 text-base font-bold text-foreground">
        {t("Workspace")}
      </h2>
      <ul className="flex flex-col divide-y divide-border">
        <Row label={t("Pending approvals")} href="/approvals">
          {valueOf(approvals, () =>
            (approvals.data?.approvals ?? []).filter((item) => item.status === "PENDING").length, i18n)}
        </Row>
        <Row label={t("Active tasks")} href="/tasks">
          {valueOf(tasks, () =>
            (tasks.data?.tasks ?? []).filter((item) => !TERMINAL_TASK_STATES.has(item.status)).length, i18n)}
        </Row>
        <Row label={t("System health")} href="/system-health">
          {valueOf(health, () => {
            const overall = health.data?.overall;
            return overall ? t(HEALTH_LABEL[overall] ?? "Needs attention") : t("Unavailable");
          }, i18n)}
        </Row>
      </ul>
    </section>
  );
}
