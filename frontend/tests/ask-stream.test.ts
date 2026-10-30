import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "../app/api/v1/ask/stream/route";
import {
  MalformedAskResponseError,
  streamAcademicAsk,
  type AskStage,
} from "@/lib/api/ask";
import { ApiError } from "@/lib/api/errors";

const encoder = new TextEncoder();

function result() {
  return {
    status: "ANSWERED",
    answer_text: "Verified answer.",
    context_sources: [],
    conversation_id: null,
    sources: [],
    step_count: 1,
    request_id: "50317f6c-a8c6-4553-b2c9-1bb662e5fdfa",
  };
}

// Deliver the SSE text in awkward chunks so frames span chunk boundaries.
function sseResponse(text: string, chunkSize = 7): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (let index = 0; index < text.length; index += chunkSize) {
        controller.enqueue(encoder.encode(text.slice(index, index + chunkSize)));
      }
      controller.close();
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "content-type": "text/event-stream; charset=utf-8" },
  });
}

function frame(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

const REQUEST = { question: "Explain backpropagation" };

describe("streamed Ask client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("reports each stage and resolves with the final verified result", async () => {
    const text = [
      ": proxy-connected   \n\n",
      frame("stage", { stage: "RECEIVED", elapsed_ms: 0 }),
      frame("stage", { stage: "SEARCHING", elapsed_ms: 12 }),
      ": keep-alive\n\n",
      frame("stage", { stage: "WRITING", elapsed_ms: 900 }),
      frame("stage", { stage: "VERIFYING", elapsed_ms: 15000 }),
      frame("result", result()),
    ].join("");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(sseResponse(text));
    const stages: AskStage[] = [];

    const response = await streamAcademicAsk(REQUEST, undefined, (stage) => stages.push(stage));

    expect(response.answer_text).toBe("Verified answer.");
    expect(stages).toEqual(["RECEIVED", "SEARCHING", "WRITING", "VERIFYING"]);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/ask/stream",
      expect.objectContaining({ method: "POST", body: JSON.stringify(REQUEST) }),
    );
  });

  it("decodes UTF-8 bytes and CRLF even when every byte arrives separately", async () => {
    const bytes = encoder.encode(frame("result", { ...result(), answer_text: "أهلاً 🌍" }).replaceAll("\n", "\r\n"));
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(new ReadableStream({
      start(controller) { for (const byte of bytes) controller.enqueue(Uint8Array.of(byte)); controller.close(); },
    }), { headers: { "content-type": "text/event-stream" } }));
    expect((await streamAcademicAsk(REQUEST)).answer_text).toBe("أهلاً 🌍");
  });

  it.each(["event: result\ndata: {", "event: result\ndata: {}\n\n"])("rejects incomplete or malformed frames: %s", async (text) => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(sseResponse(text));
    await expect(streamAcademicAsk(REQUEST)).rejects.toBeInstanceOf(MalformedAskResponseError);
  });

  it("cancels the response reader after the terminal event", async () => {
    const cancel = vi.fn();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(new ReadableStream({
      start(controller) { controller.enqueue(encoder.encode(frame("result", result()))); }, cancel,
    }), { headers: { "content-type": "text/event-stream" } }));
    await streamAcademicAsk(REQUEST);
    expect(cancel).toHaveBeenCalledOnce();
  });

  it("ignores unknown stages instead of showing them", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(sseResponse(
      frame("stage", { stage: "DROP_TABLES", elapsed_ms: 1 }) + frame("result", result()),
    ));
    const stages: AskStage[] = [];

    await streamAcademicAsk(REQUEST, undefined, (stage) => stages.push(stage));

    expect(stages).toEqual([]);
  });

  it("rejects a result that does not match the Ask contract", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(sseResponse(
      frame("result", { ...result(), status: "SUCCESS" }),
    ));

    await expect(streamAcademicAsk(REQUEST)).rejects.toBeInstanceOf(MalformedAskResponseError);
  });

  it("treats a stream that ends without a result as malformed, never as success", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(sseResponse(
      frame("stage", { stage: "WRITING", elapsed_ms: 1 }),
    ));

    await expect(streamAcademicAsk(REQUEST)).rejects.toBeInstanceOf(MalformedAskResponseError);
  });

  it("turns a backend error event into a safe API error", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(sseResponse(
      frame("error", { code: "ASK_FAILED", message: "AAOS could not complete this request." }),
    ));

    const failure = await streamAcademicAsk(REQUEST).catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(500);
  });

  it("surfaces a validation error returned before streaming starts", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(
      JSON.stringify({ error: { code: "INVALID_REQUEST", message: "x", request_id: "r" } }),
      { status: 422, headers: { "content-type": "application/json" } },
    ));

    const failure = await streamAcademicAsk(REQUEST).catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(422);
  });
});

describe("same-origin Ask SSE proxy", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.AAOS_BACKEND_ORIGIN;
  });

  it("forwards the question and streams the upstream body unchanged", async () => {
    process.env.AAOS_BACKEND_ORIGIN = "http://127.0.0.1:8765/";
    const upstreamText = frame("stage", { stage: "RECEIVED", elapsed_ms: 0 });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      expect(input).toBe("http://127.0.0.1:8765/api/v1/ask/stream");
      expect(init?.method).toBe("POST");
      expect(init?.body).toBe(JSON.stringify(REQUEST));
      return sseResponse(upstreamText, 1024);
    });
    vi.stubGlobal("fetch", fetchMock);

    const response = await POST(new Request("http://localhost/api/v1/ask/stream", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(REQUEST),
    }));

    expect(response.headers.get("content-type")).toContain("text/event-stream");
    expect(response.headers.get("x-accel-buffering")).toBe("no");
    const reader = response.body!.getReader();
    const prelude = new TextDecoder().decode((await reader.read()).value);
    expect(prelude.startsWith(": proxy-connected")).toBe(true);
    expect(new TextDecoder().decode((await reader.read()).value)).toBe(upstreamText);
  });

  it("cancels the upstream reader when the browser cancels its response", async () => {
    const cancel = vi.fn();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(new ReadableStream({
      start(controller) { controller.enqueue(encoder.encode(": keep-alive\n\n")); }, cancel,
    }), { headers: { "content-type": "text/event-stream" } })));
    const response = await POST(new Request("http://localhost/api/v1/ask/stream", { method: "POST", body: "{}" }));
    const reader = response.body!.getReader();
    await reader.read();
    await reader.read();
    await reader.cancel();
    await vi.waitFor(() => expect(cancel).toHaveBeenCalledOnce());
  });

  it("passes a non-stream error response through untouched", async () => {
    const upstream = new Response("{}", {
      status: 422,
      headers: { "content-type": "application/json" },
    });
    vi.stubGlobal("fetch", vi.fn(async () => upstream));

    const response = await POST(new Request("http://localhost/api/v1/ask/stream", {
      method: "POST",
      body: "{}",
    }));

    expect(response.status).toBe(422);
  });
});
