import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render } from "@testing-library/react";
import { createElement } from "react";

import { useActivityStream } from "@/components/aaos/activity-stream";

type Listener = (event: MessageEvent) => void;

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  readonly url: string;
  readonly listeners = new Map<string, Listener[]>();
  close = vi.fn();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(name: string, listener: Listener): void {
    this.listeners.set(name, [...(this.listeners.get(name) ?? []), listener]);
  }

  emit(name: string, event: MessageEvent): void {
    for (const listener of this.listeners.get(name) ?? []) listener(event);
  }
}

function Harness(props: {
  cursor: string | null;
  onEvent: (event: unknown, cursor: string) => void;
  onConnectionState: (state: "connected" | "reconnecting" | "unavailable") => void;
  onRecovery?: () => Promise<void>;
}) {
  useActivityStream(props);
  return null;
}

afterEach(() => {
  FakeEventSource.instances = [];
  vi.unstubAllGlobals();
});

describe("activity EventSource boundary", () => {
  it("opens one same-origin stream, advances opaque cursor, suppresses duplicates, and closes", () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const onEvent = vi.fn();
    const onConnectionState = vi.fn();

    const view = render(
      createElement(Harness, {
        cursor: null,
        onEvent,
        onConnectionState,
      }),
    );
    const source = FakeEventSource.instances[0];
    expect(source?.url).toBe("/api/v1/activity/stream");

    act(() => {
      source?.emit("open", new MessageEvent("open"));
      source?.emit(
        "activity",
        new MessageEvent("activity", {
          data: JSON.stringify({
            schema_version: "phase7-activity-v1",
            activity_id: "event-1",
            occurred_at: "2026-09-18T00:00:00Z",
            category: "TASK",
            event_code: "TASK_CREATED",
            status: "PENDING",
            resource_type: "TASK",
            resource_id: "task-1",
            title: "Task created",
            detail: null,
            context: null,
          }),
          lastEventId: "cursor-1",
        }),
      );
      source?.emit(
        "activity",
        new MessageEvent("activity", {
          data: JSON.stringify({
            schema_version: "phase7-activity-v1",
            activity_id: "event-1",
            occurred_at: "2026-09-18T00:00:00Z",
            category: "TASK",
            event_code: "TASK_CREATED",
            status: "PENDING",
            resource_type: "TASK",
            resource_id: "task-1",
            title: "Task created",
            detail: null,
            context: null,
          }),
          lastEventId: "cursor-1",
        }),
      );
    });

    expect(onConnectionState).toHaveBeenCalledWith("connected");
    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(onEvent).toHaveBeenCalledWith(
      expect.objectContaining({
        activity_id: "event-1",
        event_code: "TASK_CREATED",
      }),
      "cursor-1",
    );

    view.unmount();
    expect(source?.close).toHaveBeenCalledOnce();
  });

  it("refetches through recovery and reopens from the refreshed cursor", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const onRecovery = vi.fn(async () => undefined);
    const onEvent = vi.fn();
    const onConnectionState = vi.fn();

    render(
      createElement(Harness, {
        cursor: "cursor-1",
        onEvent,
        onConnectionState,
        onRecovery,
      }),
    );
    const source = FakeEventSource.instances[0];
    await act(async () => {
      source?.emit(
        "recovery_required",
        new MessageEvent("recovery_required", {
          data: JSON.stringify({ reason: "REPLAY_REQUIRED" }),
        }),
      );
      await Promise.resolve();
    });

    expect(onRecovery).toHaveBeenCalledOnce();
    expect(onConnectionState).toHaveBeenCalledWith("reconnecting");
    expect(source?.close).toHaveBeenCalledOnce();
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(FakeEventSource.instances[1]?.url).toContain(
      "/api/v1/activity/stream?after=cursor-1",
    );
  });
});
