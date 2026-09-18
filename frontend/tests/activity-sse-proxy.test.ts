import { afterEach, describe, expect, it, vi } from "vitest";

import { GET } from "../app/api/v1/activity/stream/route";

afterEach(() => {
  vi.unstubAllGlobals();
  delete process.env.AAOS_BACKEND_ORIGIN;
});

describe("same-origin Activity SSE proxy", () => {
  it("forwards the upstream stream without consuming or rewriting its body", async () => {
    process.env.AAOS_BACKEND_ORIGIN = "http://127.0.0.1:8765";
    const upstreamBody = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("event: activity\n\n"));
        controller.close();
      },
    });
    const upstream = new Response(upstreamBody, {
      status: 200,
      headers: {
        "cache-control": "no-cache, no-store",
        "content-type": "text/event-stream; charset=utf-8",
        "x-accel-buffering": "no",
      },
    });
    const fetchMock = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        expect(input).toBe(
          "http://127.0.0.1:8765/api/v1/activity/stream?after=cursor-1",
        );
        expect(init?.method).toBe("GET");
        expect(init?.body).toBeUndefined();
        await new Promise((resolve) => setTimeout(resolve, 150));
        return upstream;
      },
    );
    vi.stubGlobal("fetch", fetchMock);

    const request = new Request(
      "http://localhost/api/v1/activity/stream?after=cursor-1",
      { method: "GET" },
    );
    const response = await GET(request);

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toContain("text/event-stream");
    expect(response.headers.get("cache-control")).toBe("no-cache, no-store");
    expect(response.headers.get("x-accel-buffering")).toBe("no");
    expect(response.body).not.toBeNull();

    const reader = response.body!.getReader();
    const prelude = await reader.read();
    const preludeText = new TextDecoder().decode(prelude.value);
    expect(preludeText.startsWith(": proxy-connected")).toBe(true);
    expect(preludeText.length).toBeGreaterThanOrEqual(1024);
    const firstChunk = await reader.read();
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(new TextDecoder().decode(firstChunk.value)).toBe(
      "event: activity\n\n",
    );
  });

  it("opens the client stream before a quiet upstream resolves", async () => {
    let resolveUpstream!: (response: Response) => void;
    const upstreamPromise = new Promise<Response>((resolve) => {
      resolveUpstream = resolve;
    });
    vi.stubGlobal("fetch", vi.fn(() => upstreamPromise));

    const responsePromise = GET(
      new Request("http://localhost/api/v1/activity/stream"),
    );
    const response = await Promise.race([
      responsePromise,
      new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error("proxy did not open promptly")), 100),
      ),
    ]);

    expect(response.status).toBe(200);
    const reader = response.body!.getReader();
    const prelude = await reader.read();
    const preludeText = new TextDecoder().decode(prelude.value);
    expect(preludeText.startsWith(": proxy-connected")).toBe(true);
    expect(preludeText.length).toBeGreaterThanOrEqual(1024);

    resolveUpstream(
      new Response(
        new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(new TextEncoder().encode("event: activity\n\n"));
            controller.close();
          },
        }),
        {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        },
      ),
    );
    const event = await reader.read();
    expect(new TextDecoder().decode(event.value)).toBe("event: activity\n\n");
  });

  it("preserves a fast upstream validation response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            error: {
              code: "INVALID_REQUEST",
              message: "The activity cursor is invalid.",
              request_id: "request-1",
            },
          }),
          {
            status: 422,
            headers: { "content-type": "application/json" },
          },
        ),
      ),
    );

    const response = await GET(
      new Request("http://localhost/api/v1/activity/stream?after=bad"),
    );

    expect(response.status).toBe(422);
    expect(await response.json()).toEqual({
      error: {
        code: "INVALID_REQUEST",
        message: "The activity cursor is invalid.",
        request_id: "request-1",
      },
    });
  });
});
