"use client";

import { CheckCircle2 } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { EmptyState } from "@/components/aaos/empty-state";
import { CalendarActionPreview } from "@/components/aaos/calendar-action-preview";
import { PageHeader } from "@/components/aaos/page-header";
import { SectionCard } from "@/components/aaos/section-card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { approveApproval, rejectApproval } from "@/lib/api/tasks";
import { useI18n } from "@/lib/i18n";
import { approvalListQueryOptions, approvalKeys } from "@/lib/query/tasks";

type Decision = "approve" | "reject";

function formatDeadline(value: string, locale: string | undefined): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

export function ApprovalListView() {
  const { t, status, dateLocale } = useI18n();
  const queryClient = useQueryClient();
  const query = useQuery(approvalListQueryOptions());
  const mutation = useMutation({
    mutationFn: ({ approvalId, decision }: { approvalId: string; decision: Decision }) =>
      decision === "approve" ? approveApproval(approvalId) : rejectApproval(approvalId),
    retry: false,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: approvalKeys.list });
    },
  });

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6">
      <PageHeader
        title={t("Approvals")}
        description={t("Review pending actions before trusted execution.")}
      />
      <SectionCard title={t("Approvals")}>
        {query.isPending ? (
          <div aria-label={t("Loading approvals")} className="space-y-3">
            {Array.from({ length: 3 }, (_, index) => (
              <Skeleton key={index} className="h-28 w-full rounded-lg" />
            ))}
          </div>
        ) : query.isError ? (
          <p className="text-sm text-muted-foreground">
            {t("Approval data is temporarily unavailable.")}
          </p>
        ) : query.data?.approvals.length === 0 ? (
          <EmptyState
            icon={CheckCircle2}
            title={t("No approvals found.")}
            description={t("When AAOS proposes a Calendar event, it waits here until you approve or reject it.")}
            action={{ href: "/ask", label: t("Open Ask AAOS") }}
          />
        ) : (
          <ul aria-label={t("Approvals")} className="space-y-3">
            {query.data?.approvals.map((approval) => (
              <li
                key={approval.approval_id}
                className="rounded-lg border border-border px-4 py-3"
              >
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h3 className="font-medium text-foreground">{approval.action.summary}</h3>
                  <span className="text-xs uppercase tracking-wide text-muted-foreground">
                    {status(approval.status)}
                  </span>
                </div>
                <p className="mt-1 text-sm text-muted-foreground">
                  {approval.action.integration_label}
                </p>
                <CalendarActionPreview action={approval.action} />
                {approval.operation_state ? (
                  <p className="mt-1 text-xs uppercase tracking-wide text-muted-foreground">
                    {t("Operation: {state}", { state: status(approval.operation_state) })}
                  </p>
                ) : null}
                {approval.status === "PENDING" ? (
                  <p className="mt-1 text-sm text-muted-foreground">
                    {t("Approve by {deadline}. Nothing is written to Calendar unless you approve.", {
                      deadline: formatDeadline(approval.approval_expires_at, dateLocale(undefined)),
                    })}
                  </p>
                ) : null}
                {approval.status === "PENDING" ? (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {approval.can_approve ? <Button
                      type="button"
                      disabled={mutation.isPending}
                      onClick={() => mutation.mutate({ approvalId: approval.approval_id, decision: "approve" })}
                    >
                      {t("Approve")}
                    </Button> : null}
                    {approval.can_reject ? <Button
                      type="button"
                      variant="outline"
                      disabled={mutation.isPending}
                      onClick={() => mutation.mutate({ approvalId: approval.approval_id, decision: "reject" })}
                    >
                      {t("Reject")}
                    </Button> : null}
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        {mutation.isError ? (
          <p className="mt-4 text-sm text-muted-foreground">
            {t("Decision status is uncertain. Refresh to verify the approval state.")}
          </p>
        ) : null}
      </SectionCard>
    </div>
  );
}
