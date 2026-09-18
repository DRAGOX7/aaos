"use client";

import { useCallback, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import {
  ActivityStreamBoundary,
  type ActivityConnectionState,
  type ActivityEvent,
} from "@/components/aaos/activity-stream";
import { PageHeader } from "@/components/aaos/page-header";
import { SectionCard } from "@/components/aaos/section-card";
import { Skeleton } from "@/components/ui/skeleton";
import { activityHistoryQueryOptions } from "@/lib/query/activity";

function statusLabel(status: string | null): string {
  return (status ?? "PENDING").replaceAll("_", " ");
}

function eventHref(event: ActivityEvent): string | null {
  if (!event.resource_type) return null;
  return event.resource_type === "TASK" ? "/tasks" : "/approvals";
}

export function ActivityListView() {
  const query = useQuery(activityHistoryQueryOptions());
  const [liveEvents, setLiveEvents] = useState<ActivityEvent[]>([]);
  const [liveCursor, setLiveCursor] = useState<string | null>(null);
  const [connectionState, setConnectionState] =
    useState<ActivityConnectionState>("reconnecting");

  const onEvent = useCallback((event: ActivityEvent, eventCursor: string) => {
    setLiveEvents((current) =>
      current.some((item) => item.activity_id === event.activity_id)
        ? current
        : [...current, event],
    );
    setLiveCursor(eventCursor);
  }, []);

  const { refetch } = query;
  const onRecovery = useCallback(async () => {
    const refreshed = await refetch();
    if (refreshed.data) {
      setLiveEvents([]);
      setLiveCursor(refreshed.data.next_cursor ?? null);
    }
  }, [refetch]);

  const visibleEvents = useMemo(
    () => {
      const merged = new Map(
        (query.data?.events ?? []).map((event) => [event.activity_id, event]),
      );
      for (const event of liveEvents) merged.set(event.activity_id, event);
      return [...merged.values()];
    },
    [liveEvents, query.data?.events],
  );

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6">
      <PageHeader
        title="Activity"
        description="Durable operational history from trusted AAOS records."
      />
      <SectionCard title="Activity">
        {query.isPending ? (
          <div aria-label="Loading activity" className="space-y-3">
            {Array.from({ length: 3 }, (_, index) => (
              <Skeleton key={index} className="h-20 w-full rounded-lg" />
            ))}
          </div>
        ) : query.isError ? (
          <p className="text-sm text-muted-foreground">
            Activity history is temporarily unavailable.
          </p>
        ) : visibleEvents.length === 0 ? (
          <p className="text-sm text-muted-foreground">No activity recorded yet.</p>
        ) : (
          <ul aria-label="Activity events" className="space-y-3">
            {visibleEvents.map((event) => {
              const href = eventHref(event);
              const content = (
                <>
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <h3 className="font-medium text-foreground">{event.title}</h3>
                    <span className="text-xs uppercase tracking-wide text-muted-foreground">
                      {statusLabel(event.status)}
                    </span>
                  </div>
                  <time
                    className="mt-1 block text-xs text-muted-foreground"
                    dateTime={event.occurred_at}
                  >
                    {new Date(event.occurred_at).toLocaleString()}
                  </time>
                </>
              );
              return (
                <li key={event.activity_id} className="rounded-lg border border-border px-4 py-3">
                  {href ? (
                    <Link className="block rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" href={href}>
                      {content}
                    </Link>
                  ) : (
                    content
                  )}
                </li>
              );
            })}
          </ul>
        )}
        {!query.isPending && !query.isError && query.data ? (
          <ActivityStreamBoundary
            cursor={liveCursor ?? query.data.next_cursor ?? null}
            onEvent={onEvent}
            onConnectionState={setConnectionState}
            onRecovery={onRecovery}
          />
        ) : null}
        {visibleEvents.length > 0 && connectionState === "unavailable" ? (
          <p
            role="status"
            aria-label="Live activity status"
            className="mt-4 text-sm text-muted-foreground"
          >
            Live activity is unavailable; showing durable history.
          </p>
        ) : null}
      </SectionCard>
    </div>
  );
}
