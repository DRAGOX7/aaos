// Same-origin proxy for the streaming Ask route. The generic /api rewrite
// buffers responses, which would hold every stage event until the answer is
// complete, so this handler forwards the upstream SSE body chunk by chunk, as
// the Activity stream proxy does.

const DEFAULT_BACKEND_ORIGIN = "http://127.0.0.1:8000";
const STREAM_PATH = "/api/v1/ask/stream";
const PROXY_PRELUDE = new TextEncoder().encode(
  `: proxy-connected${" ".repeat(2048)}\n\n`,
);

export const runtime = "nodejs";

function upstreamUrl(): string {
  const backendOrigin =
    process.env.AAOS_BACKEND_ORIGIN ?? DEFAULT_BACKEND_ORIGIN;
  return `${backendOrigin.replace(/\/+$/, "")}${STREAM_PATH}`;
}

function isEventStream(response: Response): boolean {
  return response.headers
    .get("content-type")
    ?.toLowerCase()
    .startsWith("text/event-stream") ?? false;
}

export async function POST(request: Request): Promise<Response> {
  // Closing the page aborts request.signal, which closes the upstream
  // connection; the backend then cancels the running Ask request.
  const upstream = await fetch(upstreamUrl(), {
    method: "POST",
    headers: {
      accept: "text/event-stream",
      "content-type": request.headers.get("content-type") ?? "application/json",
    },
    body: await request.text(),
    signal: request.signal,
    cache: "no-store",
  });

  // Validation and other errors arrive as ordinary JSON responses.
  if (!isEventStream(upstream)) {
    return upstream;
  }

  const stream = upstream.body?.pipeThrough(new TransformStream<Uint8Array, Uint8Array>({
    start(controller) { controller.enqueue(PROXY_PRELUDE); },
    transform(chunk, controller) { controller.enqueue(chunk); },
  })) ?? null;

  return new Response(stream, {
    status: 200,
    headers: {
      "cache-control": "no-cache, no-store",
      "content-type": "text/event-stream; charset=utf-8",
      "x-accel-buffering": "no",
    },
  });
}
