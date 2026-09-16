import type { NextConfig } from "next";

const DEFAULT_BACKEND_ORIGIN = "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
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
