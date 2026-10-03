"use client";

import Link from "next/link";
import { ListTodo } from "lucide-react";
import { useQuery } from "@tanstack/react-query";

import { EmptyState } from "@/components/aaos/empty-state";
import { PageHeader } from "@/components/aaos/page-header";
import { SectionCard } from "@/components/aaos/section-card";
import { Skeleton } from "@/components/ui/skeleton";
import type { StudyTask } from "@/lib/api/tasks";
import { studyTaskListQueryOptions, taskListQueryOptions } from "@/lib/query/tasks";

function statusLabel(status: string): string {
  return status.replaceAll("_", " ");
}

const PRIORITY_LABELS: Record<StudyTask["priority"], string> = {
  LOW: "Low priority",
  NORMAL: "Normal priority",
  HIGH: "High priority",
};

/** Plain words for a task's state; "OPEN" read like a button. */
export function studyTaskStatusLabel(status: StudyTask["status"]): string {
  return status === "COMPLETED" ? "Done" : "To do";
}

function StudyTaskRow({ task }: { task: StudyTask }) {
  const done = task.status === "COMPLETED";
  return (
    <li
      id={`study-task-${task.id}`}
      className="scroll-mt-24 rounded-lg border border-border px-4 py-3 target:border-primary target:ring-2 target:ring-ring/30"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className={`font-medium text-foreground ${done ? "text-muted-foreground line-through" : ""}`}>
          {task.title}
        </h3>
        <span
          className={`rounded-md px-2 py-0.5 text-xs font-semibold ${
            done ? "bg-muted text-muted-foreground" : "bg-primary-soft text-primary-strong"
          }`}
        >
          {studyTaskStatusLabel(task.status)}
        </span>
      </div>
      <p className="mt-1 text-sm text-muted-foreground">
        {PRIORITY_LABELS[task.priority]}
        {task.course_id !== null && task.course_title ? (
          <>
            {" · "}
            <Link
              href={`/courses/${task.course_id}`}
              className="font-medium text-primary-strong underline-offset-4 hover:underline"
            >
              {task.course_title}
            </Link>
          </>
        ) : null}
        {task.assignment_title ? ` · ${task.assignment_title}` : null}
      </p>
    </li>
  );
}

export function TaskListView() {
  const studyQuery = useQuery(studyTaskListQueryOptions());
  const query = useQuery(taskListQueryOptions());

  const studyTasks = Array.isArray(studyQuery.data?.study_tasks) ? studyQuery.data.study_tasks : [];
  const actions = Array.isArray(query.data?.tasks) ? query.data.tasks : [];
  const pending = studyQuery.isPending || query.isPending;
  const nothing = !pending && !studyQuery.isError && !query.isError
    && studyTasks.length === 0 && actions.length === 0;

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6">
      <PageHeader
        title="Tasks"
        description="Your study tasks, and Calendar actions waiting for or done after your approval."
      />
      {pending ? (
        <SectionCard title="Tasks">
          <div aria-label="Loading tasks" className="space-y-3">
            {Array.from({ length: 3 }, (_, index) => (
              <Skeleton key={index} className="h-24 w-full rounded-lg" />
            ))}
          </div>
        </SectionCard>
      ) : nothing ? (
        <SectionCard title="Tasks">
          <EmptyState
            icon={ListTodo}
            title="No tasks found."
            description="Ask AAOS to add one, for example: add a study task: review chapter 2 for AI445."
            action={{ href: "/ask", label: "Open Ask AAOS" }}
          />
        </SectionCard>
      ) : (
        <>
          {studyQuery.isError ? (
            <SectionCard title="Study tasks">
              <p className="text-sm text-muted-foreground">Study tasks are temporarily unavailable.</p>
            </SectionCard>
          ) : studyTasks.length > 0 ? (
            <SectionCard title="Study tasks">
              <ul aria-label="Study tasks" className="space-y-3">
                {studyTasks.map((task) => (
                  <StudyTaskRow key={task.id} task={task} />
                ))}
              </ul>
            </SectionCard>
          ) : null}
          {query.isError ? (
            <SectionCard title="Calendar actions">
              <p className="text-sm text-muted-foreground">
                Task data is temporarily unavailable.
              </p>
            </SectionCard>
          ) : actions.length > 0 ? (
            <SectionCard title="Calendar actions">
              <ul aria-label="Tasks" className="space-y-3">
                {actions.map((task) => (
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
            </SectionCard>
          ) : null}
        </>
      )}
    </div>
  );
}
