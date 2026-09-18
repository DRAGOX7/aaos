const DEFAULT_BACKEND_ORIGIN = "http://127.0.0.1:8000";
const STREAM_PATH = "/api/v1/activity/stream";
const PROXY_PRELUDE = new TextEncoder().encode(
  `: proxy-connected${" ".repeat(2048)}\n\n`,
);
const UPSTREAM_PREFLIGHT_TIMEOUT_MS = 100;

export const runtime = "nodejs";

function upstreamUrl(request: Request): string {
  const backendOrigin =
    process.env.AAOS_BACKEND_ORIGIN ?? DEFAULT_BACKEND_ORIGIN;
  const requestUrl = new URL(request.url);
  return `${backendOrigin.replace(/\/+$/, "")}${STREAM_PATH}${requestUrl.search}`;
}

function fetchUpstream(request: Request): Promise<Response> {
  return fetch(upstreamUrl(request), {
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
}

function isEventStream(response: Response): boolean {
  return response.headers
    .get("content-type")
    ?.toLowerCase()
    .startsWith("text/event-stream") ?? false;
}

async function forwardUpstream(
  upstreamPromise: Promise<Response>,
  controller: ReadableStreamDefaultController<Uint8Array>,
): Promise<void> {
  try {
    controller.enqueue(PROXY_PRELUDE);
    const upstream = await upstreamPromise;

    if (upstream.body === null) {
      controller.close();
      return;
    }

    const reader = upstream.body.getReader();
    try {
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        controller.enqueue(chunk.value);
      }
    } finally {
      reader.releaseLock();
    }
    controller.close();
  } catch {
    try {
      controller.error();
    } catch {
      // Client disconnects can close the stream before the upstream settles.
    }
  }
}

async function resolveFastUpstream(
  upstreamPromise: Promise<Response>,
): Promise<Response | null> {
  let timeout: ReturnType<typeof setTimeout> | undefined;
  try {
    return await new Promise<Response | null>((resolve, reject) => {
      timeout = setTimeout(
        () => resolve(null),
        UPSTREAM_PREFLIGHT_TIMEOUT_MS,
      );
      upstreamPromise.then(resolve, reject);
    });
  } finally {
    if (timeout !== undefined) clearTimeout(timeout);
  }
}

export async function GET(request: Request): Promise<Response> {
  const upstreamPromise = fetchUpstream(request);
  const fastUpstream = await resolveFastUpstream(upstreamPromise);
  if (fastUpstream !== null && !isEventStream(fastUpstream)) {
    return fastUpstream;
  }

  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      void forwardUpstream(
        fastUpstream === null ? upstreamPromise : Promise.resolve(fastUpstream),
        controller,
      );
    },
  });

  return new Response(stream, {
    status: 200,
    headers: {
      "cache-control": "no-cache, no-store",
      "content-type": "text/event-stream; charset=utf-8",
      "x-accel-buffering": "no",
    },
  });
}
