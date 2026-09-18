"use client";

import { useEffect, useRef, useState } from "react";

import type { components } from "@/types/api.generated";

export type ActivityEvent = components["schemas"]["ActivityEvent"];
export type ActivityConnectionState =
  | "connected"
  | "reconnecting"
  | "unavailable";

type ActivityStreamOptions = {
  cursor: string | null;
  onEvent: (event: ActivityEvent, cursor: string) => void;
  onConnectionState: (state: ActivityConnectionState) => void;
  onRecovery?: () => void | Promise<void>;
};

const ACTIVITY_EVENT_KEYS = new Set([
  "schema_version",
  "activity_id",
  "occurred_at",
  "category",
  "event_code",
  "status",
  "resource_type",
  "resource_id",
  "title",
  "detail",
  "context",
]);

function parseActivityEvent(data: string): ActivityEvent | null {
  try {
    const value: unknown = JSON.parse(data);
    if (typeof value !== "object" || value === null) return null;
    const record = value as Record<string, unknown>;
    if (
      Object.keys(record).some((key) => !ACTIVITY_EVENT_KEYS.has(key)) ||
      Object.keys(record).length !== ACTIVITY_EVENT_KEYS.size ||
      typeof record.activity_id !== "string" ||
      typeof record.event_code !== "string" ||
      typeof record.title !== "string"
    ) {
      return null;
    }
    return record as ActivityEvent;
  } catch {
    return null;
  }
}

export function useActivityStream({
  cursor,
  onEvent,
  onConnectionState,
  onRecovery,
}: ActivityStreamOptions): void {
  const cursorRef = useRef(cursor);
  const onEventRef = useRef(onEvent);
  const onConnectionStateRef = useRef(onConnectionState);
  const onRecoveryRef = useRef(onRecovery);
  const seenIdsRef = useRef(new Set<string>());
  const [reconnectVersion, setReconnectVersion] = useState(0);

  useEffect(() => {
    cursorRef.current = cursor;
  }, [cursor]);
  useEffect(() => {
    onEventRef.current = onEvent;
    onConnectionStateRef.current = onConnectionState;
    onRecoveryRef.current = onRecovery;
  }, [onEvent, onConnectionState, onRecovery]);

  useEffect(() => {
    let disposed = false;
    let closed = false;
    const query = cursorRef.current
      ? `?after=${encodeURIComponent(cursorRef.current)}`
      : "";
    const source = new EventSource(`/api/v1/activity/stream${query}`);
    const closeSource = () => {
      if (!closed) {
        closed = true;
        source.close();
      }
    };

    source.addEventListener("open", () =>
      onConnectionStateRef.current("connected"),
    );
    source.addEventListener("error", () => {
      onConnectionStateRef.current(
        source.readyState === EventSource.CLOSED ? "unavailable" : "reconnecting",
      );
    });
    source.addEventListener("activity", (rawEvent) => {
      const event = rawEvent as MessageEvent<string>;
      const parsed = parseActivityEvent(event.data);
      const eventCursor = event.lastEventId;
      if (!parsed || !eventCursor || seenIdsRef.current.has(parsed.activity_id)) return;
      seenIdsRef.current.add(parsed.activity_id);
      onEventRef.current(parsed, eventCursor);
    });
    source.addEventListener("recovery_required", () => {
      closeSource();
      onConnectionStateRef.current("reconnecting");
      void Promise.resolve(onRecoveryRef.current?.()).then(() => {
        if (!disposed) setReconnectVersion((value) => value + 1);
      });
    });

    return () => {
      disposed = true;
      closeSource();
    };
  }, [reconnectVersion]);
}

type ActivityStreamBoundaryProps = ActivityStreamOptions;

export function ActivityStreamBoundary(
  props: ActivityStreamBoundaryProps,
): null {
  useActivityStream(props);
  return null;
}
