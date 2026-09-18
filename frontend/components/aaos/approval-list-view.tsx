"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { PageHeader } from "@/components/aaos/page-header";
import { SectionCard } from "@/components/aaos/section-card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { approveApproval, rejectApproval } from "@/lib/api/tasks";
import { approvalListQueryOptions, approvalKeys } from "@/lib/query/tasks";

type Decision = "approve" | "reject";

function statusLabel(status: string): string {
  return status.replaceAll("_", " ");
}

export function ApprovalListView() {
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
        title="Approvals"
        description="Review pending actions before trusted execution."
      />
      <SectionCard title="Approvals">
        {query.isPending ? (
          <div aria-label="Loading approvals" className="space-y-3">
            {Array.from({ length: 3 }, (_, index) => (
              <Skeleton key={index} className="h-28 w-full rounded-lg" />
            ))}
          </div>
        ) : query.isError ? (
          <p className="text-sm text-muted-foreground">
            Approval data is temporarily unavailable.
          </p>
        ) : query.data?.approvals.length === 0 ? (
          <p className="text-sm text-muted-foreground">No approvals found.</p>
        ) : (
          <ul aria-label="Approvals" className="space-y-3">
            {query.data?.approvals.map((approval) => (
              <li
                key={approval.approval_id}
                className="rounded-lg border border-border px-4 py-3"
              >
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h3 className="font-medium text-foreground">{approval.action.summary}</h3>
                  <span className="text-xs uppercase tracking-wide text-muted-foreground">
                    {statusLabel(approval.status)}
                  </span>
                </div>
                <p className="mt-1 text-sm text-muted-foreground">
                  {approval.action.integration_label}
                </p>
                {approval.operation_state ? (
                  <p className="mt-1 text-xs uppercase tracking-wide text-muted-foreground">
                    Operation: {statusLabel(approval.operation_state)}
                  </p>
                ) : null}
                {approval.status === "PENDING" ? (
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Button
                      type="button"
                      disabled={mutation.isPending}
                      onClick={() => mutation.mutate({ approvalId: approval.approval_id, decision: "approve" })}
                    >
                      Approve
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      disabled={mutation.isPending}
                      onClick={() => mutation.mutate({ approvalId: approval.approval_id, decision: "reject" })}
                    >
                      Reject
                    </Button>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        {mutation.isError ? (
          <p className="mt-4 text-sm text-muted-foreground">
            Decision status is uncertain. Refresh to verify the approval state.
          </p>
        ) : null}
      </SectionCard>
    </div>
  );
}
