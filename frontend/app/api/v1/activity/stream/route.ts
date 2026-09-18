const DEFAULT_BACKEND_ORIGIN = "http://127.0.0.1:8000";
const STREAM_PATH = "/api/v1/activity/stream";
const PROXY_PRELUDE = new TextEncoder().encode(": proxy-connected\n\n");

export const runtime = "nodejs";

function upstreamUrl(request: Request): string {
  const backendOrigin =
    process.env.AAOS_BACKEND_ORIGIN ?? DEFAULT_BACKEND_ORIGIN;
  const requestUrl = new URL(request.url);
  return `${backendOrigin.replace(/\/+$/, "")}${STREAM_PATH}${requestUrl.search}`;
}

async function forwardUpstream(
  request: Request,
  writer: WritableStreamDefaultWriter<Uint8Array>,
): Promise<void> {
  try {
    await writer.write(PROXY_PRELUDE);
    const upstream = await fetch(upstreamUrl(request), {
      method: "GET",
      headers: {
        accept: request.headers.get("accept") ?? "text/event-stream",
        ...(request.headers.get("last-event-id")
          ? { "last-event-id": request.headers.get("last-event-id")! }
          : {}),
      },
      signal: request.signal,
      cache: "no-store",
    });

    if (upstream.body === null) {
      await writer.close();
      return;
    }

    const reader = upstream.body.getReader();
    try {
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        await writer.write(chunk.value);
      }
    } finally {
      reader.releaseLock();
    }
    await writer.close();
  } catch {
    try {
      await writer.abort();
    } catch {
      // Client disconnects can close the writer before the upstream settles.
    }
  }
}

export function GET(request: Request): Response {
  const stream = new TransformStream<Uint8Array, Uint8Array>();
  void forwardUpstream(request, stream.writable.getWriter());

  return new Response(stream.readable, {
    status: 200,
    headers: {
      "cache-control": "no-cache, no-store",
      "content-type": "text/event-stream; charset=utf-8",
      "x-accel-buffering": "no",
    },
  });
}
