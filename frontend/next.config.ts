import type { NextConfig } from "next";

const DEFAULT_BACKEND_ORIGIN = "http://127.0.0.1:8000";

// The backend bounds one Ask request at 300 seconds
// (MAX_ACADEMIC_REQUEST_DEADLINE_SECONDS); the rewrite proxy must not cut it
// off at Next's 30-second default, so allow the deadline plus a margin.
const BACKEND_PROXY_TIMEOUT_MS = 310_000;

const nextConfig: NextConfig = {
  experimental: {
    proxyTimeout: BACKEND_PROXY_TIMEOUT_MS,
  },
  async rewrites() {
    const backendOrigin =
      process.env.AAOS_BACKEND_ORIGIN ?? DEFAULT_BACKEND_ORIGIN;

    return [
      {
        source: "/api/:path*",
        destination: `${backendOrigin}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
