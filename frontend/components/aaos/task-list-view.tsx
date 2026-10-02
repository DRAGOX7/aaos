"use client";

import { ListTodo } from "lucide-react";
import { useQuery } from "@tanstack/react-query";

import { EmptyState } from "@/components/aaos/empty-state";
import { PageHeader } from "@/components/aaos/page-header";
import { SectionCard } from "@/components/aaos/section-card";
import { Skeleton } from "@/components/ui/skeleton";
import { taskListQueryOptions } from "@/lib/query/tasks";

function statusLabel(status: string): string {
  return status.replaceAll("_", " ");
}

export function TaskListView() {
  const query = useQuery(taskListQueryOptions());

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6">
      <PageHeader
        title="Tasks"
        description="Review trusted academic actions and their approval state."
      />
      <SectionCard title="Tasks">
        {query.isPending ? (
          <div aria-label="Loading tasks" className="space-y-3">
            {Array.from({ length: 3 }, (_, index) => (
              <Skeleton key={index} className="h-24 w-full rounded-lg" />
            ))}
          </div>
        ) : query.isError ? (
          <p className="text-sm text-muted-foreground">
            Task data is temporarily unavailable.
          </p>
        ) : query.data?.tasks.length === 0 ? (
          <EmptyState
            icon={ListTodo}
            title="No tasks found."
            description="Ask AAOS to create a study task, for example: remind me to review the AI445 notes."
            action={{ href: "/ask", label: "Open Ask AAOS" }}
          />
        ) : (
          <ul aria-label="Tasks" className="space-y-3">
            {query.data?.tasks.map((task) => (
              <li
                key={task.task_id}
                className="rounded-lg border border-border px-4 py-3"
              >
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h3 className="font-medium text-foreground">{task.action.summary}</h3>
                  <span className="text-xs uppercase tracking-wide text-muted-foreground">
                    {statusLabel(task.status)}
                  </span>
                </div>
                <p className="mt-1 text-sm text-muted-foreground">
                  {task.action.integration_label}
                </p>
              </li>
            ))}
          </ul>
        )}
      </SectionCard>
    </div>
  );
}
